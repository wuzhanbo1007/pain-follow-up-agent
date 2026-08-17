# backend/routes/episodes_route.py
"""routes/episodes_route.py —— 单患者 episode REST（说明书 13）。

POST /api/followups/episodes/{id}/reply   提交手动患者回复（PatientGraph + Command(resume)）
GET  /api/followups/episodes/{id}          读取患者 episode 状态和会话（只查 Projection）
"""
from __future__ import annotations

from fastapi import APIRouter, Body
from pydantic import BaseModel

from services.dispatch_service import DispatchService
from infrastructure.runtime_context import get_context

episodes_router = APIRouter(prefix="/api/followups", tags=["followup-episode"])


class ReplyRequest(BaseModel):
    text: str


@episodes_router.post("/episodes/{episode_id}/reply")
async def reply_episode(episode_id: str, req: ReplyRequest = Body(...)):
    svc = DispatchService(get_context())
    result = await svc.resume_episode(episode_id, req.text)
    return {"episode_id": episode_id, "result": result}


@episodes_router.get("/episodes/{episode_id}")
async def get_episode(episode_id: str):
    """读取患者 episode 状态和会话投影（只查 checkpoint，不运行图、不修改状态）。

    返回结构（说明书 §12.10）：episode_id / dispatch_id / patient_id / status /
    messages / risk_result / review_status / report。
    """
    from agents.patient_followup_agent import patient_followup_workflow
    from infrastructure.langgraph import thread_config
    try:
        snap = patient_followup_workflow.get_state(thread_config(episode_id))
    except Exception:
        snap = None
    values = (snap.values or {}) if snap is not None else {}
    if not values:
        return {"episode_id": episode_id, "status": "not_found", "dispatch_id": "",
                "patient_id": "", "messages": [], "risk_result": {},
                "review_status": "pending", "report": None}
    review_status = ("ai_review_ready" if (values.get("review_id")
                                           or values.get("review")) else "pending")
    return {
        "episode_id": episode_id,
        "dispatch_id": values.get("dispatch_id", ""),
        "patient_id": values.get("patient_id", ""),
        "status": values.get("status", ""),
        "messages": values.get("messages", []),
        "risk_result": values.get("risk_result", {}),
        "review_status": review_status,
        "report": values.get("report"),
    }
