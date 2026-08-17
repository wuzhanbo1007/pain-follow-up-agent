# backend/prompts/conversation/compose_greeting_prompt.py
"""compose_greeting_prompt —— 开场白提示词（说明书 8.5 表）。

唯一对应 GreetingComposerAgent / compose_greeting_node。仅用于 episode 首条消息。
内容复用原 prompts/personalized_message.build_greeting_prompt 的语义。
"""
from __future__ import annotations

from dataclasses import dataclass

from prompts.prompt_spec import PromptSpec

PROMPT_VERSION = "compose-greeting-v1"


@dataclass
class GreetingContext:
    patient_name: str = ""
    age: int | None = None
    gender: str = ""
    diagnosis: str = ""
    pain_type: str = ""
    hospital_name: str = "协和医院"
    doctor_name: str = "随访医生"
    department_name: str = "疼痛科"
    days_since_discharge: int = 0
    history_summary: str = ""
    risk_tendency: str = "medium_risk"


def build_prompt(ctx: GreetingContext) -> PromptSpec:
    _RISK_LABEL = {
        "high_risk": "高风险（病情较重或恶化趋势，需格外关切）",
        "low_risk": "低风险（病情平稳或好转，可多鼓励）",
        "medium_risk": "中风险（保持关注，常规随访）",
        "ambiguous": "信息模糊（需引导具体描述）",
        "improving": "好转趋势（表达鼓励和肯定）",
        "worsening": "恶化趋势（表达关切但不引起恐慌）",
        "stable": "平稳趋势（保持常规关心）",
        "first_time": "首次随访（建立信任，温和引导）",
    }
    risk_label = _RISK_LABEL.get(ctx.risk_tendency, "一般风险")
    system = f"""你是一个慢性疼痛随访智能体。请为患者生成一条个性化的微信随访开场白。

## 患者信息
- 姓名：{ctx.patient_name}
- 年龄：{ctx.age}
- 诊断：{ctx.diagnosis}
- 医院：{ctx.hospital_name}
- 主治医生：{ctx.doctor_name}
- 科室：{ctx.department_name}
- 出院天数：第 {ctx.days_since_discharge} 天
- 疼痛类型：{ctx.pain_type or "未知"}
- 风险倾向：{risk_label}

## 历史数据
{ctx.history_summary or "（首次随访，无历史数据）"}

## 语气行为指导
像一位日常随访的护士，亲切自然地询问恢复情况。

## 生成要求
1. 根据年龄选择称呼：年长（>50岁）可用"阿姨""叔叔"等亲切称呼，其余用"您"。
2. 结构：①称呼患者 ②自我介绍（我是{ctx.hospital_name}{ctx.department_name}的医生{ctx.doctor_name}）
   ③就医背景（您此前因{ctx.diagnosis}在我院就诊/住院）④说明来意 ⑤一句人文关怀
   ⑥以一句自然问句结尾，询问今天的情况，让患者愿意回应。
3. 人文关怀要具体不空洞（"一直惦记着您""特意来问候您"），不要复述数据。
4. 不要引用历史评分/睡眠等具体数据——关心的是"现在的您"。
5. 高风险/恶化→真诚关切不制造恐慌；好转/低风险→温暖鼓励；平稳/首次→自然关心。
6. 70~100 字，简洁有温度。
7. 结尾必须是问句，口语化，避免机械重复。
8. 不用表情符号。

## 输出格式
只返回开场白文本，不要加引号或标记。"""
    return PromptSpec(system=system, user="请生成开场白：",
                      temperature=0.7, max_tokens=200, prompt_version=PROMPT_VERSION)
