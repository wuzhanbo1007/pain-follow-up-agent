"""
风险评分引擎 — 基于患者历史 + 今日回复计算风险评分
"""
import json
import os


class RiskEngine:
    """风险评分计算引擎"""

    def __init__(self, rules_path=None):
        if rules_path is None:
            rules_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "references", "risk_scoring_rules.json"
            )
        with open(rules_path, "r", encoding="utf-8") as f:
            self.rules = json.load(f)

    def calculate(self, patient_id, patient_data, today_data, history_7d):
        """
        风险评分 = 基础分 + 动态分

        today_data: { nrs, sleep_quality, medication_taken }
        history_7d: [{ date, nrs_score, sleep_quality, medication_taken }]
        """
        base_score = self._calc_base(patient_data)
        dynamic_result = self._calc_dynamic(today_data, history_7d)
        total = base_score + dynamic_result["score"]

        # 风险等级判定
        levels = self.rules["levels"]
        if total <= levels["low"]["max"]:
            level = "low"
            action = levels["low"]["action"]
        elif total <= levels["medium"]["max"]:
            level = "medium"
            action = levels["medium"]["action"]
        else:
            level = "high"
            action = levels["high"]["action"]

        return {
            "total_score": total,
            "base_score": base_score,
            "dynamic_score": dynamic_result["score"],
            "level": level,
            "level_label": levels[level]["label"],
            "level_color": levels[level]["color"],
            "action": action,
            "details": {
                "base_reasons": self._base_reasons(patient_data, base_score),
                "dynamic_reasons": dynamic_result["reasons"]
            }
        }

    def _calc_base(self, patient_data):
        """计算基础分（诊断 + 年龄）"""
        score = 0
        diagnosis = patient_data.get("diagnosis", "")
        age = patient_data.get("age", 0)

        # 诊断基础分
        base_by_diag = self.rules.get("base_score_by_diagnosis", {})
        score += base_by_diag.get(diagnosis, 1)

        # 年龄基础分
        base_by_age = self.rules.get("base_score_by_age", {})
        if age < 60:
            score += base_by_age.get("under_60", 0)
        elif age <= 70:
            score += base_by_age.get("60_70", 1)
        else:
            score += base_by_age.get("over_70", 2)

        return score

    def _base_reasons(self, patient_data, base_score):
        """生成基础分原因描述（含分值）"""
        reasons = []
        diagnosis = patient_data.get("diagnosis", "")
        age = patient_data.get("age", 0)
        diag_scores = self.rules.get("base_score_by_diagnosis", {})
        d_score = diag_scores.get(diagnosis, 1)
        reasons.append(f"{diagnosis}(+{d_score})")
        if age > 70:
            reasons.append(f"年龄{age}岁(+2)")
        elif age > 60:
            reasons.append(f"年龄{age}岁(+1)")
        else:
            reasons.append(f"年龄{age}岁(+0)")
        return reasons

    def _calc_dynamic(self, today, history):
        """计算动态分"""
        score = 0
        reasons = []

        nrs = today.get("nrs")
        if nrs is not None:
            if nrs >= 7:
                score += self.rules["dynamic_rules"]["nrs_ge_7"]
                reasons.append(f"NRS≥7 (+{self.rules['dynamic_rules']['nrs_ge_7']})")
            elif nrs >= 5:
                score += self.rules["dynamic_rules"]["nrs_ge_5"]
                reasons.append(f"NRS≥5 (+{self.rules['dynamic_rules']['nrs_ge_5']})")

        # 连续恶化趋势
        if len(history) >= 3:
            recent_nrs = [h.get("nrs_score") for h in history[-3:]]
            if all(x is not None for x in recent_nrs):
                if recent_nrs == sorted(recent_nrs) and len(set(recent_nrs)) > 1:
                    score += self.rules["dynamic_rules"]["rising_3day_trend"]
                    reasons.append(f"连续3日NRS上升 (+{self.rules['dynamic_rules']['rising_3day_trend']})")

        # 未用药
        if not today.get("medication_taken", True):
            score += self.rules["dynamic_rules"]["medication_skipped"]
            reasons.append(f"未按时用药 (+{self.rules['dynamic_rules']['medication_skipped']})")

        # 睡眠持续差
        if today.get("sleep_quality") in ["差", "很差"]:
            recent_sleep = [h.get("sleep_quality") for h in history[-3:]]
            if all(s in ["差", "很差"] for s in recent_sleep):
                score += self.rules["dynamic_rules"]["poor_sleep_3day"]
                reasons.append(f"睡眠持续差 (+{self.rules['dynamic_rules']['poor_sleep_3day']})")

        return {"score": score, "reasons": reasons}

    def is_emergency(self, result):
        """判断是否需要紧急预警"""
        return result["level"] == "high"
