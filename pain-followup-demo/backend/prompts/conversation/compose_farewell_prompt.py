"""Prompt for the closing message of a follow-up conversation."""
from __future__ import annotations

from dataclasses import dataclass

from prompts.prompt_spec import PromptSpec


PROMPT_VERSION = "compose-farewell-v2-address"


@dataclass
class FarewellContext:
    patient_name: str = ""
    patient_address: str = ""
    diagnosis: str = ""
    pain_type: str = ""
    risk_level: str = "medium"
    nrs_score: float | None = None
    sleep_quality: str | None = None
    medication_status: str | None = None
    side_effects: str | None = None
    incomplete: bool = False


def build_prompt(ctx: FarewellContext) -> PromptSpec:
    nrs_str = str(ctx.nrs_score) if ctx.nrs_score is not None else "未提及"
    sleep_str = ctx.sleep_quality or "未提及"
    med_str = ctx.medication_status or "未提及"
    side_effects_str = ctx.side_effects or "未提及"
    address = ctx.patient_address or "您"
    finish_type = "不完整结束（部分信息未收集到）" if ctx.incomplete else "完整结束"

    system = f"""你是一名慢性疼痛随访医护人员，请生成一条自然、温和的微信告别语。

患者信息：
- 患者姓名（仅用于识别，禁止在对话中直接使用全名）：{ctx.patient_name}
- 本次必须使用的自然称呼：{address}
- 诊断：{ctx.diagnosis}
- 疼痛类型：{ctx.pain_type or "未知"}

本次随访信息：
- 疼痛评分（NRS，0-10分）：{nrs_str}
- 睡眠情况：{sleep_str}
- 用药情况：{med_str}
- 不良反应：{side_effects_str}
- 风险等级：{ctx.risk_level}
- 结束类型：{finish_type}

生成要求：
1. 开场白已经称呼过患者，告别语不要每次都以“{address}”开头，通常直接使用“您”或直接承接反馈；禁止直接说出患者全称。
2. 先简短回应患者本次反馈，再给出与本次信息相关的关怀或建议。
3. NRS 7-10 分表示疼痛程度较高、患者通常会明显不舒服，应明确表达关心，并说明会反馈医生或建议重点关注；不要轻描淡写地说“还不错”。
4. 睡眠差时可建议白天适当活动、规律作息；用药不规律时提醒按医嘱用药；有不良反应时只做简短关注提示，不擅自调整药物。
5. 不完整结束时语气温和，不继续追问，说明下次再详细沟通；完整结束时也不要追加问题。
6. 只输出50到90字的告别语，不要标题、列表、引号、表情符号或解释。
"""
    return PromptSpec(
        system=system,
        user="请生成告别语。",
        temperature=0.5,
        max_tokens=150,
        prompt_version=PROMPT_VERSION,
    )
