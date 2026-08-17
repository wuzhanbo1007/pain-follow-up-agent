# backend/domain/models/followup.py
"""随访调度与 episode 模型（说明书 9、10、附录 A）。

幂等主键（说明书 11.3）：
  dispatch_id = "dispatch:{date}:{scope}:{base_roster_version}:{callback_policy_version_or_none}"
  episode_id  = "episode:{dispatch_id}:{patient_id}"
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RosterDecision(BaseModel):
    """单患者在名单判定中的可解释决策。"""
    patient_id: str
    name: str = ""
    need_followup: bool
    mode: Literal["auto", "manual", "wechat", "phone", "app"] = "auto"
    channel: Literal["wechat", "phone", "app"] = "wechat"
    phone_callback: bool = False
    reason: str = ""
    matched_rule: str = ""
    source: Literal["config", "rule", "config_no_reply", "manual_assign"] = "rule"
    day_index: int | None = None
    decision_trace: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class DispatchRoster(BaseModel):
    """一次调度的应访/免访名单快照。"""
    total: int = 0
    send_list: list[dict[str, Any]] = Field(default_factory=list)
    skip_list: list[dict[str, Any]] = Field(default_factory=list)
    details: dict[str | int, Any] = Field(default_factory=dict)  # {patient_id: RosterDecision-as-dict}
    phone_callback_count: int = 0
    today: str = ""

    model_config = {"extra": "ignore"}


class FollowupEpisode(BaseModel):
    """一患者一次随访的 episode。"""
    episode_id: str
    dispatch_id: str
    patient_id: str
    name: str = ""
    input_source: Literal["human", "simulator"] = "simulator"
    channel: Literal["wechat", "phone", "app"] = "wechat"
    status: str = "created"
    thread_id: str = ""
    session_id: str | None = None
    review_id: str | None = None
    scenario_id: str | None = None

    model_config = {"extra": "ignore"}


class DispatchRun(BaseModel):
    """一次总调度运行。"""
    dispatch_id: str
    business_date: str
    timezone: str = "Asia/Shanghai"
    scope: str = "ward-A"
    callback_policy_version: str | None = None
    patient_snapshot_version: str = ""
    status: str = "created"
    counts: dict[str, int] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}
