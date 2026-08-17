# backend/agents/patient_followup_agent.py
"""PatientFollowupAgent —— 单患者随访工作流（Agent-centric迁移方案 §4.2 / §6 / §8）。

一张图统一 human/simulator：仅在 acquire_reply 节点处分叉，其余节点共用（§3.2 原则4）。

  load_context → send_message
      │（input_source 分叉）
      ├─ acquire_human_reply（interrupt 等待人工，状态经 checkpointer 保存）
      └─ simulate_reply（调用 PatientSimulatorAgent 工作流）
      │
      ▼
      conversation（ConversationAgent 子图：理解→合并→路由→追问/告别）
      │（turn_decision 路由）
      ├─ 追问 → 回到 acquire_reply / simulate_reply（循环）
      └─ 完成/转人工 → calculate_final_risk → persist_episode → review（ReviewAgent）→ build_report → END

唯一持久化与汇报出口：persist_episode → build_report（§6.2）。
resume 前置校验：episode 必须处于 waiting_human 才允许恢复（P0#8）。
节点统一经 Runtime[AppContext] 获取依赖，不再调用全局 get_context()（§7）。
"""
from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agents.states import PatientState
from agents.conversation_agent import conversation_workflow
from agents.patient_simulator_agent import run_simulation
from agents.review_agent import ReviewAgent
from agents.capability_agents.greeting_composer_agent import GreetingComposerAgent
from infrastructure.langgraph import get_checkpointer, thread_config
from infrastructure.runtime_context import AppContext
from domain.models.patient import PatientSnapshot
from domain.models.patient_report import PatientReport, RiskReport, CoverageReport


class EpisodeNotWaitingHuman(RuntimeError):
    """resume 校验失败：episode 当前不在 waiting_human 状态。"""


# ---- 节点：上下文加载 ----

def load_context_node(state: PatientState, runtime: Runtime[AppContext]) -> dict:
    """加载患者快照/计划/历史/策略（§6.2 load_context）。只读。"""
    ctx = runtime.context
    pid = state.get("patient_id", "")
    snap: PatientSnapshot | None = None
    if state.get("patient_snapshot"):
        snap = PatientSnapshot(**state["patient_snapshot"])
    else:
        p = ctx.patient_repository.get_patient(pid)
        if p is not None:
            p = dict(p)
            # §11-10：channel 以调度决定为准（电话回访=phone），不被快照固定为 wechat
            p["channel"] = (state.get("channel")
                            or p.get("preferred_channel") or p.get("channel") or "wechat")
            p.setdefault("history", ctx.patient_repository.get_history(pid, 7))
            # §6 / §11-11：input_source 由调度 State 显式传入（Dispatcher 按 manual_patient_ids 分流），
            # 不读取患者表中的 followup_mode 字段。
            snap = ctx.patient_repository.freeze_patient_snapshot(
                p, input_source=state.get("input_source", "simulator"))
    plan = {}
    try:
        plan_doc = ctx.plan_repository.get_latest_plan(pid)
        if plan_doc:
            plan = plan_doc.get("plan_json") or plan_doc
            plan = plan if isinstance(plan, dict) else {}
    except Exception:
        plan = {}
    ctx.event_outbox.episode_state_changed(
        dispatch_id=state.get("dispatch_id", ""), episode_id=state.get("episode_id", ""),
        patient_id=pid, status="running",
        input_source=state.get("input_source", "simulator"),
        channel=state.get("channel", "wechat"),
    )
    snap_dict = snap.model_dump() if snap else {}
    return {"patient_snapshot": snap_dict, "plan_snapshot": plan,
            "status": "running", "slots": {}, "turn_no": 0}


# ---- 节点：开场白 ----

