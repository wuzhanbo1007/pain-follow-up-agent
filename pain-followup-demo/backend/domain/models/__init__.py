# backend/domain/models/__init__.py
"""领域模型（Pydantic 输入/输出契约）。

每个模型是某一节点的输入或输出 Schema，被 Agent / 节点 / Repository 共享。
字段约束（ge/le/enum）同时用于校验，保证进入 State 关键字段的值合法。
"""
from domain.models.patient import PatientSnapshot, PatientBrief
from domain.models.followup import (
    FollowupEpisode, DispatchRun, DispatchRoster, RosterDecision,
)
from domain.models.callback_policy import CallbackPolicy, CallbackPolicyDraft
from domain.models.reply_understanding import ReplyUnderstanding
from domain.models.patient_report import PatientReport, DispatchReport
from domain.models.review import ReviewDraft

__all__ = [
    "PatientSnapshot", "PatientBrief",
    "FollowupEpisode", "DispatchRun", "DispatchRoster", "RosterDecision",
    "CallbackPolicy", "CallbackPolicyDraft",
    "ReplyUnderstanding",
    "PatientReport", "DispatchReport",
    "ReviewDraft",
]
