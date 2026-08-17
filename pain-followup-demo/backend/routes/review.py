# backend/routes/review.py
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
    get_latest_transcripts, get_latest_sessions,
)
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
    dispatch_id: str = Query(None),
):
    """每个患者最新一次随访会话（按患者去重），供 Review 面板展示最新内容"""
    rows = get_latest_sessions(today=today, dispatch_id=dispatch_id)
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
def api_review_stats(today: str = Query(None), dispatch_id: str = Query(None)):
    """F2.8 统计：审阅完成率 / 平均评分 / 需跟踪数"""
    stats = get_review_statistics(today=today, dispatch_id=dispatch_id)
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


