# backend/domain/models/reply_understanding.py
"""患者回复理解模型（说明书 8.5 / 表）。

ReplyUnderstandingAgent 的结构化输出：抽取值、置信度、原文证据、停止意图、模糊类型。
LLM 只做理解；是否继续由确定性 TurnRouter 决定。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# 模糊类型（说明书 8.5：compose_question_prompt 依据此选择语气）
AmbiguityType = Literal["none", "vague", "deflect", "emotional", "irrelevant", "minimal"]
EmotionState = Literal["positive", "stable", "low", "distressed", "urgent", "unknown"]
EmotionIntensity = Literal["low", "medium", "high"]


class ReplyUnderstanding(BaseModel):
    """一次患者回复的结构化理解。"""
    # 四项关键槽位
    pain_nrs: float | None = Field(default=None, ge=0, le=10)
    sleep_quality: str | None = None
    medication_taken: bool | None = None
    side_effects: str | None = None

    # 置信度与证据（说明书 12：低置信度标记 uncertain）
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    uncertain: bool = False
    evidence: dict[str, str] = Field(default_factory=dict)  # {slot: 原文片段}

    # 患者意图
    patient_requested_stop: bool = False
    requires_immediate_action: bool = False

    # 当前这一条患者消息的情绪，不做跨天趋势计算
    emotion_state: EmotionState = "unknown"
    emotion_intensity: EmotionIntensity = "low"
    emotion_evidence: str = ""

    # 模糊类型，驱动追问语气
    ambiguity_type: AmbiguityType = "none"
    ambiguity_retry_count: int = 0

    # 原文
    raw_text: str = ""

    model_config = {"extra": "ignore"}

    def to_slot_overrides(self) -> dict:
        """返回非空槽位（供 merge_slots 节点合并）。None 的不覆盖已有值。"""
        out: dict = {}
        if self.pain_nrs is not None:
            out["pain_nrs"] = self.pain_nrs
        if self.sleep_quality is not None:
            out["sleep_quality"] = self.sleep_quality
        if self.medication_taken is not None:
            out["medication_taken"] = self.medication_taken
        if self.side_effects is not None:
            out["side_effects"] = self.side_effects
        return out
