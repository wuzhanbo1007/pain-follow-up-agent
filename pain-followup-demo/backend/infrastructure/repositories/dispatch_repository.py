# backend/infrastructure/repositories/dispatch_repository.py
"""dispatch_runs 投影仓储（Agent-centric 重构 §6）。

Projection 不能只依赖 Graph checkpoint：dispatch_runs 记录一次总调度的
状态与完成/等待/失败计数，供 GET 查询返回真实数据（不再是占位文案）。

实现说明：默认使用进程内投影（演示环境无需改动共享 MySQL schema，
对应 DDL 见 scripts/migrate_db.py：dispatch_runs 表）。生产切换 MySQL 时
保持同一接口，仅替换内部存储。

错误约定（§11）：显式区分"找不到 / 冲突 / 持久化失败"。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from infrastructure.repositories import errors


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class DispatchRepository:
    """总调度投影仓储（进程内实现，接口与 dispatch_runs 表一致）。"""

    def __init__(self):
        self._runs: dict[str, dict] = {}
        self._locks: set[str] = set()
        self._guard = threading.RLock()

    # ---- 投影读写 ----

    async def create(self, dispatch_id: str, *, status: str = "created",
                     payload: dict | None = None,
                     counts: dict | None = None) -> dict:
        counts = counts or {}
        run = {
            "dispatch_id": dispatch_id,
            "status": status,
            "total_count": int(counts.get("total_agents") or counts.get("total") or 0),
            "completed_count": int(counts.get("completed") or 0),
            "waiting_count": int(counts.get("waiting") or 0),
            "failed_count": int(counts.get("failed") or 0),
            "payload": payload or {},
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self._guard:
            self._runs[dispatch_id] = run
        return dict(run)

    async def update_status(self, dispatch_id: str, *, status: str,
                            counts: dict | None = None) -> dict | None:
        with self._guard:
            run = self._runs.get(dispatch_id)
            if run is None:
                return None
            counts = counts or {}
            run["status"] = status
            run["total_count"] = int(counts.get("total_agents") or counts.get("total") or 0)
            run["completed_count"] = int(counts.get("completed") or 0)
            run["waiting_count"] = int(counts.get("waiting") or 0)
            run["failed_count"] = int(counts.get("failed") or 0)
            run["updated_at"] = _now()
            return dict(run)

    async def get(self, dispatch_id: str) -> dict | None:
        with self._guard:
            run = self._runs.get(dispatch_id)
            return dict(run) if run is not None else None

    async def get_required(self, dispatch_id: str) -> dict:
        run = await self.get(dispatch_id)
        if run is None:
            raise errors.NotFoundError(f"dispatch not found: {dispatch_id}")
        return run

    async def get_latest(self) -> dict | None:
        """最近一次调度投影（按 created_at 取最新）。"""
        with self._guard:
            if not self._runs:
                return None
            run = max(self._runs.values(), key=lambda r: r.get("created_at", ""))
            return dict(run)

    # ---- 跨请求锁（§11：不能用实例变量当锁；统一走仓储）----
    def acquire_lock(self, dispatch_id: str) -> bool:
        """进程内互斥锁（同一进程内防止并发重复调度；跨进程需 Redis/DB 唯一键）。"""
        with self._guard:
            if dispatch_id in self._locks:
                return False
            self._locks.add(dispatch_id)
            return True

    def release_lock(self, dispatch_id: str) -> None:
        with self._guard:
            self._locks.discard(dispatch_id)
