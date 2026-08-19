"""Generate the nurse's closing message for a follow-up conversation."""
from __future__ import annotations

from prompts.conversation.compose_farewell_prompt import (
    FarewellContext,
    build_prompt as build_farewell,
)
from domain.services.patient_address import (
    build_patient_address,
    remove_leading_patient_address,
    sanitize_patient_address,
)



class FarewellComposerAgent:
    def __init__(self, llm_gateway=None):
        self._gw = llm_gateway

    async def compose(
        self,
        *,
        patient_name: str,
        patient_address: str = "",
        diagnosis: str = "",
        pain_type: str = "",
        risk_level: str = "medium",
        nrs_score=None,
        sleep_quality=None,
        medication_status=None,
        side_effects=None,
        incomplete: bool = False,
    ) -> str:
        from infrastructure.llm.llm_gateway import LLMGateway

        address = patient_address or build_patient_address(patient_name)
        gw = self._gw or LLMGateway()
        spec = build_farewell(
            FarewellContext(
                patient_name=patient_name,
                patient_address=address,
                diagnosis=diagnosis,
                pain_type=pain_type,
                risk_level=risk_level,
                nrs_score=nrs_score,
                sleep_quality=sleep_quality,
                medication_status=medication_status,
                side_effects=side_effects,
                incomplete=incomplete,
            )
        )
        try:
            llm_available = gw.is_llm_available()
        except Exception as exc:
            fallback = self._fallback(incomplete)
            return fallback
        if not llm_available:
            fallback = self._fallback(incomplete)
            return fallback
        try:
            text = await gw.chat(
                [
                    {"role": "system", "content": spec.system},
                    {"role": "user", "content": spec.user},
                ],
                profile="compose_farewell",
            )
        except Exception as exc:
            fallback = self._fallback(incomplete)
            return fallback
        try:
            text = sanitize_patient_address(text, patient_name, address)
            text = remove_leading_patient_address(text, address)
        except Exception as exc:
            fallback = self._fallback(incomplete)
            return fallback
        if not text or not str(text).strip():
            fallback = self._fallback(incomplete)
            return fallback
        return text

    @staticmethod
    def _fallback(incomplete: bool) -> str:
        if incomplete:
            return "好的，今天先聊到这里，您先休息，有情况随时联系我们，下次再详细聊。"
        return "好的，感谢您告诉我这些情况。记得按时吃药、留意疼痛变化，有情况随时联系我们，祝您早日康复。"
