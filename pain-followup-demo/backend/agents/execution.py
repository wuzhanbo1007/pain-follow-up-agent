"""
C. 随访执行智能体（FollowUpExecutionAgent）

ReACT 循环由 function-calling 驱动——LLM 持工具（parse/history/risk/escalate/finalize），
自行解析回复、评估风险、生成追问，一次调用替代原来的两次调用。

图结构：
    START → generate_message
              ↓
         ┌→ collect_one_round ──┐
         │   (收回复 + 发追问)      │
         │        ↓               │
         │    tool_reflect ──────┘  (条件边: 未结束 → 循环)
         │        ↓
         score_risk → persist_session → END

设计：
  - 复用现有引擎：MessageGenerator / ReplyParser / RiskEngine / AutoReplyEngine
  - 函数调用工具从 engine.tool_definitions.HANDLER_MAP 加载
  - LLM 不可用时降级到固定轮次兜底
"""
import json
import time
from datetime import datetime
from langgraph.graph import StateGraph, START, END

from agents.state import AgentState
from engine.react_core import run_tool_reflect, target_question
from data.database import PatientDB
from db.followup_db import create_session
from llm.message_generator import MessageGenerator
from llm.parser import ReplyParser
from engine.risk_engine import RiskEngine
from engine.auto_reply import AutoReplyEngine

# LLM 完全不可用时的降级轮次
FALLBACK_MAX_ROUNDS = 2
# 硬编码最大轮次上限（防止 LLM 在患者反复给模糊回复时无限追问）
HARD_MAX_ROUNDS = 4  # 自动患者 4 轮收尾；手动演示（followup_service）单独用 20 轮


def _engines():
    """按 app.py 同款初始化引擎（懒加载）"""
    mg = MessageGenerator()
    rp = ReplyParser()
    re_ = RiskEngine()
    return mg, rp, re_


# ========== 图节点 ==========

async def _generate_message(state: AgentState) -> dict:
    patient = state.get("patient", {})
    mg, _, _ = _engines()
    try:
        # 用新版开场白（问候+出院天数+人文关怀+问句结尾），与手动演示路径一致
        msg = await mg.generate_greeting(patient)
    except Exception:
        msg = f"{patient.get('name','患者')}您好，又到随访时间了。今天感觉怎么样？疼痛大概几分？睡得还好吗？药有没有按时吃呀？"
    transcript = [{"role": "nurse", "content": msg}]
    return {"transcript": transcript, "agent_summary": f"已生成随访消息并发送：{msg[:40]}..."}


async def _collect_one_round(state: AgentState) -> dict:
    """单轮收集：先发送上轮护士追问，再模拟一条患者回复。

    若 state 中有 _pending_nurse_msg（上轮 tool_reflect 产出），
    则先追加它；首轮无 pending 则跳过，由下轮 tool_reflect 产出第一条追问。"""
    patient = state.get("patient", {})
    pid = patient.get("patient_id")
    transcript = list(state.get("transcript", []))
    rnd = max(1, state.get("react_round", 1))

    engine = None
    try:
        engine = AutoReplyEngine([patient], {}, RiskEngine())
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"[execution] AutoReplyEngine 创建失败 patient={pid}: {exc}")

    # 护士追问：放在患者回复之前（先发护士消息，再收患者回复）
    pending = state.get("_pending_nurse_msg")
    if pending:
        transcript.append({"role": "nurse", "content": pending})

    # 生成患者回复（模拟）
    try:
        reply = engine.generate_reply(pid, rnd) if engine else "今天还好"
    except Exception:
        reply = "今天还好"
    transcript.append({"role": "patient", "content": reply})

    return {"transcript": transcript, "_pending_nurse_msg": ""}


async def _tool_reflect(state: AgentState) -> dict:
    """Function-Calling 反射：LLM 持工具解析回复、评估风险、决策是否继续。

    LLM 的文本输出即为下轮护士追问（存入 _pending_nurse_msg）。
    """
    patient = state.get("patient", {})
    transcript = list(state.get("transcript", []))
    inv = dict(state.get("react_info_inventory", {}))
    rnd = state.get("react_round", 1)

    decision = await run_tool_reflect(
        patient, transcript, inv,
        fallback_fn=lambda: _react_fallback(rnd),
    )
    # 提取结构化信息合并
    parsed = decision.get("parsed") or {}
    if parsed.get("nrs_score") is not None:
        inv["pain_nrs"] = parsed["nrs_score"]
    if parsed.get("sleep_quality") is not None:
        inv["sleep_quality"] = parsed["sleep_quality"]
    if parsed.get("medication_taken") is not None:
        inv["medication_taken"] = bool(parsed["medication_taken"])
    if parsed.get("side_effects") is not None:
        inv["side_effects"] = parsed["side_effects"]

    # 护栏：信息没收齐不让结束（与手动路径一致）
    should_end = decision.get("should_end", False)
    missing_items = [
        k for k in ("pain_nrs", "sleep_quality", "medication_taken", "side_effects")
        if inv.get(k) in (None, "")
    ]
    if should_end and missing_items and rnd < HARD_MAX_ROUNDS:
        should_end = False
        decision["next_question"] = await target_question(missing_items, patient, transcript)

    # 风险评分（来自工具调用或旧版兜底）
    risk_result = decision.get("risk_result") or {}
    if not risk_result:
        risk_result = state.get("risk_result", {})
    rr = dict(risk_result)
    if parsed:
        rr["parsed"] = parsed

    return {
        "react_round": rnd + 1,
        "react_should_end": should_end,
        "react_decision": decision,
        "react_info_inventory": inv,
        "_pending_nurse_msg": decision.get("next_question", ""),
        "risk_result": rr,
    }


