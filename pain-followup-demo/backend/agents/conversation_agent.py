# backend/agents/conversation_agent.py
"""ConversationAgent —— 单轮对话工作流（Agent-centric迁移方案 §4.3 / §7.3）。

固定流程：ReplyUnderstandingAgent → merge slots → CoverageEvaluator →
TurnRouter → QuestionComposerAgent / FarewellComposerAgent。

最终风险评分由 PatientFollowupAgent 在整段患者对话结束后统一计算，
本子图不再在每一轮输出风险评分。

约定（§8.2 / §7）：
  - 风险计算、继续追问/结束/转人工是确定性决策，ReAct 不得自行决定；
  - 护士消息经 MessageOutbox 幂等入队，不直接广播；
  - 节点统一经 Runtime[AppContext] 获取依赖，不再调用全局 get_context()；
  - 被 PatientFollowupAgent 以子图方式逐轮调用，字段为 PatientState 子集。
"""
from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime

from agents.states import ConversationState
from agents.capability_agents.reply_understanding_agent import ReplyUnderstandingAgent
from agents.capability_agents.question_composer_agent import QuestionComposerAgent
from agents.capability_agents.farewell_composer_agent import FarewellComposerAgent
from domain.models.reply_understanding import ReplyUnderstanding
from domain.models.patient_report import CoverageReport
from infrastructure.runtime_context import AppContext


# ---- 节点 ----

async def understand_reply_node(state: ConversationState,
                                runtime: Runtime[AppContext]) -> dict:
    """理解患者最新回复（§8.5 表）。"""
    ctx = runtime.context
    msgs = state.get("messages") or []
    last_patient = next(
        (m for m in reversed(msgs) if m.get("role") == "patient"), None)
    if not last_patient:
        return {"reply_understanding": ReplyUnderstanding(raw_text="").model_dump(),
                "turn_decision": {}}
    patient_index = len(msgs) - 1 - next(
        i for i, message in enumerate(reversed(msgs))
        if message.get("role") == "patient"
    )
    last_nurse = next(
        (m.get("content", "") for m in reversed(msgs[:patient_index])
         if m.get("role") in ("nurse", "assistant")), "")
    agent = ReplyUnderstandingAgent(ctx.llm_gateway)
    understanding = await agent.understand(
        last_patient.get("content", ""),
        known_slots=state.get("slots") or {},
        current_question=last_nurse,
        # 当前评分必须来自本轮患者回复；历史记录不能直接填充今天的槽位。
        history_summary="",
        turn_no=state.get("turn_no", 1),
    )
    return {"reply_understanding": understanding.model_dump()}


def merge_slots_node(state: ConversationState,
                     runtime: Runtime[AppContext]) -> dict:
    """合并结构化理解到 slots + 计算 coverage（§6.2 / §8.1）。"""
    ctx = runtime.context
    slots: dict = dict(state.get("slots") or {})
    u = state.get("reply_understanding") or {}
    # 非空槽位才覆盖（None 不覆盖已有值）
    for k in ("pain_nrs", "sleep_quality", "medication_taken", "side_effects"):
        v = u.get(k)
        if v is not None:
            slots[k] = v
    coverage = ctx.coverage_evaluator.evaluate(slots)
    return {"slots": slots, "coverage": coverage.model_dump()}


def route_turn_node(state: ConversationState,
                    runtime: Runtime[AppContext]) -> dict:
    """单一流程决策点（§8.2 / §12）：唯一决定继续追问 / 转人工 / 结束。"""
    ctx = runtime.context
    u = ReplyUnderstanding(**(state.get("reply_understanding") or {}))
    cov = CoverageReport(**(state.get("coverage") or {}))
    turn_no = state.get("turn_no", 1)
    is_auto = state.get("input_source") == "simulator"
    cap = ctx.conversation_policy.auto_max_turns if is_auto else ctx.conversation_policy.max_turns
    # §11-12：TurnRouter 必须接收确定性风险结果，不能只凭回复理解/覆盖率决定结束
    decision = ctx.turn_router.route(
        u, cov, turn_no, max_turns=cap,
        risk_report=state.get("risk_result") or {},
    )
    decision_dict = decision.as_dict()
    if u.requires_immediate_action:
        ctx.event_outbox.immediate_intervention_alert(
            dispatch_id=state.get("dispatch_id", ""),
            episode_id=state.get("episode_id", ""),
            patient_id=state.get("patient_id", ""),
            patient_name=(state.get("patient_snapshot") or {}).get("name", ""),
            turn_no=turn_no,
            reason="患者表达绝望、极端痛苦或其他需要立即处理的情绪/症状",
            alert_key=f"immediate-alert:{state.get('episode_id', '')}:{turn_no}",
        )
    ctx.event_outbox.turn_decision(
        dispatch_id=state.get("dispatch_id", ""),
        episode_id=state.get("episode_id", ""),
        patient_id=state.get("patient_id", ""),
        patient_name=(state.get("patient_snapshot") or {}).get("name", ""),
        turn_no=turn_no,
        input_source=state.get("input_source", "simulator"),
        decision=decision_dict,
        coverage=state.get("coverage") or {},
        risk_result=state.get("risk_result") or {},
    )
    return {"turn_decision": decision_dict}


