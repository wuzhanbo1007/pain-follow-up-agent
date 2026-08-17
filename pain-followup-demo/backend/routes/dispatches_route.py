# backend/routes/dispatches_route.py
"""routes/dispatches_route.py —— 总调度 REST（说明书 13）。

POST /api/followups/dispatches            启动每日调度（DispatcherGraph）
GET  /api/followups/dispatches/{id}        读取调度投影（只查 Projection，不运行图）
POST /api/followups/callback-policies/compile   编译电话回访自然语言
POST /api/followups/callback-policies/{id}/approve 确认策略快照
"""
from __future__ import annotations

from fastapi import APIRouter, Body
from pydantic import BaseModel

from services.dispatch_service import DispatchService
from infrastructure.runtime_context import get_context

dispatches_router = APIRouter(prefix="/api/followups", tags=["followup-dispatch"])


class DispatchStartRequest(BaseModel):
    scope: str = "ward-A"
    callback_policy_version: str | None = None


@dispatches_router.post("/dispatches")
async def start_dispatch(req: DispatchStartRequest = Body(default_factory=DispatchStartRequest)):
    svc = DispatchService(get_context())
    report = await svc.start_dispatch(scope=req.scope,
                                       callback_policy_version=req.callback_policy_version)
    return report


@dispatches_router.get("/dispatches/{dispatch_id}")
async def get_dispatch(dispatch_id: str):
    svc = DispatchService(get_context())
    return await svc.query_projection(dispatch_id)


class CompilePolicyRequest(BaseModel):
    natural_language: str


@dispatches_router.post("/callback-policies/compile")
async def compile_policy(req: CompilePolicyRequest):
    svc = DispatchService(get_context())
    return await svc.compile_callback_policy(req.natural_language)


class ApprovePolicyRequest(BaseModel):
    natural_language: str
    approved_by: str = ""


@dispatches_router.post("/callback-policies/approve")
async def approve_policy(req: ApprovePolicyRequest):
    svc = DispatchService(get_context())
    return await svc.approve_callback_policy(req.natural_language, req.approved_by)
