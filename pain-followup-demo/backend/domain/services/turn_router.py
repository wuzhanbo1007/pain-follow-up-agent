# backend/domain/services/turn_router.py
"""TurnRouter —— 单一流程决策点（说明书 8.2 / 12）。

唯一决定继续追问 / 转人工 / 结束。LLM 不能自行决定"再追问几次"或"结束随访"。
依据：requires_immediate_action / patient_requested_stop / coverage.complete / max_turns / ambiguity_retry。
"""
from __future__ import annotations

from typing import Literal

from domain.models.reply_understanding import ReplyUnderstanding
from domain.models.patient_report import CoverageReport
from domain.policies.schedule_spec import ConversationPolicy


TurnAction = Literal["emergency_handoff", "incomplete_handoff", "complete",
                     "clarify", "continue_wait"]

# TurnRouter 选定继续追问时给出的 missing slot（供 compose_question 节点）
class TurnDecision:
    """TurnRouter 的输出。"""
    __slots__ = ("action", "missing_slots", "ambiguity_type", "reason")

    def __init__(self, action: TurnAction, missing_slots: list[str],
                 ambiguity_type: str = "none", reason: str = ""):
        self.action = action
        self.missing_slots = missing_slots
        self.ambiguity_type = ambiguity_type
        self.reason = reason

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "missing_slots": list(self.missing_slots),
            "ambiguity_type": self.ambiguity_type,
            "reason": self.reason,
        }


class TurnRouter:
    """唯一流程决策点。"""

    def __init__(self, policy: ConversationPolicy | None = None):
        self.policy = policy or ConversationPolicy()

    def route(self, understanding: ReplyUnderstanding, coverage: CoverageReport,
              turn_no: int, *, max_turns: int | None = None,
              risk_report: dict | None = None) -> TurnDecision:
        # 0) 确定性高风险（RiskEvaluator 输出）→ 转人工，不依赖 LLM 判断（§11-12）
        risk = risk_report or {}
        if risk.get("level") == "high":
            return TurnDecision("emergency_handoff", coverage.missing,
                                understanding.ambiguity_type,
                                f"high_risk({risk.get('level_label') or '高风险'})")

        # 1) 紧急：需立即动作（LLM 理解层面）→ 先告警转人工，不等信息收齐
        if understanding.requires_immediate_action:
            return TurnDecision("emergency_handoff", coverage.missing,
                                understanding.ambiguity_type, "requires_immediate_action")

        # 2) 患者主动停止 → 尊重意愿，保留缺失项
        if understanding.patient_requested_stop:
            return TurnDecision("incomplete_handoff", coverage.missing,
                                understanding.ambiguity_type, "patient_requested_stop")

        # 3) 四项收齐 → 生成告别并结束
        if coverage.complete:
            return TurnDecision("complete", [], understanding.ambiguity_type,
                                "coverage_complete")

        # 4) 到达安全上限 → 不继续机械追问
        cap = max_turns if max_turns is not None else self.policy.max_turns
        if turn_no >= cap:
            return TurnDecision("incomplete_handoff", coverage.missing,
                                understanding.ambiguity_type,
                                f"max_turns_reached({turn_no}/{cap})")

        # 5) 其他 → 从优先级最高的 missing slot 生成追问
        return TurnDecision("clarify", coverage.missing,
                            understanding.ambiguity_type, "clarify_missing_slot")