async def send_message_node(state: PatientState, runtime: Runtime[AppContext]) -> dict:
    """发送开场白（§6.2 send_message，episode 首条）。经 MessageOutbox 幂等入队。

    日期冻结（§5.2）：业务日期取调度已冻结的 state.business_date，不再调用 DateProvider。
    """
    ctx = runtime.context
    snap = PatientSnapshot(**(state.get("patient_snapshot") or {}))
    bd = state.get("business_date", "")
    agent = GreetingComposerAgent(ctx.llm_gateway)
    greeting = await agent.compose(
        snap.brief.model_dump(),
        days_since_discharge=_days_since(snap, bd, ctx),
        history_summary=state.get("early_summary", ""),
        risk_tendency=snap.risk_level or "medium_risk",
    )
    first_msg = greeting if any(q in greeting for q in ("？", "?")) else f"{greeting}\n\n今天感觉怎么样呀？"
    turn_no = 1
    episode_id = state.get("episode_id", "")
    message_key = f"message:{episode_id}:{turn_no}:nurse"
    ctx.message_outbox.enqueue(
        episode_id=episode_id, patient_id=state.get("patient_id", ""),
        patient_name=snap.name, role="nurse", content=first_msg,
        turn_no=turn_no, message_key=message_key,
        dispatch_id=state.get("dispatch_id", ""),
    )
    # P0#8：interrupt 前的状态写入 checkpoint。acquire_human_reply 内 interrupt() 会在
    # 节点返回前挂起，其 return 不落 checkpoint，因此 waiting_human 必须在 send_message
    # 阶段（interrupt 之前）写入，resume 时据此校验。
    status = "waiting_human" if state.get("input_source") == "human" else "running"
    return {"messages": [{"role": "nurse", "content": first_msg,
                           "turn_no": turn_no, "message_key": message_key}],
            "turn_no": turn_no, "status": status,
            "evaluation_time": bd}


def _days_since(snap: PatientSnapshot, business_date: str = "", ctx=None) -> int:
    """出院后天数。业务日期用调度冻结的 business_date（无则回退 DateProvider）。"""
    if not snap.discharge_date:
        return 0
    try:
        from datetime import date
        bd = business_date
        if not bd:
            bd = (ctx or __import__(
                "infrastructure.runtime_context", fromlist=["get_context"]
            ).get_context()).clock.get_business_date().isoformat()
        dd = date.fromisoformat(snap.discharge_date[:10])
        return max(0, (date.fromisoformat(bd) - dd).days)
    except Exception:
        return 0


# ---- 节点：回复获取（唯一分叉点） ----

def _route_by_input_source(state: PatientState) -> str:
    """条件路由：human → acquire_human_reply；simulator → simulate_reply。"""
    return "acquire_human_reply" if state.get("input_source") == "human" else "simulate_reply"


async def acquire_human_reply_node(state: PatientState,
                                   runtime: Runtime[AppContext]) -> dict:
    """手动患者：interrupt 等待回复（§6.3）。状态经 checkpointer 保存跨进程可恢复。

    图到达此处被挂起；API/WebSocket 收到回复后用 Command(resume={"text": reply})
    恢复同一 thread。删除 wait_manual_done / asyncio.Event 长期等待（§2.2 关键变化）。
    """
    ctx = runtime.context
    episode_id = state.get("episode_id", "")
    msgs = state.get("messages") or []
    last_nurse = next(
        (m.get("content", "") for m in reversed(msgs) if m.get("role") == "nurse"), "")
    cov = state.get("coverage") or {}
    ctx.event_outbox.episode_state_changed(
        dispatch_id=state.get("dispatch_id", ""), episode_id=episode_id,
        patient_id=state.get("patient_id", ""), status="waiting_human",
        input_source="human", channel=state.get("channel", "wechat"),
    )
    ctx.event_outbox.waiting_human(
        dispatch_id=state.get("dispatch_id", ""), episode_id=episode_id,
        patient_id=state.get("patient_id", ""), question=last_nurse,
        thread_id=episode_id,
    )
    reply_payload = interrupt({
        "episode_id": episode_id,
        "patient_id": state.get("patient_id", ""),
        "last_message": last_nurse,
        "expected_slots": cov.get("missing", []),
    })
    if isinstance(reply_payload, dict):
        reply_text = reply_payload.get("text", "")
    elif isinstance(reply_payload, str):
        reply_text = reply_payload
    else:
        reply_text = ""
    turn_no = state.get("turn_no", 1)
    message_key = f"message:{episode_id}:{turn_no + 1}:patient"
    return {"messages": [{"role": "patient", "content": reply_text,
                           "turn_no": turn_no + 1, "message_key": message_key}],
            "turn_no": turn_no + 1, "status": "waiting_human"}


