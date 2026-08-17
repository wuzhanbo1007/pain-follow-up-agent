# backend/infrastructure/repositories/review_repository.py
"""审阅仓储 —— §10.1 审阅状态机（进程内投影）。

状态机：episode_completed → ai_review_pending → ai_review_ready → doctor_reviewed

记录字段（§10.1）：review_id, review_key, episode_id, dispatch_id, review_source,
status, ai_review_json, doctor_review_json, reviewed_by, session_id。

AI 审阅按 ``review_key = ai-review:{episode_id}`` 幂等创建（create_ai_pending_once），
重复投递/重试只返回 False，不再重复生成审阅。医生提交（submit_doctor_review）
只更新医生字段（doctor_review_json / reviewed_by），绝不覆盖 ai_review_json。

默认进程内实现（演示环境无需改动共享 MySQL schema；医生面板的 MySQL 审阅行
由 ReviewAgent 写穿兼容，本仓储是权威状态机）。
"""
from __future__ import annotations

import threading
import time

from infrastructure.repositories.errors import NotFoundError


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class ReviewRepository:
    """AI/医生审阅状态机投影（进程内实现，接口与 §10.1 字段一致）。"""

    def __init__(self):
        self._reviews: dict[str, dict] = {}   # review_key -> review record
        self._by_session: dict[str, str] = {}  # session_id -> review_key
        self._guard = threading.RLock()
        self._seq = 0

    # ---- 幂等创建（AI 审阅只触发一次）----
    def create_ai_pending_once(self, *, review_key: str, episode_id: str,
                               dispatch_id: str, session_id=None) -> bool:
        """§10.1：按 review_key 幂等创建 ai_review_pending；已存在返回 False。"""
        with self._guard:
            if review_key in self._reviews:
                return False
            self._seq += 1
            self._reviews[review_key] = {
                "review_id": f"review:{self._seq}",
                "review_key": review_key,
                "episode_id": episode_id,
                "dispatch_id": dispatch_id,
                "session_id": session_id,
                "review_source": "ai",
                "status": "ai_review_pending",
                "ai_review_json": None,
                "doctor_review_json": None,
                "reviewed_by": None,
                "created_at": _now(),
                "updated_at": _now(),
            }
            if session_id:
                self._by_session[str(session_id)] = review_key
            return True

    # ---- 查询 ----
    def get(self, review_key: str) -> dict | None:
        with self._guard:
            r = self._reviews.get(review_key)
            return dict(r) if r is not None else None

    def get_required(self, review_key: str) -> dict:
        r = self.get(review_key)
        if r is None:
            raise NotFoundError(f"review not found: {review_key}")
        return r

    def get_by_session(self, session_id) -> dict | None:
        """按 session 定位审阅记录（医生面板 session_id → review_key）。"""
        with self._guard:
            key = self._by_session.get(str(session_id))
            if not key:
                return None
            r = self._reviews.get(key)
            return dict(r) if r is not None else None

    # ---- 状态迁移 ----
    def attach_ai_review(self, review_key: str, ai_review_json: dict) -> dict | None:
        """AI 审阅结果落库：ai_review_pending → ai_review_ready；只写 ai_review_json。"""
        with self._guard:
            r = self._reviews.get(review_key)
            if r is None:
                return None
            r["ai_review_json"] = ai_review_json
            r["status"] = "ai_review_ready"
            r["updated_at"] = _now()
            return dict(r)

    def submit_doctor_review(self, *, review_key: str,
                             doctor_review_json: dict,
                             reviewed_by: str | None = None) -> dict:
        """医生提交：ai_review_ready → doctor_reviewed。

        只更新医生字段（doctor_review_json / reviewed_by），不覆盖 ai_review_json。
        """
        with self._guard:
            r = self._reviews.get(review_key)
            if r is None:
                raise NotFoundError(f"review not found: {review_key}")
            r["doctor_review_json"] = doctor_review_json
            r["reviewed_by"] = reviewed_by
            r["status"] = "doctor_reviewed"
            r["updated_at"] = _now()
            return dict(r)

    # ---- 兼容旧路径（医生面板按 review_id 更新 AI JSON）----
    def attach_ai_review_by_id(self, review_id: str, ai_review_json: dict) -> None:
        """写穿到 MySQL 审阅行的兼容方法（由 ReviewAgent 对旧面板行调用）。"""
        from db.followup_db import attach_ai_review
        try:
            attach_ai_review(review_id, ai_review_json)
        except Exception as exc:  # pragma: no cover - 演示环境无 MySQL 时降级
            from infrastructure.repositories.errors import PersistenceError
            raise PersistenceError(f"attach_ai_review 失败: {exc}") from exc
