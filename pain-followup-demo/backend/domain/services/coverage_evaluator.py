# backend/domain/services/coverage_evaluator.py
"""覆盖度评估器（说明书 8.1 merge_slots / 8.2 TurnRouter 输入）。

确定性计算 coverage：四项关键信息是否收齐、缺哪些。
完成度由本节点计算，不交给 LLM。
"""
from __future__ import annotations

from domain.policies.schedule_spec import SLOT_KEYS
from domain.models.patient_report import CoverageReport


class CoverageEvaluator:
    """计算会话覆盖度。"""

    def evaluate(self, slots: dict) -> CoverageReport:
        collected = []
        missing = []
        for k in SLOT_KEYS:
            v = slots.get(k)
            if v in (None, "", []):
                missing.append(k)
            else:
                collected.append(k)
        return CoverageReport(
            complete=(not missing),
            collected=collected,
            missing=missing,
        )