async def simulate_reply_node(state: PatientState,
                              runtime: Runtime[AppContext]) -> dict:
    """自动患者：调 PatientSimulatorAgent 工作流生成回复（§7 / §9）。

    场景事实由模拟工作流 ensure_scenario 生成一次并固化进 state.scenario，
    后续各轮复用（种子与轮次无关），同一患者症状/用药/情绪前后一致。
    """
    ctx = runtime.context
    snap = PatientSnapshot(**(state.get("patient_snapshot") or {}))
    turn_no = state.get("turn_no", 1)
    episode_id = state.get("episode_id", "")
    turn_decision = state.get("turn_decision") or {}
    missing_slots = turn_decision.get("missing_slots") or []
    # missing_slots 只作为 PatientSimulatorAgent 的参考，不强制规定回复内容。
    # Agent 会结合护士的实际问题、历史对话和患者场景自主决定如何回答。
    target_slot = missing_slots[0] if missing_slots else None
    result = await run_simulation(
        episode_id=episode_id,
        patient_id=state.get("patient_id", ""),
        scenario_seed=state.get("scenario_seed"),
        snapshot=state.get("patient_snapshot") or {},
        scenario=state.get("scenario"),
        transcript=state.get("messages") or [],
        round_num=turn_no,
        target_slot=target_slot,
        known_slots=state.get("slots") or {},
        context=ctx,
    )
    reply = result.get("reply", "今天还好。")
    message_key = f"message:{episode_id}:{turn_no + 1}:patient"
    ctx.message_outbox.enqueue(
        episode_id=episode_id, patient_id=state.get("patient_id", ""),
        patient_name=snap.name, role="patient", content=reply,
        turn_no=turn_no + 1, message_key=message_key,
        dispatch_id=state.get("dispatch_id", ""),
    )
    out = {"messages": [{"role": "patient", "content": reply,
                          "turn_no": turn_no + 1, "message_key": message_key}],
           "turn_no": turn_no + 1}
    if result.get("target_slot"):
        out["simulator_target_slot"] = result["target_slot"]
    if result.get("reply_metadata"):
        out["simulator_reply_meta"] = result["reply_metadata"]
    if result.get("scenario"):
        out["scenario"] = result["scenario"]
    if result.get("scenario_seed"):
        out["scenario_seed"] = result["scenario_seed"]
    return out


def _route_after_conversation(state: PatientState) -> str:
    """ConversationAgent 子图结束后的父图路由（§8.2 表）。

    追问 → 回到回复获取（继续下一轮）；完成/转人工（含紧急）→ calculate_final_risk。
    """
    action = (state.get("turn_decision") or {}).get("action", "clarify")
    if action in ("clarify", "continue_wait"):
        return _route_by_input_source(state)
    # 只有覆盖度完整、正常结束的对话才允许计算风险。
    # 信息不完整/紧急转人工直接持久化为未完成报告，禁止用缺失槽位生成分数。
    return "calculate_final_risk" if action == "complete" else "persist_episode"


# ---- 节点：持久化 ----

