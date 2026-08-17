# backend/domain/services/risk_evaluator.py
"""RiskEvaluator —— 版本化风险策略解释器（说明书 8.3）。

risk_engine.py 保持确定性，但升级为版本化规则解释器：
  - 规则值（年龄区间、NRS 阈值、趋势窗口、风险等级、动作映射）全部外置到 RiskPolicy；
  - Python 只实现通用运算符；
  - 每次结果携带 policy_version / input_snapshot / matched_rules。

实现上复用现有 references/risk_scoring_rules.json 作为 RiskPolicy 的数据源，
行为与旧 RiskEngine 完全一致，只在外层补齐可追溯字段。
"""
from __future__ import annotations

import json
import os
from typing import Any

from domain.models.patient_report import RiskReport

_DEFAULT_RULES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "..", "references", "risk_scoring_rules.json",
)

DEFAULT_POLICY_VERSION = "pain-risk-v5"


class RiskPolicy:
    """版本化的风险规则值对象（从 JSON 加载）。"""

    def __init__(self, rules_path: str | None = None,
                 version: str = DEFAULT_POLICY_VERSION):
        path = rules_path or _DEFAULT_RULES_PATH
        with open(path, "r", encoding="utf-8") as f:
            self.rules = json.load(f)
        self.version = version


class RiskEvaluator:
    """确定性风险评分。复用旧 RiskEngine 算法，输出带可追溯字段的 RiskReport。"""

    def __init__(self, policy: RiskPolicy | None = None,
                 legacy_engine=None):
        # legacy_engine: 可注入现有 engine.risk_engine.RiskEngine（复用其 calculate）
        self.policy = policy or RiskPolicy()
        self._legacy = legacy_engine  # 可为 None

    def evaluate(self, patient_id: str, patient_data: dict[str, Any],
                 today_data: dict[str, Any], history_7d: list[dict[str, Any]]) -> RiskReport:
        rules = self.policy.rules
        base_score = self._calc_base(patient_data)
        dynamic = self._calc_dynamic(today_data, history_7d)
        total = base_score + dynamic["score"]

        levels = rules["levels"]
        if total <= levels["low"]["max"]:
            level = "low"
        elif total <= levels["medium"]["max"]:
            level = "medium"
        else:
            level = "high"
        action = levels[level]["action"]

        matched = []
        matched.extend(self._base_reasons(patient_data, base_score))
        matched.extend(dynamic["reasons"])

        return RiskReport(
            level=level,
            score=total,
            policy_version=self.policy.version,
            matched_rules=matched,
            base_score=base_score,
            dynamic_score=dynamic["score"],
            level_label=levels[level]["label"],
            action=action,
            details={
                "base_reasons": self._base_reasons(patient_data, base_score),
                "dynamic_reasons": dynamic["reasons"],
                "input_snapshot": {
                    "nrs": today_data.get("nrs"),
                    "sleep_quality": today_data.get("sleep_quality"),
                    "medication_taken": today_data.get("medication_taken"),
                },
            },
        )

    def is_emergency(self, report: RiskReport) -> bool:
        return report.level == "high"

    # 以下算法与旧 RiskEngine 完全一致，保证行为不变
    def _calc_base(self, patient_data: dict) -> int:
        score = 0
        diagnosis = patient_data.get("diagnosis", "")
        age = patient_data.get("age", 0)
        score += self.policy.rules.get("base_score_by_diagnosis", {}).get(diagnosis, 1)
        if age < 60:
            score += self.policy.rules.get("base_score_by_age", {}).get("under_60", 0)
        elif age <= 70:
            score += self.policy.rules.get("base_score_by_age", {}).get("60_70", 1)
        else:
            score += self.policy.rules.get("base_score_by_age", {}).get("over_70", 2)
        return score

    def _base_reasons(self, patient_data: dict, _base_score: int) -> list[str]:
        """年龄原因文本与 _calc_base 使用同一阈值（§11-14：年龄计算与解释必须一致）。

        _calc_base：age<60 → under_60；age<=70 → 60_70；else → over_70。
        这里按相同边界取规则值，保证"加分多少"与"文本写多少"一致。
        """
        reasons: list[str] = []
        diagnosis = patient_data.get("diagnosis", "")
        age = patient_data.get("age", 0)
        d_score = self.policy.rules.get("base_score_by_diagnosis", {}).get(diagnosis, 1)
        reasons.append(f"{diagnosis}(+{d_score})")
        age_rules = self.policy.rules.get("base_score_by_age", {})
        if age < 60:
            reasons.append(f"年龄{age}岁(+{age_rules.get('under_60', 0)})")
        elif age <= 70:
            reasons.append(f"年龄{age}岁(+{age_rules.get('60_70', 1)})")
        else:
            reasons.append(f"年龄{age}岁(+{age_rules.get('over_70', 2)})")
        return reasons

    def _calc_dynamic(self, today: dict, history: list) -> dict:
        score = 0
        reasons: list[str] = []
        rules = self.policy.rules["dynamic_rules"]
        nrs = today.get("nrs")
        if nrs is not None:
            if nrs >= 7:
                score += rules["nrs_ge_7"]
                reasons.append(f"NRS≥7 (+{rules['nrs_ge_7']})")
            elif nrs >= 5:
                score += rules["nrs_ge_5"]
                reasons.append(f"NRS≥5 (+{rules['nrs_ge_5']})")
        if len(history) >= 3:
            recent_nrs = [h.get("nrs_score") for h in history[-3:]]
            if all(x is not None for x in recent_nrs):
                if recent_nrs == sorted(recent_nrs) and len(set(recent_nrs)) > 1:
                    score += rules["rising_3day_trend"]
                    reasons.append(f"连续3日NRS上升 (+{rules['rising_3day_trend']})")
        if not today.get("medication_taken", True):
            score += rules["medication_skipped"]
            reasons.append(f"未按时用药 (+{rules['medication_skipped']})")
        if today.get("sleep_quality") in ["差", "很差"] and len(history) >= 3:
            # §11-14：先判历史长度，避免 all([])==True 把空历史误判为"连续三天睡眠差"
            recent_sleep = [h.get("sleep_quality") for h in history[-3:]]
            if len(recent_sleep) == 3 and all(s in ["差", "很差"] for s in recent_sleep):
                score += rules["poor_sleep_3day"]
                reasons.append(f"睡眠持续差 (+{rules['poor_sleep_3day']})")
        return {"score": score, "reasons": reasons}
