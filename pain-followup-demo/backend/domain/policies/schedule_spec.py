# backend/domain/policies/schedule_spec.py
"""随访会话策略（ScheduleSpec）与对话策略（ConversationPolicy）。

说明书 8.2：Policy 定义需采集项、最大轮次、置信度；
说明书 6.1：conversation_policy_version 定义会话行为。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# 四项关键信息槽位（与现有系统一致）
SLOT_KEYS: tuple[str, ...] = ("pain_nrs", "sleep_quality", "medication_taken", "side_effects")

SLOT_LABELS: dict[str, str] = {
    "pain_nrs": "疼痛评分",
    "sleep_quality": "睡眠质量",
    "medication_taken": "用药依从性",
    "side_effects": "副作用",
}

# 槽位采集优先级（说明书 8.5：TurnRouter 选优先级最高的 missing slot 生成追问）
SLOT_PRIORITY: tuple[str, ...] = ("pain_nrs", "sleep_quality", "medication_taken", "side_effects")


class ConversationPolicy(BaseModel):
    """定义一次会话的采集项、最大轮次、置信度阈值。

    版本号随提示词版本一起落库，可追溯。
    """
    version: str = "conv-v1"
    required_slots: tuple[str, ...] = SLOT_KEYS
    max_turns: int = 20          # 安全上限（防止机械追问）
    auto_max_turns: int = 20      # 自动患者（模拟器）上限，与人工随访一致
    min_confidence: float = 0.6  # 低于此置信度标记 uncertain
    high_risk_score_threshold: int = 8  # 高风险评分阈值（与 RiskPolicy 一致）

    model_config = {"extra": "ignore"}

    def slot_label(self, key: str) -> str:
        return SLOT_LABELS.get(key, key)

    def next_missing_slot(self, slots: dict) -> str | None:
        """按优先级返回第一个缺失（None/空）的槽位。"""
        for k in SLOT_PRIORITY:
            v = slots.get(k)
            if v in (None, "", []):
                return k
        return None


class ScheduleSpec(BaseModel):
    """随访频次结构化规则（说明书 14.2：followup_scheduler 拆分）。

    自然语言频次只在计划审批前编译为 schedule_spec；运行时只做标准日程求值。
    """
    type: Literal["daily", "interval", "once"] = "daily"
    interval_days: int | None = None
    day: int | None = None  # once 模式：第 N 天随访一次
    frequency_text: str = "每日"

    model_config = {"extra": "ignore"}