async def persist_episode_node(state: PatientState,
                               runtime: Runtime[AppContext]) -> dict:
    """持久化出口（§6.2 persist_episode）：幂等创建 session + 高风险告警。

    AI 审阅已拆出为独立 ReviewAgent（§4.5），由 review 节点在 session 就绪后触发一次。
    """
    ctx = runtime.context
    pid = state.get("patient_id", "")
    episode_id = state.get("episode_id", "")
    # §11-13：风险已由患者对话结束后的 calculate_final_risk 节点冻结；
    # 告警按冻结结果的 level 产生（high → 立即预警医生）。
    risk = state.get("risk_result") or {}
    slots = state.get("slots") or {}
    messages = state.get("messages") or []
    summary = _build_summary(risk, slots)

    session_key = f"session:{episode_id}"
    session_id = ctx.followup_repository.create_session(
        patient_id=pid, transcript_json=messages, risk_result=risk,
        agent_summary=summary, plan_id=(state.get("plan_snapshot") or {}).get("plan_id"),
        session_key=session_key,
    )

    snap = state.get("patient_snapshot") or {}
    if risk.get("level") == "high":
        alert_key = f"alert:{episode_id}:{risk.get('policy_version', '')}:{risk.get('level')}"
        ctx.event_outbox.doctor_alert(
            dispatch_id=state.get("dispatch_id", ""), episode_id=episode_id,
            patient_id=pid, patient_name=snap.get("name", ""),
            risk_result={**risk, "total_score": risk.get("score")},
            alert_key=alert_key,
        )
    return {"session_id": session_id, "agent_summary": summary}


def calculate_final_risk_node(state: PatientState,
                              runtime: Runtime[AppContext]) -> dict:
    """患者对话结束后计算唯一一次最终风险评分。

    对话中的 TurnRouter 只负责覆盖度和流程路由，不使用未完成的风险结果；
    这里在完整对话（包括最后一轮告别消息）结束后才产生评分事件。
    """
    ctx = runtime.context
    snap = state.get("patient_snapshot") or {}
    slots = state.get("slots") or {}
    today_data = {
        "nrs": slots.get("pain_nrs"),
        "sleep_quality": slots.get("sleep_quality"),
        "medication_taken": slots.get("medication_taken"),
    }
    report = ctx.risk_evaluator.evaluate(
        state.get("patient_id", ""),
        {"diagnosis": snap.get("diagnosis", ""), "age": snap.get("age", 0)},
        today_data,
        snap.get("history") or [],
    )
    risk_dict = report.model_dump()
    ctx.event_outbox.risk_scored(
        dispatch_id=state.get("dispatch_id", ""),
        episode_id=state.get("episode_id", ""),
        patient_id=state.get("patient_id", ""),
        patient_name=snap.get("name", ""),
        risk_result={**risk_dict, "total_score": risk_dict.get("score")},
    )
    return {"risk_result": risk_dict}


async def review_node(state: PatientState, runtime: Runtime[AppContext]) -> dict:
    """触发一次 ReviewAgent（§4.5：单个 episode 完成后只触发一次 AI 审阅）。"""
    ctx = runtime.context
    agent = ReviewAgent(ctx)
    history = {"history": (state.get("patient_snapshot") or {}).get("history") or []}
    result = await agent.run_review_episode(
        dispatch_id=state.get("dispatch_id", ""),
        episode_id=state.get("episode_id", ""),
        patient_id=state.get("patient_id", ""),
        session_id=state.get("session_id"),
        risk_result=state.get("risk_result") or {},
        agent_summary=state.get("agent_summary", ""),
        transcripts=state.get("messages") or [],
        plan=state.get("plan_snapshot") or {},
        history=history,
    )
    return {"review_id": result.get("review_id"),
            "review": result.get("review") or {}}


# ---- 节点：汇报 ----

