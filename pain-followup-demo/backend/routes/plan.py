# backend/routes/plan.py
"""
随访计划 REST 路由（需求一 F1.1–F1.9）

事件（WebSocket）：
  plan:drafted  → 推送草稿给医生屏 PlanReviewPanel
  plan:approved / plan:modified → 回执

含 LLM 调用的路由为 async def，直接 await generate_draft（底层 ChatOpenAI.ainvoke
→ AsyncOpenAI，事件循环非阻塞，与 Comet 编排一致）；纯 DB/文件路由保持同步 def，
由 FastAPI 线程池托管。
"""
import traceback
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Query, Body, HTTPException
from fastapi.responses import FileResponse
from core.logging_config import get_logger

log = get_logger("painsmart.plan")

from agents.planner_agent import generate_draft
from infrastructure.repositories.followup_repository import PlanRepository
from knowledge import config as kb_config
from core.realtime import emit

plan_router = APIRouter(tags=["plans"])

# §12.2：计划审批/修改/列表统一经 PlanRepository（不再直连 db.followup_db）
_plan_repo = PlanRepository()


@plan_router.get("/api/guideline-file")
def api_guideline_file(
    guide: str = Query(""),
    source: str = Query(""),
):
    """溯源跳转：按指南名（guide）或文件名（source）定位知识库原始 PDF/MD，
    供前端"查看原文"在新标签页打开并跳转到对应页码。

    文件系统遍历在 os.scandir/rglob 层面是同步 I/O，由 FastAPI 线程池托管执行。
    """
    guide = guide.strip()
    source = source.strip()

    def _find_target():
        raw_dir = Path(kb_config.RAW_DIR)
        if not raw_dir.exists():
            return None

        target = None
        # ① 优先按文件名精确匹配（source = 完整文件名）
        if source:
            for p in raw_dir.rglob(source):
                if p.is_file():
                    target = p
                    break

        # ② 按 guide 匹配文件词干（stem），先精确 → 再前缀 → 再包含
        if not target and guide:
            g = guide.strip().lower()
            candidates = []
            for p in sorted(raw_dir.rglob("*")):
                if not (p.is_file() and p.suffix.lower() in (".pdf", ".md", ".markdown", ".txt")):
                    continue
                stem = p.stem.strip().lower()
                if stem == g:
                    target = p
                    break
                if stem.startswith(g):
                    candidates.append(p)
            if not target and candidates:
                target = candidates[0]
        return target

    target = _find_target()
    if not target:
        raise HTTPException(status_code=404, detail={"ok": False, "error": f"未找到对应指南原文：{guide or source}"})

    mimetype = "application/pdf" if target.suffix.lower() == ".pdf" else "text/plain"
    return FileResponse(str(target), media_type=mimetype)


@plan_router.post("/api/plans/generate")
async def api_generate_plan(data: Optional[dict] = Body(None)):
    """出院触发 / 手动生成随访计划草稿（A 号 Agent + B 号 RAG）"""
    if data is None:
        data = {}
    patient_id = data.get("patient_id")
    if not patient_id:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "缺少 patient_id"})
    try:
        import time as _t
        _start = _t.time()
        log.info("生成随访计划: patient_id=%s", patient_id)
        # generate_draft 内含 LLM 调用 → await（事件循环非阻塞）
        draft = await generate_draft(
            patient_id,
            diagnosis=data.get("diagnosis"),
            discharge_summary=data.get("discharge_summary"),
        )
        log.info("计划生成完成: patient_id=%s plan_id=%s freq=%s (%.1fs)",
                 patient_id, draft.get("plan_id"),
                 draft.get("plan_json", {}).get("frequency", "?"),
                 _t.time() - _start)
    except Exception as e:
        log.error("计划生成失败: patient_id=%s err=%s", patient_id, e)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail={"ok": False, "error": str(e)})
    try:
        emit({"type": "plan:drafted", "data": {
            "plan_id": draft.get("plan_id"),
            "patient_id": patient_id,
            "diagnosis": draft.get("diagnosis", ""),
            "plan_json": draft.get("plan_json", {}),
            "citations": draft.get("citations", []),
        }})
    except Exception:
        pass  # WebSocket 推送失败不影响 HTTP 响应
    return {"ok": True, **draft}


@plan_router.post("/api/plans/{plan_id}/approve")
def api_approve_plan(plan_id: str, data: Optional[dict] = Body(None)):
    """F1.5 医生同意"""
    if data is None:
        data = {}
    ok = _plan_repo.approve_plan(plan_id, data.get("doctor_id"))
    emit({"type": "plan:approved", "data": {"plan_id": plan_id, "doctor_id": data.get("doctor_id")}})
    return {"ok": ok, "plan_id": plan_id, "status": "approved"}


@plan_router.post("/api/plans/{plan_id}/modify")
def api_modify_plan(plan_id: str, data: Optional[dict] = Body(None)):
    """F1.6 医生修改后确认（保留原文快照）"""
    if data is None:
        data = {}
    plan_json = data.get("plan_json")
    if plan_json is None:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "缺少 plan_json"})
    ok = _plan_repo.modify_plan(plan_id, plan_json, data.get("doctor_id"))
    emit({"type": "plan:modified", "data": {"plan_id": plan_id, "doctor_id": data.get("doctor_id")}})
    return {"ok": ok, "plan_id": plan_id, "status": "modified"}


@plan_router.post("/api/plans/batch-approve")
def api_batch_approve(data: Optional[dict] = Body(None)):
    """F1.5 批量同意：所有待审阅随访计划一次性同意，避免逐一点击"""
    if data is None:
        data = {}
    try:
        result = _plan_repo.batch_approve_plans(data.get("doctor_id"))
        try:
            emit({"type": "plan:batch_approved", "data": {"count": result["approved"]}})
        except Exception:
            pass
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail={"ok": False, "error": str(e)})
    return {"ok": True, **result}


@plan_router.get("/api/plans")
def api_list_all_plans():
    """批量获取所有患者的最新随访计划（启动时前端 initial load 用）"""
    plans = _plan_repo.list_all_latest_plans()
    return {"ok": True, "count": len(plans), "plans": plans}


@plan_router.get("/api/plans/{patient_id}")
def api_get_plan(patient_id: str):
    """取患者最新计划"""
    plan = _plan_repo.get_latest_plan(patient_id)
    if not plan:
        return {"ok": True, "plan": None}
    return {"ok": True, "plan": plan}
