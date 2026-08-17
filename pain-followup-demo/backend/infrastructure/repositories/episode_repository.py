# backend/infrastructure/repositories/episode_repository.py
"""episode_runs 投影仓储（Agent-centric 重构 §6）。

每个 Episode 用独立 thread_id 运行；episode_runs 记录状态/风险/报告，
供恢复、查询与 Dispatch 汇总。默认进程内实现（DDL 见 scripts/migrate_db.py）。

幂等（§6.3）：关键写入使用唯一幂等键，重复恢复/重试/重复投递不产生
重复消息、报告、审阅和告警。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from infrastructure.repositories import errors


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class EpisodeRepository:
    """单患者 Episode 投影仓储（进程内实现，接口与 episode_runs 表一致）。"""

    def __init__(self):
        self._episodes: dict[str, dict] = {}
        self._guard = threading.RLock()
        self._idempotency: set[str] = set()

    # ---- 投影读写 ----

    async def create(self, *, episode_id: str, dispatch_id: str, patient_id: str,
                     status: str = "pending", payload: dict | None = None) -> dict:
        ep = {
            "episode_id": episode_id,
            "dispatch_id": dispatch_id,
            "patient_id": patient_id,
            "status": status,
            "risk_result": None,
            "report": None,
            "last_error": None,
            "payload": payload or {},
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self._guard:
            self._episodes[episode_id] = ep
        return dict(ep)

    async def get(self, episode_id: str) -> dict | None:
        with self._guard:
            ep = self._episodes.get(episode_id)
            return dict(ep) if ep is not None else None

    async def get_required(self, episode_id: str) -> dict:
        ep = await self.get(episode_id)
        if ep is None:
            raise errors.NotFoundError(f"episode not found: {episode_id}")
        return ep

    async def save_progress(self, state: dict) -> dict | None:
        """从 PatientState 落进度（status / risk_result / report / 错误）。"""
        episode_id = state.get("episode_id")
        if not episode_id:
            return None
        with self._guard:
            ep = self._episodes.get(episode_id)
            if ep is None:
                return None
            if state.get("status"):
                ep["status"] = state["status"]
            if state.get("risk_result"):
                ep["risk_result"] = state["risk_result"]
            if state.get("report"):
                ep["report"] = state["report"]
            if state.get("errors"):
                errs = state["errors"]
                ep["last_error"] = (errs[-1] if isinstance(errs, list) and errs
                                    else errs)
            ep["updated_at"] = _now()
            return dict(ep)

    async def update_status(self, episode_id: str, status: str) -> dict | None:
        with self._guard:
            ep = self._episodes.get(episode_id)
            if ep is None:
                return None
            ep["status"] = status
            ep["updated_at"] = _now()
            return dict(ep)

    async def mark_failed(self, episode_id: str, error: Any) -> dict | None:
        with self._guard:
            ep = self._episodes.get(episode_id)
            if ep is None:
                return None
            ep["status"] = "failed"
            ep["last_error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            ep["updated_at"] = _now()
            return dict(ep)

    # ---- 幂等完成（§6.3 complete_once）----
    def insert_if_absent(self, key: str) -> bool:
        """幂等键去重：首次插入返回 True，重复返回 False。"""
        with self._guard:
            if key in self._idempotency:
                return False
            self._idempotency.add(key)
            return True

    async def complete_once(self, episode_id: str, report: dict, key: str,
                            status: str | None = None) -> bool:
        """只完成一次：幂等键通过才写报告并标记终态（重试/重复投递不重复写）。

        status 缺省取 report.status（completed / human_handoff / incomplete_handoff），
        保证投影记录真实终态，不把转人工硬编码成 completed。
        """
        if not self.insert_if_absent(key):
            return False
        with self._guard:
            ep = self._episodes.get(episode_id)
            if ep is None:
                return False
            ep["status"] = status or report.get("status") or "completed"
            ep["report"] = report
            ep["risk_result"] = report.get("risk", ep.get("risk_result"))
            ep["updated_at"] = _now()
        return True

    async def list_by_dispatch(self, dispatch_id: str) -> list[dict]:
        """返回该 dispatch 下的全部 episode 投影（供统计聚合）。"""
        with self._guard:
            return [dict(ep) for ep in self._episodes.values()
                    if ep.get("dispatch_id") == dispatch_id]

    async def count_episode_statuses(self, dispatch_id: str) -> dict:
        """按 dispatch 汇总：total / completed / waiting / failed / handoff / unfinished。

        completed 含所有终态（completed / human_handoff / incomplete_handoff）；
        终态外的状态（pending / running / dispatching）视为未完成。
        """
        counts = {"total": 0, "completed": 0, "waiting": 0,
                  "failed": 0, "handoff": 0, "unfinished": 0}
        terminal = {"completed", "human_handoff", "incomplete_handoff"}
        with self._guard:
            for ep in self._episodes.values():
                if ep.get("dispatch_id") != dispatch_id:
                    continue
                status = ep.get("status", "")
                counts["total"] += 1
                if status in terminal:
                    counts["completed"] += 1
                    if status != "completed":
                        counts["handoff"] += 1
                elif status in ("waiting_reply", "waiting_human", "waiting_review"):
                    counts["waiting"] += 1
                    counts["unfinished"] += 1
                elif status == "failed":
                    counts["failed"] += 1
                    counts["unfinished"] += 1
                else:  # pending / running / dispatching
                    counts["unfinished"] += 1
        return counts
