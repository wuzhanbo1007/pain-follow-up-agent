# backend/infrastructure/langgraph/checkpointer.py
"""Checkpointer 工厂与线程配置（Agent-centric 重构 §6）。

只允许一个工厂选择 Checkpointer：
  - memory   → langgraph.checkpoint.memory.MemorySaver（进程内，演示可恢复语义）
  - postgres → langgraph.checkpoint.postgres.PostgresSaver.from_conn_string

依赖注入顺序：settings → get_checkpointer()；Graph 编译时统一
``compile(checkpointer=get_checkpointer(settings))``，禁止在节点内新建。
thread_config() 为同一个 thread_id 生成跨调用恢复配置。
"""
from __future__ import annotations

from typing import Any

try:  # pragma: no cover
    from langgraph.checkpoint.memory import MemorySaver
    HAS_CHECKPOINTER = True
except Exception:  # pragma: no cover
    MemorySaver = None  # type: ignore[assignment]
    HAS_CHECKPOINTER = False

# 进程内 memory checkpointer 单例：各图编译与 AppContext 共享同一实例，
# 保证同一 thread_id 跨图调用（start/resume）命中同一份 checkpoint。
_memory_singleton = None


def _memory_saver():
    global _memory_singleton
    if not HAS_CHECKPOINTER:
        raise RuntimeError("未安装 langgraph checkpointer，无法启用持久化")
    if _memory_singleton is None:
        _memory_singleton = MemorySaver()
    return _memory_singleton


def _postgres_saver(dsn: str):
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "checkpointer_backend=postgres 需要安装 langgraph-checkpoint-postgres") from exc
    return PostgresSaver.from_conn_string(dsn)


def get_checkpointer(settings: Any = None):
    """唯一 Checkpointer 工厂：按 settings.checkpointer_backend 选择实现。

    settings 缺省时回退进程内 MemorySaver（保持现有行为，测试/离线可用）。
    """
    backend = "memory"
    dsn = ""
    if settings is not None:
        backend = getattr(settings, "checkpointer_backend", "memory") or "memory"
        dsn = getattr(settings, "checkpointer_dsn", "") or ""
    if backend == "memory":
        return _memory_saver()
    if backend == "postgres":
        if not dsn:
            raise RuntimeError("checkpointer_backend=postgres 必须配置 checkpointer_dsn")
        return _postgres_saver(dsn)
    raise RuntimeError(f"未配置可用的 Checkpointer: backend={backend!r}")


def thread_config(thread_id: str) -> dict:
    """为一次运行生成 thread 配置（跨调用恢复同一图状态）。"""
    return {"configurable": {"thread_id": thread_id}}
