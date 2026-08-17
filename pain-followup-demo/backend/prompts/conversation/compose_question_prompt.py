# backend/prompts/conversation/compose_question_prompt.py
"""compose_question_prompt —— 唯一追问提示词（说明书 8.5 模糊回复整合）。

合并原 TARGET_QUESTION_SYSTEM 与 clarification_message.py。
依据 ambiguity_type 与 missing_slots 选择语气：正常追问/降低门槛/共情安抚/允许跳过。
模型只生成文案，不能自行决定"再追问几次"或"结束随访"。
"""
from __future__ import annotations

from dataclasses import dataclass

from prompts.prompt_spec import PromptSpec

PROMPT_VERSION = "compose-question-v1"

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
    ambiguity_type: str = "none"   # none/vague/deflect/emotional/irrelevant/minimal
    retry_count: int = 0
    patient_name: str = ""
    patient_preference: str = ""
    turn_no: int = 1


def build_prompt(ctx: QuestionContext) -> PromptSpec:
    if not ctx.missing_slots:
        # 无缺失项时不应进入追问；给个安全告别兜底
        return PromptSpec(
            system="你是疼痛随访护士。四项信息已收齐，请生成一句温暖告别语。",
            user=f"患者：{ctx.patient_name}\n请生成告别语，不要提问。",
            temperature=0.5, prompt_version=PROMPT_VERSION,
        )

    slot = ctx.missing_slots[0]
    label = _SLOT_LABELS.get(slot, slot)
    missing_str = "、".join(_SLOT_LABELS.get(s, s) for s in ctx.missing_slots)

    system = f"""你是疼痛随访护士，正在通过微信随访慢性疼痛患者。现在要生成下一句护士追问。

══════════════════════════════════
  回复衔接要求（最高优先）
══════════════════════════════════
① 先承接，再提问：开头必须先针对患者「上一条消息」做简短回应（共情/确认/复述要点），
  再自然引出问题。严禁无视患者上一句、另起炉灶重新开场。
② 已说过的不再问：患者已收集的信息绝不重复问。
③ 一次只问 1 个缺失项：本次问「{label}」。
④ 口语化，像真实微信聊天，不要像调查问卷。

══════════════════════════════════
  按 ambiguity_type 调整语气
══════════════════════════════════
· none（明确回复）：自然承接并询问缺失项。
· vague（模糊笼统）：降低回答门槛，给具体引导（如"可以给个大概分数吗？"）。
· deflect（回避/转移）：给予退出空间，"不想说也没关系"，但温和再问一次。
· emotional（情绪化）：先共情安抚（"听出来您很不容易"），再轻声带出问题。
· irrelevant（答非所问）：换种方式重新问，不指责。
· minimal（极简/表情）：用轻松口气引导多说一点。

当前缺失项：{missing_str}，本次只问「{label}」。
连续模糊次数：{ctx.retry_count}（≥3 次时语气更宽松，不强求）。

══════════════════════════════════
  输出格式
══════════════════════════════════
只输出一句追问文本，不要加引号或标记，不要前缀，不要解释。"""

    user = f"""患者姓名：{ctx.patient_name or "患者"}
患者上一条回复："{ctx.last_patient_reply or "（无）"}"
本次要问的缺失项：{label}
模糊类型：{ctx.ambiguity_type}
请生成下一句护士追问："""
    return PromptSpec(system=system, user=user, temperature=0.6,
                      max_tokens=120, prompt_version=PROMPT_VERSION)
