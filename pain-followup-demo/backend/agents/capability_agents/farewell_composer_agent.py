# backend/agents/capability_agents/farewell_composer_agent.py
"""FarewellComposerAgent —— 告别语生成（Agent-centric迁移方案 §8）。

单一受限 LLM 能力：根据本轮 slots / 风险 / 完整性生成告别语。
工作流 Agent（ConversationAgent）调用，不直接拼接 Prompt 或调用底层 LLM Client。
"""
from __future__ import annotations

from prompts.conversation.compose_farewell_prompt import (
    build_prompt as build_farewell,
    FarewellContext,
)


class FarewellComposerAgent:
    def __init__(self, llm_gateway=None):
        self._gw = llm_gateway

    async def compose(self, *, patient_name: str, diagnosis: str = "",
                      pain_type: str = "", risk_level: str = "medium",
                      nrs_score=None, sleep_quality=None, medication_status=None,
                      side_effects=None, incomplete: bool = False) -> str:
        from infrastructure.llm.llm_gateway import LLMGateway
        gw = self._gw or LLMGateway()
        spec = build_farewell(FarewellContext(
            patient_name=patient_name, diagnosis=diagnosis, pain_type=pain_type,
            risk_level=risk_level, nrs_score=nrs_score, sleep_quality=sleep_quality,
            medication_status=medication_status, side_effects=side_effects,
            incomplete=incomplete,
        ))
        if not gw.is_llm_available():
            return self._fallback(incomplete)
        try:
            text = await gw.chat(
                [{"role": "system", "content": spec.system},
                 {"role": "user", "content": spec.user}],
                profile="compose_farewell",
            )
            return (text or "").strip() or self._fallback(incomplete)
        except Exception:
            return self._fallback(incomplete)

    @staticmethod
    def _fallback(incomplete: bool) -> str:
        if incomplete:
            return "好的，今天先聊到这里，您先休息，有情况随时联系，下次再详细聊聊。"
        return "好的，今天辛苦您了，记得按时吃药、记录疼痛评分，有变化随时联系我，祝您早日康复。"