def build_report_node(state: PatientState, runtime: Runtime[AppContext]) -> dict:
    """构建单患者汇报（§10.1 PatientReport）并发布终态事件。"""
    ctx = runtime.context
    td = state.get("turn_decision") or {}
    action = td.get("action", "complete")
    status_map = {
        "complete": "completed",
        "incomplete_handoff": "incomplete_handoff",
        "emergency_handoff": "human_handoff",
        "clarify": "completed",
    }
    status = status_map.get(action, "completed")
    risk = state.get("risk_result") or {}
    cov = state.get("coverage") or {}
    snap = state.get("patient_snapshot") or {}
    follow_up_needed = action in ("incomplete_handoff", "emergency_handoff")
    follow_up_reason = (
        "信息不完整，需进一步随访"
        if action == "incomplete_handoff"
        else "存在紧急情况，需人工进一步随访"
        if action == "emergency_handoff"
        else ""
    )
    if follow_up_needed:
        risk = dict(risk)
        risk.setdefault("level", "unknown")
        risk.setdefault("level_label", "未知")
        risk.setdefault("action", "需进一步随访")
        risk["details"] = {
            **(risk.get("details") or {}),
            "follow_up_needed": True,
            "follow_up_reason": follow_up_reason,
            "missing": cov.get("missing") or [],
        }
    # §11-15：错误从 errors 列表取最后一条的 message，而不是恒为 None
    _errors = state.get("errors") or []
    error = _errors[-1].get("message") if _errors else None
    report = PatientReport(
        episode_id=state.get("episode_id", ""),
        # 患者仓储可能返回 int ID，但 PatientReport/API 契约统一使用字符串。
        patient_id=str(state.get("patient_id", "")),
        name=snap.get("name", ""),
        status=status,
        input_source=state.get("input_source", "simulator"),
        channel=state.get("channel", "wechat"),
        session_id=(str(state.get("session_id")) if state.get("session_id") is not None else None),
        review_id=(str(state.get("review_id")) if state.get("review_id") is not None else None),
        risk=RiskReport(
            level=risk.get("level", "unknown"),
            score=risk.get("score"),
            policy_version=risk.get("policy_version", ""),
            matched_rules=risk.get("matched_rules") or [],
            base_score=risk.get("base_score"),
            dynamic_score=risk.get("dynamic_score"),
            level_label=risk.get("level_label", ""),
            action=risk.get("action", ""),
            details=risk.get("details", {}),
        ),
        coverage=CoverageReport(
            complete=cov.get("complete", False),
            collected=cov.get("collected") or [],
            missing=cov.get("missing") or [],
        ),
        follow_up_needed=follow_up_needed,
        follow_up_reason=follow_up_reason,
        summary=state.get("review", {}).get("summary", ""),
        # §9.1.8：callback 与 conversation 两策略版本分开持久化，禁止互写
        callback_policy_version=state.get("callback_policy_version"),
        conversation_policy_version=state.get("conversation_policy_version"),
        business_date=state.get("business_date", ""),
        decision_trace={"turn_decision": td, "slots": state.get("slots", {})},
        started_at=state.get("evaluation_time", "") or state.get("business_date", ""),
        error=error,
    )
    ctx.event_outbox.episode_state_changed(
        dispatch_id=state.get("dispatch_id", ""),
        episode_id=state.get("episode_id", ""),
        patient_id=state.get("patient_id", ""),
        status=status,
        input_source=state.get("input_source", "simulator"),
        channel=state.get("channel", "wechat"),
    )
    ctx.event_outbox.report_ready(
        dispatch_id=state.get("dispatch_id", ""),
        episode_id=state.get("episode_id", ""),
        patient_id=state.get("patient_id", ""),
        status=status, risk_result=risk,
        report=report.model_dump(),
    )
    sid = state.get("session_id")
    rid = state.get("review_id")
    out = report.model_dump()
    out["session_id"] = str(sid) if sid is not None else None
    out["review_id"] = str(rid) if rid is not None else None
    return {"report": out, "status": status}


def _build_summary(risk: dict, slots: dict) -> str:
    return (f"风险评分 {risk.get('score')} 分 → {risk.get('level_label') or risk.get('level','未知')}；"
            f"NRS={slots.get('pain_nrs')}, 睡眠={slots.get('sleep_quality')}, "
            f"用药={'是' if slots.get('medication_taken') else '否'}")


# ---- 图 ----

