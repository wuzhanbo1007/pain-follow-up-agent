# backend/agents/capability_agents/plan_generation_agent.py
"""PlanGenerationAgent —— 随访计划生成（说明书 8.5 表）。计划生成独立于当天调度。"""
from __future__ import annotations

from prompts.planning.generate_followup_plan_prompt import build_prompt, PlanContext
from prompts.planning.plan_system_guardrails_prompt import build_prompt as guardrails


class PlanGenerationAgent:
    def __init__(self, llm_gateway=None):
        self._gw = llm_gateway

    async def generate(self, *, patient_brief: str, discharge_summary: str = "",
                       retrieved_evidence: str = "") -> dict:
        from infrastructure.llm.llm_gateway import LLMGateway
        gw = self._gw or LLMGateway()
        if not gw.is_llm_available():
            return self._fallback_plan()
        g = guardrails()
        spec = build_prompt(PlanContext(
            patient_brief=patient_brief, discharge_summary=discharge_summary,
            retrieved_evidence=retrieved_evidence,
        ))
        from llm.client import parse_json_safe
        raw = await gw.chat(
            [{"role": "system", "content": g.system + "\n" + spec.system},
             {"role": "user", "content": spec.user}],
            profile="plan_generation",
        )
        data = parse_json_safe(raw or "")
        return data or self._fallback_plan()

    @staticmethod
    def _fallback_plan() -> dict:
        return {
            "frequency": "每日", "duration_days": 90,
            "pain_type": "慢性疼痛",
            "recheck_items": ["每周记录NRS疼痛评分", "每月复诊评估疗效"],
            "medication_adjustment": "维持现有镇痛方案，NRS≥5时由医生评估是否调整剂量或联合用药",
            "warning_threshold": "NRS≥7，或出现爆发痛、睡眠持续变差、药物不良反应时及时就医",
            "health_education": ["按时服药，不可自行增减量或停药",
                                 "每日记录疼痛评分（NRS 0-10）",
                                 "出现爆发痛及时联系主管医生"],
            "lifestyle": ["规律作息，保证充足睡眠", "适度活动，避免久坐久站",
                          "清淡饮食，戒烟限酒"],
        }
