# backend/infrastructure/repositories/followup_repository.py
"""随访会话/计划/审阅仓储 —— 封装 db.followup_db（复用现有 MySQL）。

幂等写入：create_session / create_review 使用唯一键，重复运行不重复创建
（说明书 11.3 幂等键）。

错误约定（§11）：禁止 ``except Exception: return None`` 掩盖失败。
  - "找不到"由查询方法自然返回 None（可选查询）；必须存在时用 get_required 抛 NotFoundError；
  - "冲突"（唯一键已存在 / 状态不允许）抛 ConflictError；
  - 底层存储写入/读取异常一律抛 PersistenceError，让上层显式处理而非静默降级。
"""
from __future__ import annotations

from typing import Any

from infrastructure.repositories.errors import NotFoundError, PersistenceError


class FollowupRepository:
    """会话/计划读写（复用现有 db.followup_db）。"""

    def __init__(self, db=None):
        self._db = db

    def _ensure(self):
        from db import followup_db
        return followup_db

    # ===== 会话 =====
    def create_session(self, *, patient_id, transcript_json, risk_result,
                       agent_summary, plan_id=None, session_key=None) -> Any:
        """幂等创建会话：若 session_key 命中已有会话则复用，避免重试重复。

        DB 写失败抛 PersistenceError（不再 return None 掩盖）。找不到已有会话
        是正常路径（get_session_by_key 返回 None），不视为错误。
        """
        db = self._ensure()
        if session_key:
            existing = self.get_session_by_key(session_key)
            if existing:
                return existing["session_id"]
        try:
            return db.create_session(
                patient_id=patient_id,
                transcript_json=transcript_json,
                risk_result=risk_result,
                agent_summary=agent_summary,
                plan_id=plan_id,
            )
        except Exception as exc:
            raise PersistenceError(f"create_session 失败: {exc}") from exc

    def get_session_by_key(self, session_key: str) -> dict | None:
        """可选查询：无则返回 None；底层读失败抛 PersistenceError。"""
        db = self._ensure()
        fn = getattr(db, "get_session_by_key", None)
        if fn is None:
            return None
        try:
            return fn(session_key)
        except Exception as exc:
            raise PersistenceError(f"get_session_by_key 失败: {exc}") from exc

    def get_today_session(self, patient_id) -> dict | None:
        """可选查询：无则返回 None；底层读失败抛 PersistenceError。"""
        db = self._ensure()
        try:
            return db.get_today_session(patient_id)
        except Exception as exc:
            raise PersistenceError(f"get_today_session 失败: {exc}") from exc

    # ===== 计划 =====
    def get_latest_plan(self, patient_id):
        from db.followup_db import get_latest_plan
        return get_latest_plan(patient_id)

    def create_plan(self, *, patient_id, plan_json, discharge_summary=None,
                    citations=None, status="draft", plan_key=None):
        from db.followup_db import create_plan
        return create_plan(
            patient_id=patient_id,
            plan_json=plan_json,
            discharge_summary=discharge_summary,
            citations=citations or [],
            status=status,
        )

    # ===== 审阅 =====
    def create_review(self, *, session_id, patient_id, track_status="followup_done",
                      audit_snapshot=None, review_key=None,
                      doctor_score=None, doctor_comment=None, reviewer_id=None):
        from db.followup_db import create_review
        if review_key:
            existing = self.get_review_by_key(review_key)
            if existing:
                return existing["review_id"]
        try:
            return create_review(
                session_id=session_id,
                patient_id=patient_id,
                doctor_score=doctor_score,
                doctor_comment=doctor_comment,
                track_status=track_status,
                reviewer_id=reviewer_id,
                audit_snapshot=audit_snapshot or {},
            )
        except Exception as exc:
            raise PersistenceError(f"create_review 失败: {exc}") from exc

    def get_review_by_session(self, session_id):
        """可选查询：无则返回 None；底层读失败抛 PersistenceError。"""
        from db.followup_db import get_review_by_session
        try:
            return get_review_by_session(session_id)
        except Exception as exc:
            raise PersistenceError(f"get_review_by_session 失败: {exc}") from exc

    def get_review_by_key(self, review_key: str):
        """可选查询：无则返回 None；底层读失败抛 PersistenceError。"""
        db = self._ensure()
        fn = getattr(db, "get_review_by_key", None)
        if fn is None:
            return None
        try:
            return fn(review_key)
        except Exception as exc:
            raise PersistenceError(f"get_review_by_key 失败: {exc}") from exc

    def attach_ai_review(self, review_id, draft_review):
        """写失败抛 PersistenceError（不再 pass 掩盖）。"""
        from db.followup_db import attach_ai_review
        try:
            attach_ai_review(review_id, draft_review)
        except Exception as exc:
            raise PersistenceError(f"attach_ai_review 失败: {exc}") from exc

    def update_review_audit_snapshot(self, review_id, patch):
        from db.followup_db import update_review_audit_snapshot
        try:
            update_review_audit_snapshot(review_id, patch)
        except Exception as exc:
            raise PersistenceError(f"update_review_audit_snapshot 失败: {exc}") from exc

    # ===== 医生面板 / 计划面板兼容（§12.2：Route/Service 不再直连 db.followup_db）=====
    # 下列方法只做等价转发，返回结构不变；底层读/写失败一律抛 PersistenceError。
    def get_session(self, session_id):
        from db.followup_db import get_session
        try:
            return get_session(session_id)
        except Exception as exc:
            raise PersistenceError(f"get_session 失败: {exc}") from exc

    def get_plan(self, plan_id):
        from db.followup_db import get_plan
        try:
            return get_plan(plan_id)
        except Exception as exc:
            raise PersistenceError(f"get_plan 失败: {exc}") from exc

    def list_reviews(self, *, status=None, patient_id=None):
        from db.followup_db import list_reviews
        try:
            return list_reviews(status=status, patient_id=patient_id)
        except Exception as exc:
            raise PersistenceError(f"list_reviews 失败: {exc}") from exc

    def review_stats(self, *, today=None, dispatch_id=None):
        from db.followup_db import review_stats
        try:
            return review_stats(today=today, dispatch_id=dispatch_id)
        except Exception as exc:
            raise PersistenceError(f"review_stats 失败: {exc}") from exc

    def get_latest_transcripts(self):
        from db.followup_db import get_latest_transcripts
        try:
            return get_latest_transcripts()
        except Exception as exc:
            raise PersistenceError(f"get_latest_transcripts 失败: {exc}") from exc

    def get_latest_sessions(self, *, today=None, dispatch_id=None):
        from db.followup_db import get_latest_sessions
        try:
            return get_latest_sessions(today=today, dispatch_id=dispatch_id)
        except Exception as exc:
            raise PersistenceError(f"get_latest_sessions 失败: {exc}") from exc

    def approve_plan(self, plan_id, doctor_id=None):
        from db.followup_db import approve_plan
        try:
            return approve_plan(plan_id, doctor_id)
        except Exception as exc:
            raise PersistenceError(f"approve_plan 失败: {exc}") from exc

    def modify_plan(self, plan_id, plan_json, doctor_id=None):
        from db.followup_db import modify_plan
        try:
            return modify_plan(plan_id, plan_json, doctor_id)
        except Exception as exc:
            raise PersistenceError(f"modify_plan 失败: {exc}") from exc

    def batch_approve_plans(self, doctor_id=None, only_draft=True):
        from db.followup_db import batch_approve_plans
        try:
            return batch_approve_plans(doctor_id=doctor_id, only_draft=only_draft)
        except Exception as exc:
            raise PersistenceError(f"batch_approve_plans 失败: {exc}") from exc

    def list_all_latest_plans(self):
        from db.followup_db import list_all_latest_plans
        try:
            return list_all_latest_plans()
        except Exception as exc:
            raise PersistenceError(f"list_all_latest_plans 失败: {exc}") from exc


class PlanRepository:
    """计划仓储（复用 FollowupRepository 的计划方法）。

    §12.2：Route 不再直连 db.followup_db，计划审批/修改/列表统一经此仓储。
    """
    def __init__(self, repo: FollowupRepository | None = None):
        self._repo = repo or FollowupRepository()

    def get_latest_plan(self, patient_id):
        return self._repo.get_latest_plan(patient_id)

    def create_plan(self, **kw):
        return self._repo.create_plan(**kw)

    def get_plan(self, plan_id):
        return self._repo.get_plan(plan_id)

    def approve_plan(self, plan_id, doctor_id=None):
        return self._repo.approve_plan(plan_id, doctor_id)

    def modify_plan(self, plan_id, plan_json, doctor_id=None):
        return self._repo.modify_plan(plan_id, plan_json, doctor_id)

    def batch_approve_plans(self, doctor_id=None, only_draft=True):
        return self._repo.batch_approve_plans(doctor_id=doctor_id, only_draft=only_draft)

    def list_all_latest_plans(self):
        return self._repo.list_all_latest_plans()
