# backend/infrastructure/langgraph/__init__.py
"""LangGraph 持久化 / 恢复配置（Agent-centric 重构 §6）。

Checkpointer 与 thread 配置的唯一实现在 checkpointer.py；本文件仅做兼容再导出，
保证现有调用方（``from infrastructure.langgraph import get_checkpointer, thread_config``）
无需改动。生产可配置 checkpointer_backend=postgres 实现跨进程恢复。
"""
from __future__ import annotations

from infrastructure.langgraph.checkpointer import (
    HAS_CHECKPOINTER,
    get_checkpointer,
    thread_config,
)

__all__ = ["HAS_CHECKPOINTER", "get_checkpointer", "thread_config"]
