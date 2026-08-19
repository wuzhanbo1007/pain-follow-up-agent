# backend/agents/capability_agents/ai_review_agent.py
"""AI 随访审阅 Agent。

审阅既参考确定性的临床风险结果，也必须对患者本次对话中的情绪给出明确意见。
情绪只针对本次会话，不计算跨会话趋势。
"""
from __future__ import annotations

from agents.capability_agents.reply_understanding_agent import _detect_emotion
from domain.models.review import ReviewDraft
from prompts.review.review_followup_prompt import build_prompt, ReviewContext



_EMOTION_PRIORITY = {
    "unknown": 0,
    "stable": 1,
    "positive": 2,
    "low": 3,
    "distressed": 4,
    "urgent": 5,
}
_INTENSITY_PRIORITY = {"low": 0, "medium": 1, "high": 2}
_VALID_EMOTION_STATES = set(_EMOTION_PRIORITY)
_VALID_INTENSITIES = set(_INTENSITY_PRIORITY)


class AIReviewAgent:
    def __init__(self, llm_gateway=None):
        self._gw = llm_gateway

    async def review(self, *, risk_result: dict, agent_summary: str,
                     transcripts: list, plan: dict | None = None,
                     history: dict | None = None) -> ReviewDraft:
        from infrastructure.llm.llm_gateway import LLMGateway

        gw = self._gw or LLMGateway()
        emotion = _aggregate_emotion(transcripts)
        rule = self._rule_review(risk_result, transcripts, emotion=emotion)
        if not gw.is_llm_available():
            return ReviewDraft(**{**rule, "source": "rule"})

        spec = build_prompt(ReviewContext(
            risk_result=risk_result,
            agent_summary=agent_summary,
            transcripts=transcripts,
            plan=plan or {},
            history=history or {},
            emotion_assessment=emotion,
        ))
        data = await gw.chat_json(
            [{"role": "system", "content": spec.system},
             {"role": "user", "content": spec.user}],
            profile="ai_review",
        )
        if not data:
            return ReviewDraft(**{**rule, "source": "rule"})
        if not data.get("summary"):
            return ReviewDraft(**{**rule, "source": "rule"})

        state = _choose_emotion_state(emotion, data.get("emotion_state"))
        intensity = _choose_emotion_intensity(emotion, data.get("emotion_intensity"))
        emotion_summary = _build_emotion_summary(
            state, intensity, emotion.get("evidence") or [],
        )
        focus_required = state in ("low", "distressed", "urgent")

        # 无论 LLM 是否主动提到情绪，审阅摘要都必须明确显示情绪结论。
        summary = _with_emotion_summary(emotion_summary, str(data.get("summary") or ""))

        # 兼容 LLM 返回对象或字符串两种格式。
        rf = data.get("risk_flags", []) or []
        rf_str = [f"{r.get('detail', '')}" if isinstance(r, dict) else str(r)
                  for r in rf]
        sg = data.get("suggestions", []) or []
        sg_str = [s.get("content", "") if isinstance(s, dict) else str(s)
                  for s in sg]
        rf_str, sg_str = _ensure_emotion_actions(
            state, intensity, rf_str, sg_str,
        )

        # completion_score 可能是 {total, breakdown}。
        score = data.get("completion_score")
        if isinstance(score, dict):
            score = score.get("total", rule["completion_score"])
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = rule["completion_score"]
        score = max(0, min(100, score))
        result = ReviewDraft(
            summary=summary,
            risk_flags=rf_str or rule["risk_flags"],
            completion_score=score,
            suggestions=sg_str or rule["suggestions"],
            source="llm",
            risk_level=risk_result.get("level", ""),
            total_score=risk_result.get("total_score", risk_result.get("score")),
            emotion_state=state,
            emotion_intensity=intensity,
            emotion_summary=emotion_summary,
            emotion_focus_required=focus_required,
        )
        return result

    @staticmethod
    def _rule_review(risk_result: dict, transcripts: list,
                     *, emotion: dict | None = None) -> dict:
        level = (risk_result.get("level_label") or risk_result.get("level") or "未知")
        high = level in ("高风险", "high_risk", "high")
        emotion = emotion or _aggregate_emotion(transcripts)
        state = emotion["state"]
        intensity = emotion["intensity"]
        emotion_summary = _build_emotion_summary(
            state, intensity, emotion.get("evidence") or [],
        )
        count = len(transcripts) if isinstance(transcripts, list) else 0
        score = 50
        if count >= 2:
            score += 20
        if count >= 4:
            score += 10
        if high:
            score = max(0, score - 20)
        if state in ("distressed", "urgent"):
            score = max(0, score - 10)
        score = max(0, min(100, score))

        flags: list[str] = []
        if high:
            flags.append("高风险：需医生重点关注")
        for m in (transcripts or []):
            if m.get("role") == "patient" and any(
                    k in m.get("content", "")
                    for k in ("疼得厉害", "受不了", "8分", "9分", "10分", "剧痛")):
                flags.append("患者自述疼痛较重")
                break

        suggestions = ["按常规周期持续随访"]
        if high:
            suggestions = ["建议医生尽快介入评估", "确认镇痛方案是否需要调整"]
        flags, suggestions = _ensure_emotion_actions(
            state, intensity, flags, suggestions,
        )
        summary = f"共 {count} 条对话；风险等级：{level}。{emotion_summary}"
        return {
            "summary": summary,
            "risk_flags": flags,
            "completion_score": score,
            "suggestions": suggestions,
            "emotion_state": state,
            "emotion_intensity": intensity,
            "emotion_summary": emotion_summary,
            "emotion_focus_required": state in ("low", "distressed", "urgent"),
        }


