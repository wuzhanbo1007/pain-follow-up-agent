"""
医生人工审阅管线（纯业务流程，无 LLM / 非 Agent）

  - submit_review：医生提交评分/评论/跟踪状态，落库 followup_review 并留痕。
  - list_review_queue：审阅列表 + 筛选。
  - get_review_statistics：审阅统计（完成率/平均评分/需跟踪数）。

（build_review_graph 为预留的 LangGraph 编排入口，当前未在生产路径使用。）
"""
import json
from langgraph.graph import StateGraph, START, END

from agents.state import AgentState
from db.followup_db import (
    get_session, create_review, list_reviews, review_stats, get_plan,
)


def _package(state: AgentState) -> dict:
    """package_session：从 DB 读取会话并打包摘要/原文/风险"""
    session = get_session(state.get("session_id"))
    if not session:
        return {"error": f"未找到会话 {state.get('session_id')}"}
    transcript = session.get("transcript_json", [])
    risk = session.get("risk_result", {})
    summary = session.get("agent_summary", "")
    risk_level = risk.get("level_label") or risk.get("level") or "未知"
    packaged = {
        "session_id": session["session_id"],
        "patient_id": session["patient_id"],
        "summary": summary,
        "risk_level": risk_level,
        "transcript_count": len(transcript) if isinstance(transcript, list) else 0,
    }
    return {"agent_summary": summary, "risk_result": risk, "messages": [packaged]}


def _notify(state: AgentState, emit=None) -> dict:
    """notify_review_queue：推送 review:session_ready（若有 emit 回调）"""
    if emit:
        emit("review:session_ready", {
            "session_id": state.get("session_id"),
            "patient_id": state.get("agent_summary") or state.get("patient_id"),
            "summary": state.get("agent_summary", ""),
            "risk_level": (state.get("risk_result") or {}).get("level_label", ""),
        })
    return {}


def build_review_graph(emit=None):
    g = StateGraph(AgentState)
    g.add_node("package_session", _package)
    g.add_node("notify_review_queue", lambda s: _notify(s, emit=emit))
    g.add_edge(START, "package_session")
    g.add_edge("package_session", "notify_review_queue")
    g.add_edge("notify_review_queue", END)
    return g.compile()


def submit_review(session_id, doctor_score=None, doctor_comment=None,
                 track_status="pending_track", reviewer_id=None):
    """
    医生提交审阅（需求二 F2.3/F2.4/F2.5）——对应 POST /api/reviews
    落库 followup_review，audit_snapshot 保存会话快照（留痕，风险 4）。
    """
    session = get_session(session_id)
    if not session:
        return {"error": f"未找到会话 {session_id}"}
    audit = {
        "session": session,
        "plan": get_plan(session.get("plan_id")) if session.get("plan_id") else None,
    }
    review_id = create_review(
        session_id=session_id,
        patient_id=session["patient_id"],
        doctor_score=doctor_score,
        doctor_comment=doctor_comment,
        track_status=track_status,
        reviewer_id=reviewer_id,
        audit_snapshot=audit,
    )
    return {
        "review_id": review_id,
        "session_id": session_id,
        "patient_id": session["patient_id"],
        "doctor_score": doctor_score,
        "track_status": track_status,
        "reviewed_at": audit.get("reviewed_at") or "",
    }


def list_review_queue(status=None, patient_id=None):
    return list_reviews(status=status, patient_id=patient_id)


def get_review_statistics(today=None):
    return review_stats(today=today)
