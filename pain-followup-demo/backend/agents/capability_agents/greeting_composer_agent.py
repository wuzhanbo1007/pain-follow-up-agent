# backend/agents/capability_agents/greeting_composer_agent.py
"""GreetingComposerAgent —— 开场白生成（Agent-centric迁移方案 §8）。

单一受限 LLM 能力：根据患者简报 + 出院天数 + 历史摘要生成开场问候。
工作流 Agent（PatientFollowupAgent）调用，不直接拼接 Prompt 或调用底层 LLM Client。
"""
from __future__ import annotations

from prompts.conversation.compose_greeting_prompt import (
    build_prompt as build_greeting,
    GreetingContext,
)


class GreetingComposerAgent:
    def __init__(self, llm_gateway=None):
        self._gw = llm_gateway

    async def compose(self, patient_brief, *, days_since_discharge: int = 0,
                      history_summary: str = "", risk_tendency: str = "medium_risk") -> str:
        from infrastructure.llm.llm_gateway import LLMGateway
        gw = self._gw or LLMGateway()
        p = patient_brief
        spec = build_greeting(GreetingContext(
            patient_name=p.get("name", ""), age=p.get("age"),
            gender=p.get("gender", ""), diagnosis=p.get("diagnosis", ""),
            pain_type=p.get("pain_type", ""),
            doctor_name=p.get("doctor_name", "随访医生"),
            department_name=p.get("department_name", "疼痛科"),
            days_since_discharge=days_since_discharge,
            history_summary=history_summary, risk_tendency=risk_tendency,
        ))
        if not gw.is_llm_available():
            return self._fallback(p)
        try:
            text = await gw.chat(
                [{"role": "system", "content": spec.system},
                 {"role": "user", "content": spec.user}],
                profile="compose_greeting",
            )
            return (text or "").strip() or self._fallback(p)
        except Exception:
            return self._fallback(p)

    @staticmethod
    def _fallback(p) -> str:
        return (f"{p.get('name','患者')}您好，我是{p.get('department_name', '疼痛科')}的医生{p.get('doctor_name', '随访医生')}，来关心一下您今天的"
                f"恢复情况。今天感觉怎么样呀？")
