# backend/prompts/planning/generate_followup_plan_prompt.py
"""generate_followup_plan_prompt —— 随访计划生成提示词（说明书 8.5 表）。

计划生成独立于当天调度。对应 PlanGenerationAgent。
"""
from __future__ import annotations

from dataclasses import dataclass

from prompts.prompt_spec import PromptSpec

PROMPT_VERSION = "generate-followup-plan-v1"


@dataclass
class PlanContext:
    patient_brief: str
    discharge_summary: str = ""
    retrieved_evidence: str = ""


def build_prompt(ctx: PlanContext) -> PromptSpec:
    system = f"""你是疼痛科随访计划制定助手。依据患者信息、出院小结与检索到的指南证据，
生成结构化的出院后疼痛随访计划 JSON。

输出字段：
{{
  "frequency": "随访频次，如 每日 / 每3天 / 每周",
  "duration_days": 90,
  "pain_type": "主要疼痛类型",
  "recheck_items": ["复查/记录项"],
  "medication_adjustment": "镇痛方案调整原则",
  "warning_threshold": "预警阈值（NRS≥7，或爆发痛/睡眠持续变差/不良反应时就医）",
  "health_education": ["健康教育条目"],
  "lifestyle": ["生活方式建议"]
}}
只输出 JSON，不要额外文字。"""
    user = (f"## 患者信息\n{ctx.patient_brief}\n\n"
            f"## 出院小结\n{ctx.discharge_summary or '（无）'}\n\n"
            f"## 检索证据\n{ctx.retrieved_evidence or '（无）'}\n\n"
            f"请生成随访计划 JSON：")
    return PromptSpec(system=system, user=user, temperature=0.4,
                      max_tokens=800, prompt_version=PROMPT_VERSION,
                      response_format={"type": "json_object"})
