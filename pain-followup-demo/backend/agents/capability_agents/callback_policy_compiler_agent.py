# backend/agents/capability_agents/callback_policy_compiler_agent.py
"""CallbackPolicyCompilerAgent —— 电话回访策略编译（说明书 5 / 8.5 表）。

把医护自然语言编译为 CallbackPolicyDraft；LLM 不可用回退正则解析器。
不决定基础应访/免随访名单。
"""
from __future__ import annotations

import re

from domain.models.callback_policy import (
    CallbackPolicyDraft, CallbackCondition, CallbackAction,
    CONDITION_FIELD_WHITELIST, ALLOWED_OPS,
)
from prompts.policy.compile_callback_policy_prompt import build_prompt, PolicyCompileContext

# ======================================================================
# 电话回访阈值天数正则（确定性兜底，无 LLM；语义源自旧 services.config_parser）
# ======================================================================
_NUM = r"(?:[0-9]+|[一二两三四五六七八九十]+)"
_UNIT = r"(?:天|周|星期)"
_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
_WEEK_UNIT = ("周", "星期")
# 长词在前，避免交替匹配只吃掉"理"
_REPLY_NO = r"(?:回复|回访|回应|应答|回信|回电|回消息|回信息|回音|消息|信息|动静|反应|理睬|理会|音讯|理|回)"
_NO_REPLY_DAYS_RE = re.compile(
    r"(" + _NUM + r")\s*个?\s*(" + _UNIT + r")\s*(?:以上|多)?\s*(?:没|未|不)\s*(?:曾|有)?\s*" + _REPLY_NO
)
_NO_REPLY_REVERSED_RE = re.compile(
    r"(?:没|未|不)\s*(?:曾|有)?\s*" + _REPLY_NO + r"\s*(?:已|已经|超过|满|达)?\s*(" + _NUM + r")\s*个?\s*(" + _UNIT + r")"
)
_NO_REPLY_EXCEED_RE = re.compile(
    r"(?:连续|已|已经)?\s*(?:超过|满|达)\s*(" + _NUM + r")\s*个?\s*(" + _UNIT + r")\s*(?:以上|多)?\s*(?:没|未|不)\s*(?:曾|有)?\s*" + _REPLY_NO
)


def _to_int(num_text: str) -> int:
    """把 '2' / '两' / '十三' / '二十一' 转成整数（只处理常见小数字）。"""
    if num_text.isdigit():
        return int(num_text)
    if num_text == "十":
        return 10
    if "十" in num_text:
        tens_s, _, ones_s = num_text.partition("十")
        tens = _CN_DIGIT.get(tens_s, 1) if tens_s else 1
        ones = _CN_DIGIT.get(ones_s, 0) if ones_s else 0
        return tens * 10 + ones
    return _CN_DIGIT.get(num_text[0], 0)


def _extract_no_reply_days(text: str) -> int | None:
    """从一行声明中提取电话回访阈值天数（如 "2天没回复" → 2）。无匹配返回 None。

    覆盖常见表述、中文/阿拉伯数字、天/周单位、倒装；多模式命中取最小值
    （更保守——宁早回访不漏）。
    """
    days: list[int] = []
    for pat in (_NO_REPLY_DAYS_RE, _NO_REPLY_REVERSED_RE, _NO_REPLY_EXCEED_RE):
        for m in pat.finditer(text):
            val = _to_int(m.group(1))
            if m.group(2) in _WEEK_UNIT:
                val *= 7
            days.append(val)
    return min(days) if days else None


class CallbackPolicyCompilerAgent:
    def __init__(self, llm_gateway=None, regex_parser=None):
        self._gw = llm_gateway
        self._regex_parser = regex_parser  # 正则兜底（旧 config_parser）

    async def compile(self, natural_language: str) -> CallbackPolicyDraft:
        from infrastructure.llm.llm_gateway import LLMGateway
        gw = self._gw or LLMGateway()
        if gw.is_llm_available():
            draft = await self._llm_compile(gw, natural_language)
            if draft is not None and draft is not None:
                return self._validate(draft)
        return self._regex_compile(natural_language)

    async def _llm_compile(self, gw, text: str) -> CallbackPolicyDraft | None:
        spec = build_prompt(PolicyCompileContext(natural_language=text))
        data = await gw.chat_json(
            [{"role": "system", "content": spec.system},
             {"role": "user", "content": spec.user}],
            profile="policy_compiler",
        )
        if not data:
            return None
        conditions = []
        for c in (data.get("conditions") or []):
            try:
                conditions.append(CallbackCondition(**c))
            except Exception:
                continue
        return CallbackPolicyDraft(
            source_text=text,
            conditions=conditions,
            action=CallbackAction(**(data.get("action") or {})),
            unrecognized_lines=data.get("unrecognized_lines") or [],
        )

    def _regex_compile(self, text: str) -> CallbackPolicyDraft:
        """LLM 不可用时回退正则解析（复用旧 config_parser 的阈值提取）。"""
        days = self._extract_no_reply_days(text)
        conditions = []
        if days is not None:
            conditions.append(CallbackCondition(
                field="consecutive_no_reply_days", op="gte", value=days))
        return CallbackPolicyDraft(
            source_text=text, conditions=conditions,
            unrecognized_lines=[] if conditions else [text.strip()],
        )

    @staticmethod
    def _validate(draft: CallbackPolicyDraft) -> CallbackPolicyDraft:
        """字段白名单 + 运算符校验（不通过的条件剔除）。"""
        valid = [c for c in draft.conditions
                 if c.field in CONDITION_FIELD_WHITELIST and c.op in ALLOWED_OPS]
        draft.conditions = valid
        return draft

    @staticmethod
    def _extract_no_reply_days(text: str) -> int | None:
        """正则提取电话回访阈值天数（语义内联自旧 services.config_parser）。"""
        return _extract_no_reply_days(text)
