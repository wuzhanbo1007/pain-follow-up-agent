# backend/agents/capability_agents/reply_understanding_agent.py
"""ReplyUnderstandingAgent —— 患者回复理解（说明书 8.5 表）。

LLM 每轮只做结构化理解；程序根据 Policy 计算 coverage、风险、下一节点。
输出经 ReplyUnderstanding Pydantic 校验；失败回退关键词解析。
"""
from __future__ import annotations

import re

from core.logging_config import get_logger
from domain.models.reply_understanding import ReplyUnderstanding
from prompts.conversation.understand_reply_prompt import build_prompt, UnderstandContext

log = get_logger("painsmart.agent.understand")


class ReplyUnderstandingAgent:
    """患者回复结构化理解 Agent。"""

    def __init__(self, llm_gateway=None, reply_parser=None):
        self._gw = llm_gateway
        self._legacy_parser = reply_parser  # 旧 ReplyParser，降级用

    async def understand(self, reply_text: str, *, known_slots: dict | None = None,
                         history_summary: str = "", current_question: str = "",
                         turn_no: int = 1) -> ReplyUnderstanding:
        from infrastructure.llm.llm_gateway import LLMGateway
        gw = self._gw or LLMGateway()
        if not gw.is_llm_available():
            return self._fallback(reply_text, current_question=current_question)
        spec = build_prompt(UnderstandContext(
            reply_text=reply_text, known_slots=known_slots or {},
            history_summary=history_summary, current_question=current_question,
            turn_no=turn_no,
        ))
        data = await gw.chat_json(
            [{"role": "system", "content": spec.system},
             {"role": "user", "content": spec.user}],
            profile="understand_reply",
        )
        if not data:
            return self._fallback(reply_text, current_question=current_question)
        try:
            return self._normalize(data, reply_text, current_question=current_question)
        except Exception as exc:
            log.warning("回复理解归一化失败，回退规则: %s", exc)
            return self._fallback(reply_text, current_question=current_question)

    @staticmethod
    def _normalize(data: dict, raw: str, current_question: str = "") -> ReplyUnderstanding:
        # LLM 可能只返回部分字段，或者把“睡得很好”“按时服药”这类
        # 明确表达留在原文而没有填入 JSON。用同一份原文做确定性补全，
        # 只补 None，不覆盖 LLM 已确认的值。
        fallback = _keyword_parse(raw, current_question=current_question)

        # pain_nrs 0-10
        nrs = data.get("pain_nrs")
        if nrs is not None:
            try:
                nrs = int(nrs)
                if not (0 <= nrs <= 10):
                    nrs = None
            except (TypeError, ValueError):
                nrs = None
        # 数字评分优先；没有数字时，明确的重度疼痛描述必须落入 NRS 8–10，
        # 防止 LLM 将“疼得厉害”误归为低分。
        qualitative_nrs = ReplyUnderstandingAgent._qualitative_pain_score(raw)
        if not ReplyUnderstandingAgent._has_explicit_pain_score(raw, current_question):
            nrs = qualitative_nrs
        if nrs is None:
            nrs = fallback.get("pain_nrs")

        sleep_quality = data.get("sleep_quality")
        if sleep_quality is None:
            sleep_quality = fallback.get("sleep_quality")

        med = data.get("medication_taken")
        if isinstance(med, str):
            normalized_med = med.strip().lower()
            if normalized_med in ("true", "yes", "taken", "是", "已服用"):
                med = True
            elif normalized_med in ("false", "no", "not_taken", "未服用", "否"):
                med = False
        if med == "partial":
            med = None  # partial 视为未确定，不覆盖
        if med is None:
            med = fallback.get("medication_taken")

        side_effects = data.get("side_effects")
        if side_effects is None:
            side_effects = fallback.get("side_effects")

        conf = data.get("confidence")
        try:
            conf = float(conf) if conf is not None else 1.0
        except (TypeError, ValueError):
            conf = 1.0
        return ReplyUnderstanding(
            pain_nrs=nrs,
            sleep_quality=sleep_quality,
            medication_taken=med if isinstance(med, bool) else None,
            side_effects=side_effects,
            confidence=conf,
            uncertain=bool(data.get("uncertain", conf < 0.6)),
            evidence=data.get("evidence") or {},
            patient_requested_stop=bool(data.get("patient_requested_stop")),
            requires_immediate_action=bool(data.get("requires_immediate_action")),
            ambiguity_type=data.get("ambiguity_type", "none"),
            raw_text=raw,
        )

    @staticmethod
    def _has_explicit_pain_score(text: str, current_question: str = "") -> bool:
        text = (text or "").strip()
        if re.match(r"^(?:10|[0-9])\s*(?:分|/\s*10)?(?:[。.!！？?，,])?\s*$", text):
            return ReplyUnderstandingAgent._is_pain_question(current_question)
        # “大概5分”“差不多5分”“5分左右”仍然是患者明确给出的评分，
        # 不能因为带了口语限定词而丢弃。
        if re.search(r"(?<![\d.\-])(?:10|[0-9])\s*分", text):
            return True
        return bool(re.search(
            r"(?:疼痛|腰疼|腰痛|痛感|疼痛评分|疼痛打分|打分|几分)[^0-9]{0,10}"
            r"(?:10|[0-9])\s*(?:分|/\s*10)?",
            text,
        ))

    @staticmethod
    def _qualitative_pain_score(text: str) -> int | None:
        """将明确的重度疼痛描述映射到 NRS 下限，避免漏掉高风险患者。"""
        text = (text or "").strip()
        if any(w in text for w in ("不疼", "不太疼", "没那么疼", "疼痛不重")):
            return None
        if any(w in text for w in ("无法忍受", "疼得受不了", "痛得受不了", "疼痛难忍")):
            return 9
        if any(w in text for w in ("疼得厉害", "疼痛很厉害", "剧烈疼痛", "非常疼",
                                   "特别疼", "疼痛很重", "疼得要命")):
            return 8
        return None

    @staticmethod
    def _has_pain_value(text: str) -> bool:
        return (ReplyUnderstandingAgent._has_explicit_pain_score(text)
                or ReplyUnderstandingAgent._qualitative_pain_score(text) is not None)

    @staticmethod
    def _is_pain_question(question: str) -> bool:
        return bool(re.search(r"疼痛|痛感|疼痛评分|疼痛打分|NRS|几分", question or "", re.I))

    def _fallback(self, reply_text: str, *, current_question: str = "") -> ReplyUnderstanding:
        """无 LLM / LLM 失败时用关键词解析（降级，保证追问能收敛）。

        旧 ReplyParser（llm/parser.py）已随重构退役，这里内嵌等价的关键词解析。
        若回退路径仍对任何回复都返回空槽位，coverage 将始终不全 → TurnRouter
        每轮重复追问同一槽位，形成"患者先说完、医护智能体又说一遍相同的话"的
        重复对话（正是手动模拟患者看到的症状）。
        """
        if self._legacy_parser is not None:
            try:
                return self._from_parsed(
                    self._legacy_parser._keyword_parse(reply_text), reply_text,
                    current_question=current_question)
            except Exception:
                pass
        return self._from_parsed(
            _keyword_parse(reply_text, current_question=current_question), reply_text,
            current_question=current_question)

    @staticmethod
    def _from_parsed(parsed: dict, raw: str, current_question: str = "") -> ReplyUnderstanding:
        """把关键词解析结果归一化为 ReplyUnderstanding（兼容新旧两种 key 名）。"""
        p = parsed or {}

        def get(*keys):
            for k in keys:
                v = p.get(k)
                if v is not None:
                    return v
            return None

        parsed_any = any(get(*keys) is not None for keys in (
            ("pain_nrs", "nrs_score"), ("sleep_quality",),
            ("medication_taken",), ("side_effects",)))
        evidence = {k: raw for k in ("pain_nrs", "sleep_quality",
                                     "medication_taken", "side_effects")
                    if get(k) is not None}
        parsed_nrs = get("pain_nrs", "nrs_score")
        if parsed_nrs is not None and not (
            ReplyUnderstandingAgent._has_explicit_pain_score(raw, current_question)
            or ReplyUnderstandingAgent._qualitative_pain_score(raw) is not None
        ):
            parsed_nrs = None
        return ReplyUnderstanding(
            pain_nrs=parsed_nrs,
            sleep_quality=get("sleep_quality"),
            medication_taken=get("medication_taken"),
            side_effects=get("side_effects"),
            confidence=0.5, uncertain=True,
            ambiguity_type="none" if parsed_any else "minimal",
            evidence=evidence,
            raw_text=raw,
        )


