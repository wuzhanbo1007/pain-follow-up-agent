# backend/agents/capability_agents/ai_review_agent.py
"""AIReviewAgent —— 会话审阅（说明书 8.4）。

只返回 ReviewDraft，不负责落库。完成度由确定性 CoverageEvaluator 计算。
"""
from __future__ import annotations

from domain.models.review import ReviewDraft
from prompts.review.review_followup_prompt import build_prompt, ReviewContext


class AIReviewAgent:
    def __init__(self, llm_gateway=None):
        self._gw = llm_gateway

    async def review(self, *, risk_result: dict, agent_summary: str,
                     transcripts: list, plan: dict | None = None,
                     history: dict | None = None) -> ReviewDraft:
        from infrastructure.llm.llm_gateway import LLMGateway
        gw = self._gw or LLMGateway()
        rule = self._rule_review(risk_result, transcripts)
        if not gw.is_llm_available():
            return ReviewDraft(**{**rule, "source": "rule"})
        spec = build_prompt(ReviewContext(
            risk_result=risk_result, agent_summary=agent_summary,
            transcripts=transcripts, plan=plan or {}, history=history or {},
        ))
        data = await gw.chat_json(
            [{"role": "system", "content": spec.system},
             {"role": "user", "content": spec.user}],
            profile="ai_review",
        )
        if not data or not data.get("summary"):
            return ReviewDraft(**{**rule, "source": "rule"})
        # completion_score 可能是 {total,breakdown}
        score = data.get("completion_score")
        if isinstance(score, dict):
            score = score.get("total", rule["completion_score"])
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = rule["completion_score"]
        score = max(0, min(100, score))
        # risk_flags 兼容 [{type,detail,severity}] 与 [str]
        rf = data.get("risk_flags", []) or []
        rf_str = [f"{r.get('detail','')}" if isinstance(r, dict) else str(r) for r in rf]
        sg = data.get("suggestions", []) or []
        sg_str = [s.get("content", "") if isinstance(s, dict) else str(s) for s in sg]
        return ReviewDraft(
            summary=data.get("summary", rule["summary"]),
            risk_flags=rf_str or rule["risk_flags"],
            completion_score=score,
            suggestions=sg_str or rule["suggestions"],
            source="llm",
            risk_level=risk_result.get("level", ""),
            total_score=risk_result.get("total_score", risk_result.get("score")),
        )

    @staticmethod
    def _rule_review(risk_result: dict, transcripts: list) -> dict:
        level = (risk_result.get("level_label") or risk_result.get("level") or "未知")
        high = level in ("高风险", "high_risk", "high")
        count = len(transcripts) if isinstance(transcripts, list) else 0
        score = 50
        if count >= 2:
            score += 20
        if count >= 4:
            score += 10
        if high:
            score = max(0, score - 20)
        score = max(0, min(100, score))
        flags = []
        if high:
            flags.append("高风险：需医生重点关注")
        for m in (transcripts or []):
            if m.get("role") == "patient" and any(
                    k in m.get("content", "") for k in ("疼得厉害", "受不了", "8分", "9分", "10分", "爆")):
                flags.append("患者自述疼痛剧烈")
                break
        suggestions = ["按常规周期持续随访"]
        if high:
            suggestions = ["建议医生尽快介入评估", "确认镇痛方案是否需要调整"]
        summary = f"共 {count} 条对话；风险等级：{level}（规则兜底总结，未调用 LLM）"
        return {"summary": summary, "risk_flags": flags,
                "completion_score": score, "suggestions": suggestions}