def _aggregate_emotion(transcripts: list | None) -> dict:
    """只聚合患者消息，选取本次会话风险最高的明确情绪。"""
    events: list[dict] = []
    for message in transcripts or []:
        if not isinstance(message, dict) or message.get("role") != "patient":
            continue
        detected = _detect_emotion(str(message.get("content") or ""))
        if detected["state"] != "unknown":
            events.append(detected)

    if not events:
        return {"state": "unknown", "intensity": "low", "evidence": []}

    selected = max(events, key=lambda item: (
        _EMOTION_PRIORITY.get(item["state"], 0),
        _INTENSITY_PRIORITY.get(item["intensity"], 0),
    ))
    evidence = []
    for event in events:
        value = event.get("evidence")
        if value and value not in evidence:
            evidence.append(value)
    return {
        "state": selected["state"],
        "intensity": max(
            (event["intensity"] for event in events),
            key=lambda value: _INTENSITY_PRIORITY.get(value, 0),
        ),
        "evidence": evidence,
    }


def _choose_emotion_state(detected: dict, llm_state: object) -> str:
    """确定性明确情绪优先；没有明确词时才采用 LLM 的语义判断。"""
    detected_state = detected.get("state", "unknown")
    if detected_state != "unknown":
        return detected_state
    return llm_state if llm_state in _VALID_EMOTION_STATES else "unknown"


def _choose_emotion_intensity(detected: dict, llm_intensity: object) -> str:
    if detected.get("state") != "unknown":
        return detected.get("intensity", "low")
    return llm_intensity if llm_intensity in _VALID_INTENSITIES else "low"


def _build_emotion_summary(state: str, intensity: str,
                           evidence: list[str]) -> str:
    """生成医生可直接阅读的中文情绪结论。"""
    evidence_text = "、".join(evidence[:3])
    intensity_text = _intensity_label(intensity)
    if state == "urgent":
        return "患者当前情绪处于紧急风险状态，出现明确的轻生/自伤相关表达，需立即人工介入。"
    if state == "distressed":
        suffix = f"（依据：{evidence_text}）" if evidence_text else ""
        return f"患者当前情绪明显痛苦或激动，情绪强度为{intensity_text}，需要重点关注后续情绪变化。{suffix}"
    if state == "low":
        suffix = f"（依据：{evidence_text}）" if evidence_text else ""
        return f"患者当前情绪偏低落或焦虑，情绪强度为{intensity_text}，建议后续随访持续关注并给予安慰。{suffix}"
    if state == "positive":
        return "患者当前情绪总体积极，表达出状态好转或信心，建议继续给予正向肯定。"
    if state == "stable":
        return "患者当前情绪表现较平稳，未见明显情绪困扰。"
    return "当前对话未出现明确情绪表达，无法仅凭本次对话确定患者心情，建议后续随访主动询问。"


def _intensity_label(intensity: str) -> str:
    return {"low": "较低", "medium": "中等", "high": "较高"}.get(intensity, "未知")


def _with_emotion_summary(emotion_summary: str, summary: str) -> str:
    summary = summary.strip()
    if not summary:
        return emotion_summary
    if summary.startswith(emotion_summary):
        return summary
    return f"{emotion_summary} {summary}"


def _ensure_emotion_actions(state: str, intensity: str,
                            flags: list[str],
                            suggestions: list[str]) -> tuple[list[str], list[str]]:
    """保证模型或规则兜底都把情绪结论转成医生可执行的意见。"""
    flags = list(flags)
    suggestions = list(suggestions)
    if state == "urgent":
        suggestions = [s for s in suggestions if s != "按常规周期持续随访"]
        flags.insert(0, "紧急情绪风险：需立即人工介入")
        suggestions.insert(0, "立即联系医生或人工接线，重点评估患者人身安全")
    elif state == "distressed":
        suggestions = [s for s in suggestions if s != "按常规周期持续随访"]
        flags.append(f"情绪重点关注：患者情绪明显痛苦或激动（强度{_intensity_label(intensity)}）")
        suggestions.append("后续随访需重点关注患者情绪变化，必要时转人工进行心理支持")
    elif state == "low":
        flags.append("情绪重点关注：患者当前情绪偏低落或焦虑")
        suggestions.append("后续随访关注患者情绪，并使用安慰、鼓励的沟通方式")
    elif state == "positive":
        suggestions.append("继续肯定患者的好转和配合，保持积极沟通")
    elif state == "unknown":
        suggestions.append("下次随访可主动询问患者近期心情，避免仅根据症状推断情绪")
    return flags, suggestions
