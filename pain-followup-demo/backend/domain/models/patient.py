# backend/domain/models/patient.py
"""患者快照模型。

PatientSnapshot：调度开始时由 PatientRepository.freeze_patient_snapshot 产出的不可变快照，
后续每个患者子图只引用该快照 + patient_id，不再按姓名重新查找（说明书 4.3）。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PatientBrief(BaseModel):
    """脱敏后可下发给前端 / 进入 Prompt 的患者摘要。"""
    patient_id: str
    name: str = ""
    age: int | None = None
    gender: str = ""
    diagnosis: str = ""
    pain_type: str = ""
    doctor_name: str = ""
    department_name: str = ""

    def as_prompt_context(self) -> str:
        parts = [f"姓名{self.name}"]
        if self.gender:
            parts.append(self.gender)
        if self.age is not None:
            parts.append(f"{self.age}岁")
        if self.diagnosis:
            parts.append(f"诊断{self.diagnosis}")
        if self.pain_type:
            parts.append(f"主要疼痛{self.pain_type}")
        return "".join(parts)


class PatientSnapshot(BaseModel):
    """一位患者的不可变运行快照。"""
    patient_id: str
    name: str = ""
    age: int | None = None
    gender: str = ""
    diagnosis: str = ""
    pain_type: str = ""
    doctor_name: str = ""
    department_name: str = "疼痛科"
    risk_level: str = "medium_risk"
    # 随访模式（说明书 6.1：followup_mode 改名 input_source）
    input_source: Literal["human", "simulator"] = "simulator"
    channel: Literal["wechat", "phone", "app"] = "wechat"
    # 通信偏好（供追问提示词调节语气）
    communication_preference: str = ""
    # 随访计划快照
    follow_up_plan: dict[str, Any] = Field(default_factory=dict)
    discharge_date: str | None = None
    duration_days: int = 90
    frequency: str = "每日"
    # 状态字段（用于名单判定与电话回访）
    followup_status: str = ""
    consent_status: str = "active"
    daily_status: str = ""
    consecutive_no_reply_days: int = 0
    # 历史随访记录（近 7 天），供风险与审阅对照
    history: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @property
    def brief(self) -> PatientBrief:
        return PatientBrief(
            patient_id=self.patient_id, name=self.name, age=self.age,
            gender=self.gender, diagnosis=self.diagnosis,
            pain_type=self.pain_type, doctor_name=self.doctor_name,
            department_name=self.department_name,
        )