def build_patient_followup_graph():
    g = StateGraph(PatientState, context_schema=AppContext)
    g.add_node("load_context", load_context_node)
    g.add_node("send_message", send_message_node)
    g.add_node("acquire_human_reply", acquire_human_reply_node)
    g.add_node("simulate_reply", simulate_reply_node)
    g.add_node("conversation", conversation_workflow)   # ConversationAgent 子图
    g.add_node("calculate_final_risk", calculate_final_risk_node)
    g.add_node("persist_episode", persist_episode_node)
    g.add_node("review", review_node)
    g.add_node("build_report", build_report_node)

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "send_message")
    # 首条消息后，按 input_source 分叉到回复获取
    g.add_conditional_edges("send_message", _route_by_input_source,
                             ["acquire_human_reply", "simulate_reply"])
    # 回复获取 → ConversationAgent 子图（理解/合并/风险/路由/追问或告别）
    g.add_edge("acquire_human_reply", "conversation")
    g.add_edge("simulate_reply", "conversation")
    # 子图结束：追问 → 回回复获取（循环）；完成/转人工 → 最终评分
    g.add_conditional_edges("conversation", _route_after_conversation,
                             ["acquire_human_reply", "simulate_reply",
                              "calculate_final_risk", "persist_episode"])
    g.add_edge("calculate_final_risk", "persist_episode")
    g.add_edge("persist_episode", "review")
    g.add_edge("review", "build_report")
    g.add_edge("build_report", END)
    return g.compile(checkpointer=get_checkpointer())


patient_followup_workflow = build_patient_followup_graph()


async def run_patient_episode(*, episode_id: str, dispatch_id: str,
                              patient_id: str, input_source: str = "simulator",
                              channel: str = "wechat", scenario: dict | None = None,
                              scenario_seed: str | None = None,
                              business_date: str = "",
                              callback_policy_version: str | None = None,
                              conversation_policy_version: str | None = None,
                              context=None) -> dict:
    """启动一位患者的一次随访 episode（自动患者在同一次运行中结束）。

    business_date 由调度入口冻结后传入（§5.2），节点不再自行获取系统日期。
    context：显式注入 AppContext（EpisodeService / Dispatcher 传入），缺省取全局单例。
    """
    if context is None:
        from infrastructure.runtime_context import get_context
        context = get_context()
    return await patient_followup_workflow.ainvoke(
        {
            "episode_id": episode_id, "dispatch_id": dispatch_id,
            "patient_id": patient_id, "input_source": input_source,
            "channel": channel, "scenario": scenario, "scenario_seed": scenario_seed,
            "business_date": business_date,
            "callback_policy_version": callback_policy_version,
            "conversation_policy_version": conversation_policy_version,
            "turn_no": 0, "slots": {}, "messages": [],
        },
        config=thread_config(episode_id),
        context=context,
    )


