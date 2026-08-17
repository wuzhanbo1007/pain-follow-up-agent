# backend/prompts/planning/plan_system_guardrails_prompt.py
"""plan_system_guardrails_prompt —— 计划生成系统护栏提示词（说明书 8.5 表）。

只服务 PlanGenerationAgent 的系统消息，禁止被其他 Agent 导入。
"""
from __future__ import annotations

from prompts.prompt_spec import PromptSpec

PROMPT_VERSION = "plan-system-guardrails-v1"

SYSTEM_TEXT = (
    "你是疼痛科随访计划制定助手。必须输出合法 JSON。"
    "随访频次应来自出院处方；NRS≥7 或出现爆发痛、睡眠持续变差、药物不良反应时需预警就医。"
    "健康教育须包含按时服药、每日记录 NRS、出现爆发痛及时联系主管医生。"
    "不得输出任何超出 JSON 结构的文字。"
)


def build_prompt() -> PromptSpec:
    return PromptSpec(system=SYSTEM_TEXT, user="",
                      temperature=0.4, prompt_version=PROMPT_VERSION)
