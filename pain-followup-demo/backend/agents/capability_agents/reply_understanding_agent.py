# backend/agents/capability_agents/reply_understanding_agent.py
"""ReplyUnderstandingAgent —— 患者回复理解（说明书 8.5 表）。

LLM 每轮只做结构化理解；程序根据 Policy 计算 coverage、风险、下一节点。
输出经 ReplyUnderstanding Pydantic 校验；失败回退关键词解析。
"""
from __future__ import annotations

import re

from domain.models.reply_understanding import ReplyUnderstanding
from prompts.conversation.understand_reply_prompt import build_prompt, UnderstandContext


_NRS_NUMBER = r"(?:10(?:\.0+)?|[0-9](?:\.[0-9]+)?)"

def _normalize_nrs_value(value):
    """把LLM/关键词解析出的NRS规范为0-10范围内、最多1位小数。"""
    try:
        score = round(float(value), 1)
    except (TypeError, ValueError):
        return None
    if not 0 <= score <= 10:
        return None
    return score


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
            result = self._normalize(data, reply_text, current_question=current_question)
            return result
        except Exception as exc:
            return self._fallback(reply_text, current_question=current_question)

    @staticmethod
    def _normalize(data: dict, raw: str, current_question: str = "") -> ReplyUnderstanding:
        # LLM 可能只返回部分字段，或者把“睡得很好”“按时服药”这类
        # 明确表达留在原文而没有填入 JSON。用同一份原文做确定性补全，
        # 只补 None，不覆盖 LLM 已确认的值。
        fallback = _keyword_parse(raw, current_question=current_question)

        # pain_nrs 0-10，允许1位小数（如7.5）
        nrs = _normalize_nrs_value(data.get("pain_nrs"))
        # 数字评分优先；没有数字时，明确的重度疼痛描述必须落入 NRS 8–10，
        # 防止 LLM 将“疼得厉害”误归为低分。
        qualitative_nrs = ReplyUnderstandingAgent._qualitative_pain_score(raw)
        if not ReplyUnderstandingAgent._has_explicit_pain_score(raw, current_question):
            nrs = qualitative_nrs
        if nrs is None:
            nrs = fallback.get("pain_nrs")

        explicit_sleep_quality = _sleep_quality_from_hours(raw)
        sleep_quality = (
            explicit_sleep_quality
            if explicit_sleep_quality is not None
            else data.get("sleep_quality")
        )
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
        emotion = _detect_emotion(raw)
        llm_emotion = data.get("emotion_state")
        allowed_emotions = {"positive", "stable", "low", "distressed", "urgent", "unknown"}
        if llm_emotion not in allowed_emotions:
            llm_emotion = "unknown"
        # 明确的原文规则优先于模型自由发挥；尤其是紧急表达不能漏判。
        emotion_state = emotion["state"] if emotion["state"] != "unknown" else llm_emotion
        intensity = emotion["intensity"] if emotion["state"] != "unknown" else data.get("emotion_intensity", "low")
        if intensity not in {"low", "medium", "high"}:
            intensity = "low"
        emotion_evidence = emotion["evidence"] or str(data.get("emotion_evidence") or "")
        return ReplyUnderstanding(
            pain_nrs=nrs,
            sleep_quality=sleep_quality,
            medication_taken=med if isinstance(med, bool) else None,
            side_effects=side_effects,
            confidence=conf,
            uncertain=bool(data.get("uncertain", conf < 0.6)),
            evidence=data.get("evidence") or {},
            patient_requested_stop=bool(data.get("patient_requested_stop")),
            requires_immediate_action=(
                bool(data.get("requires_immediate_action"))
                or emotion_state == "urgent"
            ),
            emotion_state=emotion_state,
            emotion_intensity=intensity,
            emotion_evidence=emotion_evidence,
            ambiguity_type=data.get("ambiguity_type", "none"),
            raw_text=raw,
        )

    @staticmethod
    def _has_explicit_pain_score(text: str, current_question: str = "") -> bool:
        text = (text or "").strip()
        if re.match(
            rf"^\s*{_NRS_NUMBER}\s*(?:分|/\s*10)?(?:[。.!！？?，,])?\s*$",
            text,
        ):
            return ReplyUnderstandingAgent._is_pain_question(current_question)
        # “大概7.5分”“差不多5分”“5分左右”仍然是患者明确给出的评分，
        # 不能因为带了口语限定词而丢弃。
        if re.search(rf"(?<![\d.\-]){_NRS_NUMBER}\s*分(?![\d.])", text):
            return True
        return bool(re.search(
            r"(?:疼痛|腰疼|腰痛|痛感|疼痛评分|疼痛打分|打分|几分)[^0-9]{0,10}"
            rf"{_NRS_NUMBER}\s*(?:分|/\s*10)?(?![\d.])",
            text,
        ))

    @staticmethod
    def _qualitative_pain_score(text: str) -> float | None:
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
        parsed_nrs = _normalize_nrs_value(get("pain_nrs", "nrs_score"))
        if parsed_nrs is not None and not (
            ReplyUnderstandingAgent._has_explicit_pain_score(raw, current_question)
            or ReplyUnderstandingAgent._qualitative_pain_score(raw) is not None
        ):
            parsed_nrs = None
        emotion = _detect_emotion(raw)
        return ReplyUnderstanding(
            pain_nrs=parsed_nrs,
            sleep_quality=get("sleep_quality"),
            medication_taken=get("medication_taken"),
            side_effects=get("side_effects"),
            confidence=0.5, uncertain=True,
            ambiguity_type="none" if parsed_any else "minimal",
            evidence=evidence,
            requires_immediate_action=emotion["state"] == "urgent",
            emotion_state=emotion["state"],
            emotion_intensity=emotion["intensity"],
            emotion_evidence=emotion["evidence"],
            raw_text=raw,
        )


