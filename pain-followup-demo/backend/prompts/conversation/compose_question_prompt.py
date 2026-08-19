"""生成单轮医护追问的提示词。"""
from __future__ import annotations

from dataclasses import dataclass, field

from prompts.prompt_spec import PromptSpec


PROMPT_VERSION = "compose-question-v3-context"

_SLOT_LABELS = {
    "pain_nrs": "疼痛评分",
    "sleep_quality": "睡眠质量",
    "medication_taken": "用药依从性",
    "side_effects": "副作用",
}


@dataclass
class QuestionContext:
    last_patient_reply: str
    missing_slots: list[str]
    ambiguity_type: str = "none"
    retry_count: int = 0
    patient_name: str = ""
    patient_address: str = ""
    patient_preference: str = ""
    turn_no: int = 1
    emotion_state: str = "unknown"
    emotion_intensity: str = "low"
    current_question: str = ""
    history_summary: str = ""
    recent_messages: list[dict] = field(default_factory=list)
    known_slots: dict = field(default_factory=dict)


def _format_recent_messages(messages: list[dict]) -> str:
    lines = []
    for message in messages or []:
        role = "医护" if message.get("role") in ("nurse", "assistant") else "患者"
        content = str(message.get("content") or "").strip()
        if content:
            lines.append(f"{role}：{content[:240]}")
    return "\n".join(lines) if lines else "（无）"


def _format_known_slots(slots: dict) -> str:
    if not slots:
        return "（暂无已确认信息）"
    values = []
    for key, value in slots.items():
        if value is not None:
            values.append(f"{_SLOT_LABELS.get(key, key)}={value}")
    return "；".join(values) if values else "（暂无已确认信息）"


def build_prompt(ctx: QuestionContext) -> PromptSpec:
    if not ctx.missing_slots:
        return PromptSpec(
            system="你是慢性疼痛随访医护人员。信息已经收集完整，请生成一句温暖的告别语，不要提问。",
            user=f"患者称呼：{ctx.patient_address or '患者'}\n请生成告别语，不要直接使用患者全名。",
            temperature=0.5,
            prompt_version=PROMPT_VERSION,
        )

    slot = ctx.missing_slots[0]
    label = _SLOT_LABELS.get(slot, slot)
    missing_str = "、".join(_SLOT_LABELS.get(s, s) for s in ctx.missing_slots)
    address = ctx.patient_address or "患者"
    recent_dialogue = _format_recent_messages(ctx.recent_messages)
    known_slots = _format_known_slots(ctx.known_slots)

    system = f"""你是慢性疼痛随访医护人员，正在通过微信进行自然、温和的随访。

上下文理解规则：
1. 先判断患者刚刚的回复是在回答上一句医护问题的哪一部分，再生成下一句；不能脱离上一问解释患者回复。
2. 如果上一句明确询问疼痛评分，患者回复“8分”就是疼痛评分NRS=8，不是“整体状态评分”；NRS 7-10分表示疼痛程度较高、患者通常会明显不舒服。
3. 已确认信息不能被重复追问；本次只询问当前缺失项目“{label}”。患者已经明确给出疼痛评分（包括7.5这类小数）时，直接视为疼痛评分已收集，不要为了区分“当前值/近期平均值”再问同一个评分；当前数据模型只有一个疼痛评分槽位。
4. 开场白已经称呼过患者，后续追问不要每次都以“{address}”开头，通常直接使用“您”或直接承接内容；禁止直接使用患者全名。
5. 如果患者明确说疼痛明显、很难受或今天状态不好，先承接身体不适，例如“听起来您现在确实很难受，我很担心”；不要用“辛苦了”“加油”“坚持一下”等客套话代替回应。
6. NRS 7-10分时禁止说“整体状态还不错”“状态不错”“挺好的”等与高疼痛不一致的话，应表达关心并继续重点了解情况。

回复衔接要求：
1. 先承接患者上一条回复，再自然提出问题。
2. 一次只询问一个缺失项目，本次只问“{label}”。
3. 口语化，像真实微信聊天，不要像调查问卷。
4. 不要解释规则，不要输出标题，不要使用表情符号。

根据模糊类型调整语气：
· none：自然承接并询问缺失项。
· vague：降低回答门槛，给出具体例子。
· deflect：允许患者暂时不回答，但温和再问一次。
· emotional：先共情安慰，再轻声询问。
· irrelevant：换种方式重新问，不指责。
· minimal：轻松引导患者多说一点。

根据当前情绪调整：
· positive：先肯定患者的好转和努力，再提问。
· stable：保持平和、自然、简洁。
· low：先安慰和接纳感受，再温和提问。
· distressed：先稳定情绪、降低压力，只询问最必要的信息。
· unknown：不要擅自判断患者情绪。

当前情绪：{ctx.emotion_state}（强度：{ctx.emotion_intensity}）。
当前缺失项目：{missing_str}。
连续模糊次数：{ctx.retry_count}。

只输出下一句医护追问，不要直接使用患者全名。"""

    user = f"""上一句医护问题：{ctx.current_question or '（无）'}
患者刚刚回复：{ctx.last_patient_reply or '（无）'}
前情摘要：{ctx.history_summary or '（无）'}
最近对话：
{recent_dialogue}
已确认信息：{known_slots}
患者姓名（仅用于识别，禁止原样称呼）：{ctx.patient_name or '患者'}
患者称呼（仅在确有必要时使用，不能每次开头重复）：{address}
本次要问的缺失项：{label}
模糊类型：{ctx.ambiguity_type}
当前情绪：{ctx.emotion_state}（强度：{ctx.emotion_intensity}）
请基于以上上下文生成下一句医护追问："""
    return PromptSpec(
        system=system,
        user=user,
        temperature=0.6,
        max_tokens=140,
        prompt_version=PROMPT_VERSION,
    )