def _score_risk(state: AgentState) -> dict:
    patient = state.get("patient", {})
    pid = patient.get("patient_id")
    parsed = state.get("risk_result", {}).get("parsed", {})
    today = {
        "nrs": parsed.get("nrs_score"),
        "sleep_quality": parsed.get("sleep_quality"),
        "medication_taken": parsed.get("medication_taken"),
    }
    history_7d = patient.get("history", [])[-7:]
    _, _, re_ = _engines()
    try:
        result = re_.calculate(pid, patient, today, history_7d)
    except Exception as e:
        result = {"level": "low", "total_score": 3, "level_label": "低风险", "details": {"error": str(e)}}
    summary = f"风险评分 {result.get('total_score')} 分 → {result.get('level_label')}；" \
              f"NRS={today.get('nrs')}, 睡眠={today.get('sleep_quality')}, 用药={'是' if today.get('medication_taken') else '否'}"
    rr = dict(state.get("risk_result", {}))
    rr.update(result)
    return {"risk_result": rr, "agent_summary": summary}


def _persist_session(state: AgentState) -> dict:
    pid = state.get("patient", {}).get("patient_id")
    session_id = create_session(
        patient_id=pid,
        transcript_json=state.get("transcript", []),
        risk_result=state.get("risk_result", {}),
        agent_summary=state.get("agent_summary", ""),
        plan_id=state.get("plan_id"),
    )
    return {"session_id": session_id}


# ========== 条件路由 ==========

def _should_continue(state: AgentState) -> str:
    """条件边：ReACT 决定是否继续循环。
    返回 "continue" → 回到 collect_one_round
    返回 "end"      → 进入 score_risk
    注意：轮次自增已在 react_reflect 节点内写回，这里只负责路由。

    硬编码上限：超过 HARD_MAX_ROUNDS 轮后强制结束，防止 LLM 在患者反复
    给出模糊回复（如"今天还好"）时无限追问，导致重复性问题。
    """
    if state.get("react_should_end", False):
        return "end"
    if state.get("react_round", 0) >= HARD_MAX_ROUNDS:
        return "end"
    return "continue"


# ========== 辅助函数 ==========

def _react_fallback(rnd: int) -> dict:
    """execution 路径的降级兜底：LLM 完全不可用时用固定轮次兜底。"""
    should_end = rnd >= FALLBACK_MAX_ROUNDS
    return {
        "should_end": should_end,
        "reasoning": f"LLM 不可用，固定 {FALLBACK_MAX_ROUNDS} 轮降级",
        "info_inventory": {},
        "next_action": "end" if should_end else "continue",
        "next_question": "",
    }


# ========== 图构建 ==========

def build_execution_graph():
    g = StateGraph(AgentState)

    g.add_node("generate_message", _generate_message)
    g.add_node("collect_one_round", _collect_one_round)
    g.add_node("tool_reflect", _tool_reflect)
    g.add_node("score_risk", _score_risk)
    g.add_node("persist_session", _persist_session)

    # 线性部分：开场 → 收一轮回复 → function-calling 反射
    g.add_edge(START, "generate_message")
    g.add_edge("generate_message", "collect_one_round")
    g.add_edge("collect_one_round", "tool_reflect")

    # 条件分支：tool_reflect 决定继续追问还是评分结束
    g.add_conditional_edges(
        "tool_reflect",
        _should_continue,
        {
            "continue": "collect_one_round",
            "end": "score_risk",
        }
    )

    g.add_edge("score_risk", "persist_session")
    g.add_edge("persist_session", END)

    return g.compile()


execution_graph = build_execution_graph()


async def run_session(patient_id: str, plan_id: str = None) -> dict:
    """对单个患者跑一次随访会话（供 API 调用；C 为自主 Agent）。

    与 Comet 编排一致：全链路 async，LangGraph 用 await graph.ainvoke(...)。
    """
    db = PatientDB()
    patient = db.get_patient(patient_id)
    if not patient:
        return {"error": f"未找到患者 {patient_id}"}
    patient["history"] = db.get_history(patient_id, days=7)
    result = await execution_graph.ainvoke({
        "patient_id": patient_id,
        "patient": patient,
        "plan_id": plan_id,
        "plan_driven": bool(plan_id),
        "react_round": 0,
        "react_info_inventory": {},
        "react_should_end": False,
    })
    return {
        "session_id": result.get("session_id"),
        "patient_id": patient_id,
        "risk_result": result.get("risk_result", {}),
        "agent_summary": result.get("agent_summary", ""),
        "react_decision": result.get("react_decision"),
    }
