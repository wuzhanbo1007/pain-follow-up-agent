# backend/app.py
"""
FastAPI + WebSocket 主服务 — 慢性疼痛随访智能体后端

职责收敛：应用创建、CORS、路由/事件注册、启动预热、uvicorn 入口。
具体启动装配（DB/容器/状态）见 core.bootstrap；REST 路由见 routes/patients；
WebSocket 事件见 routes/ws；多 Agent / RAG 路由见 routes.{plan,review,knowledge}。
"""
import os
import sys
import time as _time

import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 确保模块可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import config
from core.logging_config import configure_logging, get_logger

# ======== 日志初始化 ========
configure_logging("painsmart")
log = get_logger("painsmart")

from core.realtime import set_socketio
from core.bootstrap import bootstrap
from knowledge.retriever import warm_store

# ======== 创建 FastAPI 应用 ========
app = FastAPI(title="LANShing慢性疼痛随访智能体", version="9.0")

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Socket.IO ASGI 服务
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
socketio_app = socketio.ASGIApp(sio, other_asgi_app=app)

# 注入 sio 引用，供 Agent 包通过 WebSocket 推送事件（plan/review）
set_socketio(sio)

# ======== 启动引导：填充 runtime 共享状态 ========
_t0 = _time.time()
bootstrap()
log.info("启动引导完成 (%.1fs)", _time.time() - _t0)

# ======== 路由 / 事件注册 ========
# 守卫：若 langgraph 等重依赖未安装，旧 Demo 仍能正常运行，
# 仅 RAG / 多 Agent 相关接口不可用。
try:
    from knowledge.api_routes import knowledge_router
    from routes.plan import plan_router
    from routes.review import review_router
    from routes.patients import patients_router
    from routes.dispatches_route import dispatches_router
    from routes.episodes_route import episodes_router
    from routes.ws import register_ws_events

    app.include_router(knowledge_router)
    app.include_router(plan_router)
    app.include_router(review_router)
    app.include_router(patients_router)
    app.include_router(dispatches_router)
    app.include_router(episodes_router)
    register_ws_events(sio)
    log.info("多 Agent / RAG / REST / WS 路由已注册: "
             "/api/knowledge /api/plans /api/reviews /api/patients "
             "/api/followups(dispatches|episodes) / WS 事件")
except Exception as e:
    log.warning("多 Agent / RAG / REST / WS 路由未注册（缺少依赖？）: %s", e)


# ======== 启动 ========
if __name__ == "__main__":
    import uvicorn

    # 预热 RAG 检索单例
    _rt0 = _time.time()
    warm_store()
    log.info("RAG 预热完成 (%.1fs)", _time.time() - _rt0)

    log.info("慢性疼痛随访智能体 - 后端服务启动")
    log.info("FastAPI + WebSocket: http://localhost:%d", config.API_PORT)
    log.info("数据库: MySQL @ %s:%s/%s", config.DB_HOST, config.DB_PORT, config.DB_NAME)
    log.info("LLM: %s  Key: %s", config.LLM_MODEL,
             "已配置" if config.LLM_API_KEY else "未配置（降级）")
    log.info("启动总耗时 %.1fs", _time.time() - _t0)

    uvicorn.run(
        socketio_app,
        host=config.API_HOST,
        port=config.API_PORT,
        reload=False,  # Windows WebSocket 必须关闭 reloader，避免 WinError 10038
    )