def _detect_emotion(text: str) -> dict[str, str]:
    """对当前单条患者消息做轻量规则识别，作为 LLM 的安全兜底。"""
    text = (text or "").strip()
    if not text:
        return {"state": "unknown", "intensity": "low", "evidence": ""}

    rules = (
        ("urgent", "high", (
            "我不想活了", "不想活下去", "不想活", "想自杀", "要自杀",
            "想轻生", "轻生", "伤害自己", "伤害我自己", "自伤", "自残",
            "自杀念头", "自杀想法", "结束生命", "活着没意义",
        )),
        ("distressed", "high", (
            "崩溃", "绝望", "太痛苦了", "痛苦得受不了", "受不了了",
            "撑不下去", "非常害怕", "情绪比较激动", "情绪很激动",
            "非常激动", "很激动", "烦躁", "愤怒", "发脾气", "控制不住情绪",
        )),
        ("low", "medium", (
            "心情不好", "情绪低落", "很难过", "难过", "沮丧", "焦虑",
            "无助", "担心", "害怕", "心里堵得慌",
        )),
        ("positive", "low", (
            "好多了", "好很多了", "心情不错", "挺开心", "很开心",
            "高兴", "精神好多了", "恢复得不错", "有信心", "满意",
        )),
        ("stable", "low", (
            "还可以", "还行", "一般", "挺好的", "很好", "比较平稳",
            "没什么变化", "老样子",
        )),
    )
    for state, intensity, phrases in rules:
        for phrase in phrases:
            # “睡得一般”“疼痛老样子”“按时吃药”等是在回答具体健康问题，
            # 不能仅因为包含“一般/老样子/还行”就判断患者整体情绪平稳。
            if state == "stable" and _has_clinical_context(text):
                continue
            if _has_non_negated_phrase(text, phrase):
                return {"state": state, "intensity": intensity, "evidence": phrase}
    return {"state": "unknown", "intensity": "low", "evidence": ""}


def _has_clinical_context(text: str) -> bool:
    """判断“还行/一般”等词是否只是具体症状或用药问题的回答。"""
    return any(word in text for word in (
        "睡", "疼", "痛", "药", "服药", "吃药", "用药", "副作用",
        "不舒服", "小时", "入睡", "醒", "恶心", "头晕", "便秘",
    ))


def _has_non_negated_phrase(text: str, phrase: str) -> bool:
    """判断文本中是否存在未被否定的目标词组。"""
    start = 0
    while True:
        index = text.find(phrase, start)
        if index < 0:
            return False
        if not _is_negated(text, phrase, index):
            return True
        start = index + len(phrase)


