# backend/infrastructure/repositories/patient_repository.py
"""患者仓储 —— 封装 data.database.PatientDB，提供不可变快照（说明书 4.3）。

调度开始时 PatientRepository.freeze_patient_snapshot() 生成不可变快照，
后续每个患者子图只引用该快照 + patient_id，不再按姓名重新查找。
"""
from __future__ import annotations

from typing import Any

from domain.models.patient import PatientSnapshot


class PatientRepository:
    """患者数据仓储（复用现有 MySQL PatientDB）。"""

    def __init__(self, db=None):
        self._db = db

    def _db_or_new(self):
        if self._db is not None:
            return self._db
        from data.database import PatientDB
        self._db = PatientDB()
        return self._db

    def get_all_patients(self) -> list[dict[str, Any]]:
        return self._db_or_new().get_all_patients()

    def get_patient(self, patient_id: str) -> dict[str, Any] | None:
        return self._db_or_new().get_patient(patient_id)

    def get_history(self, patient_id: str, days: int = 7) -> list[dict[str, Any]]:
        return self._db_or_new().get_history(patient_id, days)

    def get_reply(self, patient_id: str) -> dict[str, Any] | None:
        """患者最近一次随访回复（PatientDB.get_all_replies 查最近一条）。"""
        return self._db_or_new().get_reply(patient_id)

    def get_all_replies(self) -> dict[str | int, dict[str, Any]]:
        """全量最近回复 {patient_id: {...}}（旧 runtime.REPLIES 同形，键为 DB 原值）。"""
        return self._db_or_new().get_all_replies()

    def freeze_patient_snapshot(self, patient: dict[str, Any],
                                *, input_source: str = "simulator") -> PatientSnapshot:
        """从患者 dict 构建不可变快照（补齐历史）。

        §6 / §11-11：input_source 由调用方（调度 State）显式传入，不读取
        患者表中的 followup_mode 字段——input_source 只作为本次 Episode 的
        运行时信息，不作为患者主数据字段。
        §11-10：channel 优先取名单决定的 channel（电话回访=phone），不固定为 wechat。
        """
        p = dict(patient)
        history = p.pop("history", None)
        if history is None:
            history = self.get_history(p.get("patient_id"), 7)
        effective_source = "human" if input_source == "human" else "simulator"
        # §11-10：channel 优先取名单决定的 channel（电话回访=phone），不固定为 wechat
        channel = (p.get("channel") or p.get("preferred_channel") or "wechat")
        return PatientSnapshot(
            patient_id=str(p.get("patient_id")),
            name=p.get("name", ""),
            age=p.get("age"),
            gender=p.get("gender", ""),
            diagnosis=p.get("diagnosis", ""),
            pain_type=p.get("pain_type", ""),
            doctor_name=p.get("doctor_name", ""),
            department_name=p.get("department_name") or "疼痛科",
            risk_level=p.get("risk_level", "medium_risk"),
            input_source=effective_source,
            channel=channel,
            communication_preference=p.get("communication_preference", ""),
            follow_up_plan=p.get("follow_up_plan") or {},
            discharge_date=p.get("discharge_date"),
            duration_days=p.get("duration_days", 90),
            frequency=(p.get("follow_up_plan") or {}).get("frequency", "每日"),
            followup_status=p.get("followup_status", ""),
            consent_status=p.get("consent_status", "active"),
            daily_status=p.get("daily_status", ""),
            consecutive_no_reply_days=int(p.get("consecutive_no_reply_days") or 0),
            history=history,
        )

    def snapshot_to_runtime_dict(self, snap: PatientSnapshot) -> dict[str, Any]:
        """快照转回供旧引擎/图使用的 dict（带 history / followup_mode）。"""
        return {
            "patient_id": snap.patient_id,
            "name": snap.name,
            "age": snap.age,
            "gender": snap.gender,
            "diagnosis": snap.diagnosis,
            "pain_type": snap.pain_type,
            "doctor_name": snap.doctor_name,
            "department_name": snap.department_name,
            "risk_level": snap.risk_level,
            "followup_mode": "manual" if snap.input_source == "human" else "auto",
            "discharge_date": snap.discharge_date,
            "duration_days": snap.duration_days,
            "follow_up_plan": snap.follow_up_plan,
            "consecutive_no_reply_days": snap.consecutive_no_reply_days,
            "daily_status": snap.daily_status,
            "followup_status": snap.followup_status,
            "history": snap.history,
        }
