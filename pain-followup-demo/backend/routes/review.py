"""
随访过程 Review REST 路由（需求二 F2.1–F2.8）

事件（WebSocket）：
  review:session_ready → 推送待审阅会话到医生屏
  review:submitted      → 提交回执 + 统计刷新

含 LLM 调用的路由为 async def，直接 await run_followup_pipeline（底层
ChatOpenAI.ainvoke → AsyncOpenAI，事件循环非阻塞，与 Comet 编排一致）；
纯 DB 查询路由保持同步 def，由 FastAPI 线程池托管。
"""
from typing import Optional
from fastapi import APIRouter, Query, Body, HTTPException

from services.doctor_review import (
    submit_review, list_review_queue, get_review_statistics, get_session,
)
from db.followup_db import get_latest_transcripts, get_latest_sessions
from agents.orchestrator import run_followup_pipeline
from core.realtime import emit

review_router = APIRouter(tags=["reviews"])


@review_router.get("/api/sessions/{session_id}")
def api_get_session(session_id: str):
    """F2.2 会话详情"""
    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "未找到会话"})
    return {"ok": True, "session": s}


@review_router.get("/api/sessions/transcripts")
def api_session_transcripts():
    """返回每个患者最新一次随访会话的 transcript，供微信对话页刷新后还原历史对话"""
    data = get_latest_transcripts()
    return {"ok": True, "transcripts": data}


@review_router.get("/api/reviews/latest")
def api_reviews_latest(
    status: str = Query(None),
    today: str = Query(None),
):
    """每个患者最新一次随访会话（按患者去重），供 Review 面板展示最新内容"""
    rows = get_latest_sessions(today=today)
    if status:
        rows = [r for r in rows if (r.get("track_status") or "") == status]
    return {"ok": True, "count": len(rows), "reviews": rows}


@review_router.get("/api/reviews")
def api_list_reviews(
    status: str = Query(None),
    patient_id: str = Query(None),
):
    """F2.1 / F2.7 审阅列表 + 筛选"""
    rows = list_review_queue(status=status, patient_id=patient_id)
    return {"ok": True, "count": len(rows), "reviews": rows}


@review_router.get("/api/reviews/stats")
def api_review_stats(today: str = Query(None)):
    """F2.8 统计：审阅完成率 / 平均评分 / 需跟踪数"""
    stats = get_review_statistics(today=today)
    return {"ok": True, **stats}


@review_router.post("/api/reviews")
def api_submit_review(data: Optional[dict] = Body(None)):
    """F2.3/F2.4/F2.5 医生提交：评分 + 评论 + 跟踪状态 + 留痕"""
    if data is None:
        data = {}
    session_id = data.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "缺少 session_id"})
    try:
        result = submit_review(
            session_id=session_id,
            doctor_score=data.get("doctor_score"),
            doctor_comment=data.get("doctor_comment"),
            track_status=data.get("track_status", "followup_done"),
            reviewer_id=data.get("reviewer_id"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail={"ok": False, "error": str(e)})
    fresh_stats = get_review_statistics()
    emit({"type": "review:submitted", "data": {**result, "stats": fresh_stats}})
    return {"ok": True, **result}


@review_router.post("/api/sessions/run/{patient_id}")
async def api_run_session(patient_id: str):
    """
    触发一次完整随访流水线（orchestrator 编排：B 判定 → C 执行 → D 总结）。
    返回 session_id / risk_result / agent_summary /
    dialogue_decision 等字段，并额外返回 b_decision / d_review 等编排结果。
    """
    try:
        result = await run_followup_pipeline(patient_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"ok": False, "error": str(e)})
    c = result.get("c_session", {}) or {}
    emit({"type": "review:session_ready", "data": {
        "session_id": c.get("session_id"),
        "patient_id": patient_id,
        "summary": c.get("agent_summary", ""),
        "risk_level": (c.get("risk_result") or {}).get("level_label", ""),
    }})
    return {
        "ok": True,
        "session_id": c.get("session_id"),
        "patient_id": patient_id,
        "risk_result": c.get("risk_result"),
        "agent_summary": c.get("agent_summary"),
        "dialogue_decision": c.get("dialogue_decision"),
        "b_decision": result.get("b_decision"),
        "d_review": result.get("d_review"),
        "review_id": result.get("review_id"),
        "early_return": result.get("early_return", False),
        "summary": result.get("summary"),
    }
