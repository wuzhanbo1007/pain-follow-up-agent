# backend/agents/capability_agents/question_composer_agent.py
"""QuestionComposerAgent —— 追问生成（说明书 8.5 模糊回复整合）。

唯一追问 Agent，吸收原 TARGET_QUESTION_SYSTEM 与 clarification_message。
只能询问 TurnRouter 选定的 missing slot；依据 ambiguity_type 选语气。
"""
from __future__ import annotations

from prompts.conversation.compose_question_prompt import build_prompt, QuestionContext

_FALLBACK_TEMPLATES = {
    "pain_nrs": "方便给今天的疼痛打个分吗（0-10分）？",
    "sleep_quality": "昨晚睡得好吗？有没有因为疼醒或者睡不好？",
    "medication_taken": "今天的药按时吃了吗？",
    "side_effects": "最近有没有恶心、头晕或者哪里不舒服吗？",
}


class QuestionComposerAgent:
    def __init__(self, llm_gateway=None):
        self._gw = llm_gateway

    async def compose(self, *, last_patient_reply: str, missing_slots: list[str],
                      ambiguity_type: str = "none", retry_count: int = 0,
                      patient_name: str = "", turn_no: int = 1) -> str:
        from infrastructure.llm.llm_gateway import LLMGateway
        gw = self._gw or LLMGateway()
        if not missing_slots:
            return ""
        if not gw.is_llm_available():
            return self._fallback(missing_slots, patient_name)
        spec = build_prompt(QuestionContext(
            last_patient_reply=last_patient_reply, missing_slots=missing_slots,
            ambiguity_type=ambiguity_type, retry_count=retry_count,
            patient_name=patient_name, turn_no=turn_no,
        ))
        try:
            text = await gw.chat(
                [{"role": "system", "content": spec.system},
                 {"role": "user", "content": spec.user}],
                profile="compose_question",
            )
            text = (text or "").strip()
            return text or self._fallback(missing_slots, patient_name)
        except Exception:
            return self._fallback(missing_slots, patient_name)

    @staticmethod
    def _fallback(missing_slots: list[str], patient_name: str) -> str:
        prefix = f"{patient_name}，" if patient_name else ""
        for k in missing_slots:
            if k in _FALLBACK_TEMPLATES:
                return prefix + _FALLBACK_TEMPLATES[k]
        return f"{prefix}您再说说其他情况呀～"
