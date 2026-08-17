# backend/domain/models/review.py
"""审阅草稿模型（说明书 8.4）。

SummarizerGraph 只返回 ReviewDraft，不负责落库。
完成度由确定性 CoverageEvaluator 计算；LLM 负责摘要、风险语言解释、沟通建议。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ReviewDraft(BaseModel):
    """单患者结束后的 AI 审阅草稿。"""
    summary: str = ""
    risk_flags: list[str] = Field(default_factory=list)
    completion_score: int = Field(default=0, ge=0, le=100)
    suggestions: list[str] = Field(default_factory=list)
    source: Literal["llm", "rule"] = "rule"
    # 确定性风险结果（由图注入，非 LLM 产出）
    risk_level: str = ""
    total_score: int | None = None

    model_config = {"extra": "ignore"}