def _is_negated(text: str, phrase: str, index: int | None = None) -> bool:
    """避免将明确否定的情绪词组误判为患者当前情绪。"""
    if index is None:
        index = text.find(phrase)
    if index < 0:
        return False

    prefix = text[max(0, index - 8):index]
    negations = (
        "并没有", "没有", "从来没有", "没有那么", "没有再", "没那么",
        "没再", "并不", "不是", "不觉得", "没", "不", "否认",
    )
    return any(negation in prefix for negation in negations)


def _keyword_parse(reply_text: str, current_question: str = "") -> dict:
    """关键词降级解析（移植自旧 llm/parser.py ReplyParser._keyword_parse）。

    返回与 ReplyUnderstanding 槽位同名的字段；解析不到则为 None。
    """
    text = reply_text or ""
    nrs = None
    # 覆盖“疼痛5分”“疼痛评分是5”“5/10”“打了5分”等常见说法，
    # 但不把年龄、睡眠时长等普通数字误当成 NRS。
    m = re.search(
        rf"(?:疼痛|痛感|疼痛评分|疼痛打分|打分|评分)\s*(?:是|为|：|:)?\s*"
        rf"({_NRS_NUMBER})(?![\d.])(?:\s*分|\s*/\s*10)?"
        rf"|(?<![\d.\-])({_NRS_NUMBER})\s*/\s*10"
        rf"|(?<![\d.\-])({_NRS_NUMBER})\s*分(?![\d.])",
        text,
    )
    if m:
        score_text = next(group for group in m.groups() if group is not None)
        nrs = _normalize_nrs_value(score_text)
    if nrs is None and ReplyUnderstandingAgent._is_pain_question(current_question):
        standalone = re.fullmatch(
            rf"\s*({_NRS_NUMBER})\s*(?:分)?\s*[。.!！？?，,]?\s*", text,
        )
        if standalone:
            nrs = _normalize_nrs_value(standalone.group(1))
    if nrs is None:
        nrs = ReplyUnderstandingAgent._qualitative_pain_score(text)
    sleep_quality = None
    if any(w in text for w in ("睡得好", "睡得挺好", "睡得很好", "睡眠好", "睡眠很好")):
        sleep_quality = "好"
    elif any(w in text for w in ("一夜没睡", "睡不着")):
        sleep_quality = "很差"
    elif any(w in text for w in ("没睡好", "睡不好", "睡得不好")):
        sleep_quality = "差"
    else:
        sleep_hours = _extract_sleep_hours(text)
        if sleep_hours is not None:
            if sleep_hours <= 2:
                sleep_quality = "很差"
            elif sleep_hours <= 4:
                sleep_quality = "差"
            elif sleep_hours <= 6:
                sleep_quality = "一般"
            else:
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


def _extract_sleep_hours(text: str) -> float | None:
    """提取“睡了5个小时/睡了两个小时”等表达。"""
    match = re.search(
        r"(?:睡了|睡眠|总共睡|睡够|睡了大概)\s*"
        r"([0-9]+(?:\.[0-9]+)?|[一二两三四五六七八九十]+)\s*(?:个)?小时",
        text,
    )
    if not match:
        return None
    value = match.group(1)
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        return float(value)
    chinese_digits = {
        "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    if value == "十":
        return 10.0
    if value.startswith("十"):
        return 10.0 + chinese_digits.get(value[1:], 0)
    if value.endswith("十"):
        return chinese_digits.get(value[:-1], 0) * 10.0
    if len(value) == 2 and value[1] == "十":
        return chinese_digits.get(value[0], 0) * 10.0
    if len(value) == 2 and value[0] == "十":
        return 10.0 + chinese_digits.get(value[1], 0)
    if len(value) == 1:
        return float(chinese_digits.get(value, 0))
    return None


def _sleep_quality_from_hours(text: str) -> str | None:
    hours = _extract_sleep_hours(text or "")
    if hours is None:
        return None
    if hours <= 2:
        return "很差"
    if hours <= 4:
        return "差"
    if hours <= 6:
        return "一般"
    return "好"
