# backend/routes/ws.py
"""
WebSocket 事件处理（说明书 13 / 15.3 事件契约）

输入事件（前端 → 后端）：
  dispatch:start      启动每日调度（DispatcherGraph）
  episode:resume      提交手动患者回复（PatientGraph + Command(resume)）
  chat:request_sync   请求当前调度投影/消息同步（只读，无副作用）

输出事件（后端 → 前端）由 Outbox → EventBus → core.realtime.emit 推送，
本文件不再直接 emit 业务事件：
  dispatch:started / dispatch:state_changed / dispatch:roster_ready /
  dispatch:report_ready / episode:state_changed / episode:message /
  episode:decision /
  episode:waiting_human / episode:report_ready / review:ready

说明：旧 demo:* / agent:* 演示事件处理器已移除（ARCHITECTURE_REFACTOR 第二阶段）。
connect 仅捕获主事件循环，不再访问已退役的 followup_service。
"""
import asyncio

from core import config
from core.logging_config import get_logger

log = get_logger("painsmart.ws")


def register_ws_events(sio):
    @sio.event
    async def connect(sid, environ):
        from core.realtime import capture_loop
        capture_loop()  # ★ 捕获主事件循环，使后续 Outbox 推送可用
        log.debug("WebSocket 客户端已连接: %s", sid)
        await sio.emit("connection:ready", {
            "status": "ok",
            "db_type": "mysql",
            "db_name": config.DB_NAME,
        }, to=sid)

    @sio.on("dispatch:start")
    async def handle_dispatch_start(sid, data):
        """启动每日调度（说明书 13 / 15.3 dispatch:start）。"""
        from services.dispatch_service import DispatchService
        from infrastructure.runtime_context import get_context
        data = data or {}
        scope = data.get("scope", "ward-A")
        callback_policy_version = data.get("callback_policy_version")
        svc = DispatchService(get_context())
        return svc.start_dispatch_background(scope=scope,
                                             callback_policy_version=callback_policy_version)

    @sio.on("episode:resume")
    async def handle_episode_resume(sid, data):
        """提交手动患者回复，恢复同一 episode（说明书 6.3 / 13）。"""
        from services.dispatch_service import DispatchService
        from infrastructure.runtime_context import get_context
        data = data or {}
        episode_id = data.get("episode_id")
        reply_text = data.get("text") or data.get("reply_text") or "今天还好"
        if not episode_id:
            return {"status": "error", "message": "缺少 episode_id"}
        svc = DispatchService(get_context())

        async def _do():
            try:
                await svc.resume_episode(episode_id, reply_text)
            except Exception as exc:
                log.warning("episode:resume 后台失败 %s: %s", episode_id, exc)

        asyncio.create_task(_do())
        return {"status": "started", "episode_id": episode_id}

    @sio.on("chat:request_sync")
    async def handle_chat_sync(sid):
        """请求当前调度投影（只读，无副作用）。

        前端重连后用于补齐已错过的调度/消息状态；不运行图、不修改状态。
        """
        from services.dispatch_service import DispatchService
        from infrastructure.runtime_context import get_context
        svc = DispatchService(get_context())
        projection = await svc.query_projection(None)
        await sio.emit("sync:projection", projection, to=sid)