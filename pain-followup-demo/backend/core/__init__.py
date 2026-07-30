"""
Core 模块 — 全局配置、日志、实时推送
"""
from .config import (
    API_HOST, API_PORT, API_DEBUG,
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT,
    DEMO_TODAY,
)
from .logging_config import configure_logging, get_logger
from .realtime import emit, set_socketio, capture_loop

__all__ = [
    "API_HOST", "API_PORT", "API_DEBUG",
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_TIMEOUT",
    "DEMO_TODAY",
    "configure_logging", "get_logger",
    "emit", "set_socketio",
]
