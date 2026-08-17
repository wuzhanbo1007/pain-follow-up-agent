# backend/prompts/conversation/compose_farewell_prompt.py
"""compose_farewell_prompt —— 告别语提示词（说明书 8.5 表）。

只在 TurnRouter=complete/incomplete_handoff 后调用。
内容复用原 prompts/personalized_message.build_farewell_prompt 的语义。
"""
from __future__ import annotations

from dataclasses import dataclass

from prompts.prompt_spec import PromptSpec

PROMPT_VERSION = "compose-farewell-v1"


@dataclass
class FarewellContext:
    patient_name: str = ""
    diagnosis: str = ""
    pain_type: str = ""
    risk_level: str = "medium"          # high/medium/low
    nrs_score: int | None = None
    sleep_quality: str | None = None
    medication_status: str | None = None
    side_effects: str | None = None
    incomplete: bool = False           # 不完整结束（保留缺失项）


def build_prompt(ctx: FarewellContext) -> PromptSpec:
    nrs_str = str(ctx.nrs_score) if ctx.nrs_score is not None else "未提及"
    sleep_str = ctx.sleep_quality or "未提及"
    med_str = ctx.medication_status or "未提及"
    se_str = ctx.side_effects or "未提及"
    system = f"""你是一个慢性疼痛随访智能体。请为患者生成一条个性化的微信随访告别语。

## 患者信息
- 姓名：{ctx.patient_name}
- 诊断：{ctx.diagnosis}
- 疼痛类型：{ctx.pain_type or "未知"}

## 本次随访情况
- 疼痛评分（NRS）：{nrs_str} 分
- 睡眠情况：{sleep_str}
- 用药情况：{med_str}
- 副作用情况：{se_str}
- 风险等级：{ctx.risk_level}
- 结束类型：{'不完整结束（部分信息未收集到）' if ctx.incomplete else '完整结束'}

## 生成要求
1.【回应患者本次反馈】先对患者今天说的内容做简短回应。
   NRS≤3→表达高兴鼓励；4-6→表达关心；≥7→表达格外关切并说会反馈给医生；未提及→自然跳过。
2.【睡眠反馈】差/很差→"白天适当活动，晚上更容易入睡"；好→简单肯定。
3.【用药叮嘱】false→"药要按时吃，忘一次没关系别连续忘"；partial→"尽量同一时间服药，设个闹钟"；true→简单肯定。
4.【副作用叮嘱】具体症状→根据症状简短关怀(便秘→多喝温水吃蔬菜)；无/否认→不提。
5.【不完整结束】温和说明，不追问，"下次再详细聊聊"。
6.【格式】不用表情；50~90字；绝对不要提问（这是告别语）；不要过于口语化的语气词。

## 输出格式
只返回告别语文本，不要加引号或标记。"""
    return PromptSpec(system=system, user="请生成告别语：",
                      temperature=0.5, max_tokens=150, prompt_version=PROMPT_VERSION)