def _keyword_parse(reply_text: str, current_question: str = "") -> dict:
    """关键词降级解析（移植自旧 llm/parser.py ReplyParser._keyword_parse）。

    返回与 ReplyUnderstanding 槽位同名的字段；解析不到则为 None。
    """
    text = reply_text or ""
    nrs = None
    # 覆盖“疼痛5分”“疼痛评分是5”“5/10”“打了5分”等常见说法，
    # 但不把年龄、睡眠时长等普通数字误当成 NRS。
    m = re.search(
        r"(?:疼痛|痛感|疼痛评分|疼痛打分|打分|评分)\s*(?:是|为|：|:)?\s*"
        r"(10|[0-9])(?:\s*分|\s*/\s*10)?"
        r"|(?<![\d.\-])(10|[0-9])\s*/\s*10"
        r"|(?<![\d.\-])(10|[0-9])\s*分",
        text,
    )
    if m:
        try:
            score = next(int(group) for group in m.groups() if group is not None)
            if 0 <= score <= 10:
                nrs = score
        except (TypeError, ValueError):
            nrs = None
    if nrs is None and ReplyUnderstandingAgent._is_pain_question(current_question):
        standalone = re.fullmatch(r"\s*(10|[0-9])\s*(?:分)?\s*[。.!！？?，,]?\s*", text)
        if standalone:
            nrs = int(standalone.group(1))
    if nrs is None:
        nrs = ReplyUnderstandingAgent._qualitative_pain_score(text)
    sleep_quality = None
    if any(w in text for w in ("睡得好", "睡得挺好", "睡得很好", "睡眠好", "睡眠很好")):
        sleep_quality = "好"
    elif any(w in text for w in ("一夜没睡", "睡不着")):
        sleep_quality = "很差"
    elif any(w in text for w in ("没睡好", "睡不好", "睡得不好")):
        sleep_quality = "差"
    elif re.search(r"睡了\s*[7-9]\s*(?:个)?小时", text):
        sleep_quality = "好"
    elif "睡" in text:
        sleep_quality = "一般"
    medication_taken = None
    if any(w in text for w in ("没吃药", "没有吃药", "忘了吃", "忘记吃", "没吃", "不吃", "停药")):
        medication_taken = False
    elif any(w in text for w in (
        "药吃了", "按时吃", "按时服药", "按时吃药", "吃了药", "服用了", "吃过药",
    )):
        medication_taken = True
    side_effects = None
    for w in ("恶心", "呕吐", "反胃", "头晕", "嗜睡", "犯困",
              "胃不舒服", "胃部不适", "皮疹", "便秘"):
        if w in text:
            side_effects = w
            break
    if side_effects is None and any(w in text for w in
                                    ("没有不舒服", "没有特别不舒服", "没发现什么不舒服",
                                     "没有副作用", "没有明显副作用", "没副作用", "没有不适",
                                     "没什么不适", "没啥不适", "没感觉", "其他都没有")):
        side_effects = "无"
    return {"pain_nrs": nrs, "sleep_quality": sleep_quality,
            "medication_taken": medication_taken, "side_effects": side_effects}
