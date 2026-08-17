# backend/domain/models/patient_report.py
"""汇报协议模型（说明书 10.1 / 4.1）。

PatientReport：单患者终态汇报；DispatchReport：总调度汇总。
均包含 callback_policy_version 与 decision_trace，可追溯。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RiskReport(BaseModel):
    level: str = "unknown"
    score: int | None = None
    policy_version: str = "pain-risk-v5"
    matched_rules: list[str] = Field(default_factory=list)
    base_score: int | None = None
    dynamic_score: int | None = None
    level_label: str = ""
    action: str = ""
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class CoverageReport(BaseModel):
    complete: bool = False
    collected: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class PatientReport(BaseModel):
    """单患者一次随访的终态汇报。"""
    episode_id: str
    patient_id: str
    name: str = ""
    status: Literal["completed", "incomplete_handoff", "human_handoff",
                    "failed", "cancelled", "waiting_reply"] = "completed"
    input_source: Literal["human", "simulator"] = "simulator"
    channel: Literal["wechat", "phone", "app"] = "wechat"
    session_id: str | None = None
    review_id: str | None = None
    risk: RiskReport = Field(default_factory=RiskReport)
    coverage: CoverageReport = Field(default_factory=CoverageReport)
    follow_up_needed: bool = False
    follow_up_reason: str = ""
    summary: str = ""
    callback_policy_version: str | None = None
    conversation_policy_version: str | None = None
    business_date: str = ""
    decision_trace: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    error: str | None = None

    model_config = {"extra": "ignore"}


class DispatchReport(BaseModel):
    """总调度汇总报告。"""
    dispatch_id: str
    business_date: str
    status: Literal["created", "calculating_roster", "applying_callback_policy",
                    "dispatching", "waiting_patients", "completed", "partial", "failed"] = "completed"
    counts: dict[str, int] = Field(default_factory=dict)
    reports_by_episode: dict[str, dict[str, Any]] = Field(default_factory=dict)
    callback_policy_version: str | None = None
    summary: str = ""

    model_config = {"extra": "ignore"}