async def run_no_reply_episode(*, episode_id: str, dispatch_id: str,
                               patient_id: str, patient_name: str,
                               message_content: str, no_reply_days: int,
                               business_date: str = "",
                               callback_policy_version: str | None = None,
                               conversation_policy_version: str | None = None,
                               input_source: str = "simulator",
                               channel: str = "phone",
                               context=None) -> dict:
    """Complete a no-reply follow-up after sending one reminder.

    A phone-callback patient has no ordinary conversation loop, but still needs
    a terminal episode, session transcript, AI review, and report so dispatch
    aggregation and the review screen treat it like every other patient.
    """
    if context is None:
        from infrastructure.runtime_context import get_context
        context = get_context()
    ctx = context
    message_key = f"message:{episode_id}:phone_callback:1"
    message = {
        "role": "nurse",
        "content": message_content,
        "turn_no": 1,
        "message_key": message_key,
    }
    reason = f"连续{no_reply_days}天未回复本次随访消息，需电话回访"
    risk = {
        "level": "unknown",
        "score": None,
        "level_label": "未评估",
        "action": "电话回访",
        "details": {
            "no_reply_days": no_reply_days,
            "follow_up_needed": True,
            "follow_up_reason": reason,
        },
    }

    ctx.event_outbox.episode_state_changed(
        dispatch_id=dispatch_id, episode_id=episode_id,
        patient_id=str(patient_id), status="running",
        input_source=input_source, channel=channel,
    )
    ctx.message_outbox.enqueue(
        episode_id=episode_id, dispatch_id=dispatch_id,
        patient_id=str(patient_id), patient_name=patient_name,
        role="nurse", content=message_content, turn_no=1,
        message_key=message_key,
    )

    summary = (
        f"已发送本次随访提醒；患者已连续{no_reply_days}天未回复，"
        "本次不等待患者回复，已转电话回访。"
    )
    session_id = ctx.followup_repository.create_session(
        patient_id=str(patient_id), transcript_json=[message],
        risk_result=risk, agent_summary=summary,
        session_key=f"session:{episode_id}",
    )
    try:
        history = ctx.patient_repository.get_history(str(patient_id), 7) or []
    except Exception:
        history = []
    try:
        plan = ctx.plan_repository.get_latest_plan(str(patient_id)) or {}
    except Exception:
        plan = {}
    plan = plan.get("plan_json") or plan if isinstance(plan, dict) else {}

    review_result = await ReviewAgent(ctx).run_review_episode(
        dispatch_id=dispatch_id, episode_id=episode_id,
        patient_id=str(patient_id), session_id=session_id,
        risk_result=risk, agent_summary=summary,
        transcripts=[message], plan=plan, history={"history": history},
    )
    report = PatientReport(
        episode_id=episode_id, patient_id=str(patient_id), name=patient_name,
        status="completed", input_source=input_source, channel=channel,
        session_id=str(session_id) if session_id is not None else None,
        review_id=(str(review_result.get("review_id"))
                   if review_result.get("review_id") is not None else None),
        risk=RiskReport(**risk),
        coverage=CoverageReport(
            complete=False, collected=[],
            missing=["pain_nrs", "sleep_quality", "medication_taken", "side_effects"],
        ),
        follow_up_needed=True, follow_up_reason=reason,
        summary=(review_result.get("review") or {}).get("summary", summary),
        callback_policy_version=callback_policy_version,
        conversation_policy_version=conversation_policy_version,
        business_date=business_date,
        decision_trace={
            "turn_decision": {
                "action": "incomplete_handoff",
                "reason": reason,
                "missing_slots": [],
            },
            "slots": {},
            "no_reply": True,
        },
        started_at=business_date, finished_at=business_date,
    )
    report_dict = report.model_dump()
    await ctx.episode_repository.complete_once(
        episode_id, report_dict,
        key=f"episode:{episode_id}:completed", status="completed",
    )
    ctx.event_outbox.episode_state_changed(
        dispatch_id=dispatch_id, episode_id=episode_id,
        patient_id=str(patient_id), status="completed",
        input_source=input_source, channel=channel,
    )
    ctx.event_outbox.report_ready(
        dispatch_id=dispatch_id, episode_id=episode_id,
        patient_id=str(patient_id), status="completed",
        risk_result=risk, report=report_dict,
    )
    return report_dict


async def resume_patient_episode(*, episode_id: str, reply_text: str,
                                 context=None) -> dict:
    """手动患者提交回复后恢复同一 episode（§13 / §6.3）。

    P0#8：恢复前校验 checkpoint 状态，必须处于 waiting_human（interrupt 挂起处），
    否则拒绝恢复，避免在错误的流程阶段注入人工回复。
    """
    if context is None:
        from infrastructure.runtime_context import get_context
        context = get_context()
    from langgraph.types import Command
    config = thread_config(episode_id)
    status = _checkpoint_status(episode_id, config)
    if status != "waiting_human":
        raise EpisodeNotWaitingHuman(
            f"episode {episode_id} 当前状态为 {status!r}，非 waiting_human，不能恢复")
    return await patient_followup_workflow.ainvoke(
        Command(resume={"text": reply_text}),
        config=config,
        context=context,
    )


def _checkpoint_status(episode_id: str, config: dict) -> str | None:
    try:
        snap = patient_followup_workflow.get_state(config)
        return (snap.values or {}).get("status") if snap else None
    except Exception:
        return None