def _turn_router_condition(state: ConversationState):
    """子图出口：依据 turn_decision.action 结束本子图并返回出口标记（§8.2 表）。

    返回 compose_question / compose_farewell / END：
      - 追问 → compose_question（父图转回回复获取，开启下一轮）；
      - 完成/转人工 → compose_farewell；
      - 紧急转人工 → 不追加告别语，直接 END（父图转 persist → build_report 标记 human_handoff）。
    """
    action = (state.get("turn_decision") or {}).get("action", "clarify")
    if action in ("clarify", "continue_wait"):
        return "compose_question"
    if action in ("complete", "incomplete_handoff"):
        return "compose_farewell"
    return END  # emergency_handoff


async def compose_question_node(state: ConversationState,
                                runtime: Runtime[AppContext]) -> dict:
    """追问生成（§8.5 QuestionComposerAgent）。护士消息幂等入队。"""
    ctx = runtime.context
    td = state.get("turn_decision") or {}
    u = state.get("reply_understanding") or {}
    snap = state.get("patient_snapshot") or {}
    msgs = state.get("messages") or []
    last_patient = next(
        (m.get("content", "") for m in reversed(msgs) if m.get("role") == "patient"), "")
    agent = QuestionComposerAgent(ctx.llm_gateway)
    question = await agent.compose(
        last_patient_reply=last_patient,
        missing_slots=td.get("missing_slots") or [],
        ambiguity_type=u.get("ambiguity_type", "none"),
        retry_count=u.get("ambiguity_retry_count", 0),
        patient_name=snap.get("name", ""),
        turn_no=state.get("turn_no", 1),
    )
    turn_no = state.get("turn_no", 1)
    episode_id = state.get("episode_id", "")
    message_key = f"message:{episode_id}:{turn_no + 1}:nurse"
    ctx.message_outbox.enqueue(
        episode_id=episode_id, patient_id=state.get("patient_id", ""),
        patient_name=snap.get("name", ""), role="nurse", content=question,
        turn_no=turn_no + 1, message_key=message_key,
        dispatch_id=state.get("dispatch_id", ""),
    )
    return {"messages": [{"role": "nurse", "content": question,
                           "turn_no": turn_no + 1, "message_key": message_key}],
            "turn_no": turn_no + 1}


async def compose_farewell_node(state: ConversationState,
                                runtime: Runtime[AppContext]) -> dict:
    """告别语生成（§8.5 FarewellComposerAgent）。护士消息幂等入队。"""
    ctx = runtime.context
    td = state.get("turn_decision") or {}
    slots = state.get("slots") or {}
    risk = state.get("risk_result") or {}
    snap = state.get("patient_snapshot") or {}
    med = slots.get("medication_taken")
    med_str = {True: "按时", False: "未按时"}.get(med, "未提及") if med is not None else "未提及"
    agent = FarewellComposerAgent(ctx.llm_gateway)
    farewell = await agent.compose(
        patient_name=snap.get("name", ""), diagnosis=snap.get("diagnosis", ""),
        pain_type=snap.get("pain_type", ""), risk_level=risk.get("level", "medium"),
        nrs_score=slots.get("pain_nrs"), sleep_quality=slots.get("sleep_quality"),
        medication_status=med_str, side_effects=slots.get("side_effects"),
        incomplete=(td.get("action") == "incomplete_handoff"),
    )
    turn_no = state.get("turn_no", 1)
    episode_id = state.get("episode_id", "")
    message_key = f"message:{episode_id}:{turn_no + 1}:nurse:farewell"
    ctx.message_outbox.enqueue(
        episode_id=episode_id, patient_id=state.get("patient_id", ""),
        patient_name=snap.get("name", ""), role="nurse", content=farewell,
        turn_no=turn_no + 1, message_key=message_key,
        dispatch_id=state.get("dispatch_id", ""),
    )
    return {"messages": [{"role": "nurse", "content": farewell,
                           "turn_no": turn_no + 1, "message_key": message_key}]}


# ---- 图 ----

def build_conversation_graph():
    g = StateGraph(ConversationState, context_schema=AppContext)
    g.add_node("understand_reply", understand_reply_node)
    g.add_node("merge_slots", merge_slots_node)
    g.add_node("route_turn", route_turn_node)
    g.add_node("compose_question", compose_question_node)
    g.add_node("compose_farewell", compose_farewell_node)

    g.add_edge(START, "understand_reply")
    g.add_edge("understand_reply", "merge_slots")
    g.add_edge("merge_slots", "route_turn")
    g.add_conditional_edges("route_turn", _turn_router_condition,
                             {"compose_question": "compose_question",
                              "compose_farewell": "compose_farewell",
                              END: END})
    g.add_edge("compose_question", END)
    g.add_edge("compose_farewell", END)
    return g.compile()


conversation_workflow = build_conversation_graph()


async def run_conversation_turn(state: dict) -> dict:
    """单轮对话入口（供独立调用/测试）。显式注入 AppContext。"""
    from infrastructure.runtime_context import get_context
    return await conversation_workflow.ainvoke(state, context=get_context())
