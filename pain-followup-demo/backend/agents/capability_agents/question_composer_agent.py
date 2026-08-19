"""Generate the next nurse follow-up question."""
from __future__ import annotations

import re

from prompts.conversation.compose_question_prompt import (
    QuestionContext,
    build_prompt,
)
from domain.services.patient_address import (
    build_patient_address,
    remove_leading_patient_address,
    sanitize_patient_address,
)



_FALLBACK_TEMPLATES = {
    "pain_nrs": "方便给今天的疼痛打个分吗？0到10分，带小数也可以。",
    "sleep_quality": "昨晚睡得还好吗？有没有因为疼痛醒来，或者睡得不踏实？",
    "medication_taken": "今天的药按时吃了吗？",
    "side_effects": "最近有没有恶心、头晕，或者哪里不舒服？",
}


class QuestionComposerAgent:
    def __init__(self, llm_gateway=None):
        self._gw = llm_gateway

    async def compose(
        self,
        *,
        last_patient_reply: str,
        missing_slots: list[str],
        ambiguity_type: str = "none",
        retry_count: int = 0,
        patient_name: str = "",
        patient_address: str = "",
        turn_no: int = 1,
        emotion_state: str = "unknown",
        emotion_intensity: str = "low",
        current_question: str = "",
        history_summary: str = "",
        recent_messages: list[dict] | None = None,
        known_slots: dict | None = None,
    ) -> str:
        from infrastructure.llm.llm_gateway import LLMGateway

        address = patient_address or build_patient_address(patient_name)
        gw = self._gw or LLMGateway()
        if not missing_slots:
            return ""

        try:
            llm_available = gw.is_llm_available()
        except Exception as exc:
            fallback = self._fallback(
                missing_slots, address, emotion_state, last_patient_reply,
            )
            return fallback
        if not llm_available:
            fallback = self._fallback(
                missing_slots, address, emotion_state, last_patient_reply,
            )
            return fallback

        spec = build_prompt(
            QuestionContext(
                last_patient_reply=last_patient_reply,
                missing_slots=missing_slots,
                ambiguity_type=ambiguity_type,
                retry_count=retry_count,
                patient_name=patient_name,
                patient_address=address,
                turn_no=turn_no,
                emotion_state=emotion_state,
                emotion_intensity=emotion_intensity,
                current_question=current_question,
                history_summary=history_summary,
                recent_messages=recent_messages or [],
                known_slots=known_slots or {},
            )
        )
        try:
            text = await gw.chat(
                [
                    {"role": "system", "content": spec.system},
                    {"role": "user", "content": spec.user},
                ],
                profile="compose_question",
            )
        except Exception as exc:
            fallback = self._fallback(
                missing_slots, address, emotion_state, last_patient_reply,
            )
            return fallback

        if not text or not str(text).strip():
            fallback = self._fallback(
                missing_slots, address, emotion_state, last_patient_reply,
            )
            return fallback

        try:
            text = sanitize_patient_address(text, patient_name, address)
            text = remove_leading_patient_address(text, address)
            text = self._normalize_discomfort_empathy(text, last_patient_reply)
            text = self._normalize_high_pain_response(text, last_patient_reply)
            text = self._normalize_sleep_response(text, last_patient_reply)
            text = self._remove_repeated_opening(text, recent_messages or [])
        except Exception as exc:
            fallback = self._fallback(
                missing_slots, address, emotion_state, last_patient_reply,
            )
            return fallback

        validation_reason = self._target_slot_validation_reason(
            text,
            missing_slots,
            known_slots,
        )
        text = self._ensure_target_slot_question(
                text,
                missing_slots,
                address,
                emotion_state,
                last_patient_reply,
                known_slots,
            )
        if validation_reason:
            return text
        if not text or not str(text).strip():
            fallback = self._fallback(
                missing_slots, address, emotion_state, last_patient_reply,
            )
            return fallback
        return text

    @staticmethod
    def _fallback(
        missing_slots: list[str],
        patient_address: str,
        emotion_state: str = "unknown",
        last_patient_reply: str = "",
    ) -> str:
        if QuestionComposerAgent._extract_high_pain_score(last_patient_reply):
            prefix = "这个疼痛分数比较高，您现在肯定很不舒服。"
        elif QuestionComposerAgent._sleep_reply_is_unfavorable(last_patient_reply):
            prefix = "睡眠不太理想，确实会让人疲惫。"
        elif emotion_state == "positive":
            prefix = "听到您现在状态不错，真好，我们继续保持。"
        elif emotion_state == "distressed":
            prefix = "我听到您现在真的很难受，先不用勉强自己，我们一步一步来。"
        elif emotion_state == "low":
            prefix = "听起来您这段时间确实不容易，您不用着急，我们慢慢来。"
        else:
            prefix = ""

        for slot in missing_slots:
            if slot in _FALLBACK_TEMPLATES:
                return prefix + _FALLBACK_TEMPLATES[slot]
        return f"{prefix}您再说说其他情况吧。"

    @staticmethod
    def _normalize_discomfort_empathy(text: str, patient_reply: str) -> str:
        """患者明确不适时，避免使用像表扬/客套话的“辛苦了”。"""
        reply = str(patient_reply or "")
        has_explicit_discomfort = any(
            phrase in reply
            for phrase in ("很痛", "很疼", "疼得", "痛得", "疼痛", "腰痛", "腰疼", "不舒服", "难受", "痛苦")
        )
        has_negated_discomfort = any(
            phrase in reply
            for phrase in ("没有不舒服", "没什么不舒服", "不疼", "不痛")
        )
        if not has_explicit_discomfort or has_negated_discomfort or "辛苦" not in text:
            return text
        return re.sub(
            r"(?:今天|这段时间|您)?(?:确实|也|真的)?辛苦(?:您)?了",
            "听起来您现在确实很难受",
            text,
        )

    @staticmethod
    def _extract_high_pain_score(patient_reply: str) -> bool:
        reply = str(patient_reply or "").strip()
        number = r"(?:10(?:\.0+)?|[7-9](?:\.[0-9]+)?)"
        standalone = re.fullmatch(
            rf"\s*({number})\s*分?(?:左右)?[。.!！]?\s*", reply,
        )
        if standalone:
            return float(standalone.group(1)) >= 7
        contextual = re.search(
            rf"(?:疼痛|痛感|疼痛评分|疼痛打分|打分|评分)"
            rf"[^0-9]{{0,10}}({number})(?![0-9.])\s*(?:分|/\s*10)?",
            reply,
        )
        if contextual:
            return float(contextual.group(1)) >= 7
        return bool(re.search(
            rf"(?<![0-9.])({number})\s*分(?![0-9.])", reply,
        ))

    @staticmethod
    def _ensure_target_slot_question(
        text: str,
        missing_slots: list[str],
        patient_address: str,
        emotion_state: str,
        last_patient_reply: str,
        known_slots: dict | None = None,
    ) -> str:
        """校验模型确实在问当前缺失项，避免已回答内容被反复追问。

        槽位路由是确定性的，但LLM仍可能生成上一问题的相似追问。
        如果生成内容没有覆盖当前目标槽位，直接使用同一槽位的安全模板，
        保证对话能继续收敛而不是在疼痛评分上打转。
        """
        validation_reason = QuestionComposerAgent._target_slot_validation_reason(
            text, missing_slots, known_slots,
        )
        if not validation_reason:
            return text
        return QuestionComposerAgent._fallback(
            missing_slots, patient_address, emotion_state, last_patient_reply,
        )

    @staticmethod
    def _target_slot_validation_reason(
        text: str,
        missing_slots: list[str],
        known_slots: dict | None = None,
    ) -> str | None:
        """返回模型回复被槽位校验拦截的原因；通过时返回 None。"""
        if not text or not missing_slots:
            return None
        slot_keywords = {
            "pain_nrs": ("疼", "痛", "评分", "打分", "几分", "NRS"),
            "sleep_quality": ("睡", "入睡", "醒", "休息"),
            "medication_taken": ("药", "服药", "吃药", "用药"),
            "side_effects": ("副作用", "不舒服", "恶心", "头晕", "便秘", "皮疹", "呕吐"),
        }
        target = missing_slots[0]
        keywords = slot_keywords.get(target, (target,))
        if not any(keyword.lower() in text.lower() for keyword in keywords):
            return "target_slot_not_mentioned"

        # 允许在承接中提到已知信息，但不允许把已收集槽位再次变成问题。
        # 按句切分后检查疑问词，避免把“刚才疼痛较高，我很担心。昨晚睡得好吗？”
        # 误判成重复追问疼痛。
        known_slots = known_slots or {}
        for known_slot, value in known_slots.items():
            if known_slot == target or value in (None, "", []):
                continue
            known_keywords = slot_keywords.get(known_slot, (known_slot,))
            for sentence in re.split(r"[。！？?!；;]", text):
                if (any(keyword.lower() in sentence.lower() for keyword in known_keywords)
                        and QuestionComposerAgent._is_known_slot_question(
                            sentence, known_slot, target,
                        )):
                    return f"known_slot_reasked:{known_slot}"
        return None

    @staticmethod
    def _is_known_slot_question(
        sentence: str, known_slot: str, target_slot: str = "",
    ) -> bool:
        """判断句子是否真的在追问已收集槽位，避免关键词误伤承接语。

        例如“有没有因为疼痛醒来”是在询问睡眠，不是重新询问疼痛评分；
        “吃药后有没有恶心”是在询问副作用，也不是重新询问服药状态。
        只有出现“疼痛几分/疼痛评分”等评分结构时，才算重复追问 pain_nrs。
        """
        text = re.sub(r"[。！？?!；;]+$", "", str(sentence or "").strip())
        if not text:
            return False
        # 服药信息可以作为副作用问题的上下文：
        # “按时服药很好，吃药后有没有恶心、头晕？”不应被判定为重复问服药。
        if target_slot == "side_effects" and known_slot == "medication_taken":
            has_side_effect_context = any(
                term in text
                for term in ("副作用", "不舒服", "恶心", "头晕", "便秘", "皮疹", "呕吐")
            )
            direct_medication_status = bool(re.search(
                r"(?:按时|规律|每天|今天)[^，。！？?!；;]{0,6}"
                r"(?:吃药|服药|用药|药)[^，。！？?!；;]{0,3}"
                r"(?:吗|呢|有没有|是否|？|\?)",
                text,
            ))
            if has_side_effect_context and not direct_medication_status:
                return False
        # 睡眠情况也经常作为服药问题的承接事实：
        # “睡眠不太理想，今天的药按时吃了吗？”不应被判定为重复问睡眠；
        # 但“昨晚睡得还好吗？今天的药按时吃了吗？”仍应拦截。
        if target_slot == "medication_taken" and known_slot == "sleep_quality":
            explicit_sleep_question = bool(re.search(
                r"(?:睡|入睡|醒|休息)[^，。！？?!；;]{0,6}"
                r"(?:吗|呢|如何|怎么样|有没有|是否|？|\?)",
                text,
            ))
            if not explicit_sleep_question:
                return False
        question_tail = r"[^。！？?!；;]{0,6}(?:吗|呢|多少|几分|如何|怎么样|有没有|是否|？|\?)"
        patterns = {
            "pain_nrs": (
                rf"(?:疼痛|痛感|评分|打分|NRS){question_tail}",
                r"(?:多少|几分|评分|打分)[^。！？?!；;]{0,8}(?:疼痛|痛感|分数|NRS)",
            ),
            "sleep_quality": (
                rf"(?:睡|入睡|醒|休息){question_tail}",
                rf"(?:有没有|是否|有无)[^。！？?!；;]{{0,8}}(?:睡|入睡|醒|休息)",
            ),
            "medication_taken": (
                rf"(?:药|服药|吃药|用药){question_tail}",
                rf"(?:有没有|是否|有无)[^。！？?!；;]{{0,8}}(?:药|服药|吃药|用药)",
            ),
            "side_effects": (
                rf"(?:副作用|不舒服|恶心|头晕|便秘|皮疹|呕吐){question_tail}",
                rf"(?:有没有|是否|有无)[^。！？?!；;]{{0,8}}(?:副作用|不舒服|恶心|头晕|便秘|皮疹|呕吐)",
            ),
        }
        slot_patterns = patterns.get(known_slot)
        if slot_patterns:
            return any(re.search(pattern, text, re.IGNORECASE) for pattern in slot_patterns)
        return bool(re.search(r"(?:吗|呢|多少|几分|如何|怎么样|有没有|是否|？|\?)", text))

    @staticmethod
    def _normalize_high_pain_response(text: str, patient_reply: str) -> str:
        """高疼痛评分时，阻止模型把患者状态描述成良好。"""
        if not QuestionComposerAgent._extract_high_pain_score(patient_reply):
            return text
        replacements = (
            "整体状态还不错",
            "整体状态不错",
            "整体情况还不错",
            "整体情况不错",
            "状态还不错",
            "状态不错",
        )
        normalized = text
        for phrase in replacements:
            normalized = normalized.replace(
                phrase, "疼痛程度较高，您现在肯定很不舒服",
            )
        return normalized

    @staticmethod
    def _sleep_reply_is_unfavorable(patient_reply: str) -> bool:
        """识别睡眠不佳，避免把睡眠问题说成整体状态平稳。"""
        reply = str(patient_reply or "").strip()
        if "睡" not in reply:
            return False
        if any(phrase in reply for phrase in (
            "睡得不好", "睡不好", "睡不着", "睡得差", "睡眠差",
            "断断续续", "不踏实", "醒了好几次", "总是醒", "睡不够",
            "睡得一般",
        )):
            return True
        return bool(re.search(
            r"(?:睡了|总共睡|睡够)[^0-9一二两三四五六七八九十]{0,8}"
            r"(?:[0-6]|一|二|两|三|四|五|六)\s*(?:个)?小时",
            reply,
        ))

    @staticmethod
    def _normalize_sleep_response(text: str, patient_reply: str) -> str:
        """睡眠明显不佳时，替换与事实冲突的“状态平稳”表述。"""
        if not QuestionComposerAgent._sleep_reply_is_unfavorable(patient_reply):
            return text
        normalized = text
        for phrase in (
            "好的，听起来目前状态比较平稳，我们按自己的节奏来。",
            "听起来目前状态比较平稳，我们按自己的节奏来。",
            "目前状态比较平稳",
            "状态比较平稳",
            "状态平稳",
        ):
            normalized = normalized.replace(
                phrase,
                "睡眠不太理想，确实会让人疲惫。"
                if phrase.endswith("。")
                else "睡眠不太理想，确实会让人疲惫",
            )
        return normalized

    @staticmethod
    def _remove_repeated_opening(text: str, recent_messages: list[dict]) -> str:
        """避免连续两轮复用完全相同的状态承接语。"""
        previous_nurse = [
            str(message.get("content") or "").strip()
            for message in recent_messages
            if message.get("role") in ("nurse", "assistant")
        ]
        repeatable = (
            "好的，听起来目前状态比较平稳，我们按自己的节奏来。",
            "听起来目前状态比较平稳，我们按自己的节奏来。",
        )
        normalized = text
        for phrase in repeatable:
            if phrase in normalized and any(phrase in old for old in previous_nurse):
                normalized = normalized.replace(phrase, "", 1).lstrip("，。； ")
        return normalized
