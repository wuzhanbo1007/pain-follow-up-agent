# backend/prompts/review/review_followup_prompt.py
"""review_followup_prompt —— AI 审阅提示词（说明书 8.4）。

单患者结束后触发，不能直接告警或写库。
内容复用原 prompts/review_analysis.build_prompt（输出结构化审阅），
输出对齐 domain.models.ReviewDraft。
"""
from __future__ import annotations

from dataclasses import dataclass

from prompts.prompt_spec import PromptSpec

PROMPT_VERSION = "review-followup-v1"


@dataclass
class ReviewContext:
    risk_result: dict = None       # 确定性风险结果（由图注入，非 LLM 产出）
    agent_summary: str = ""
    transcripts: list = None        # [{role, content}]
    plan: dict = None
    history: dict = None            # query_patient_history 返回


def build_prompt(ctx: ReviewContext) -> PromptSpec:
    risk = ctx.risk_result or {}
    risk_level = risk.get("level_label") or risk.get("level") or "未知"
    total_score = risk.get("total_score", risk.get("score"))

    lines = []
    for m in (ctx.transcripts or []):
        role = "护士" if m.get("role") == "nurse" else "患者"
        lines.append(f"{role}：{m.get('content', '')}")
    conv_text = "\n".join(lines) if lines else "（无对话记录）"

    plan_text = ""
    if ctx.plan:
        p = ctx.plan
        plan_text = (f"## 随访计划\n- 频次：{p.get('frequency','未知')}\n"
                     f"- 周期：{p.get('duration_days','未知')} 天\n"
                     f"- 疼痛类型：{p.get('pain_type','未知')}\n"
                     f"- 预警阈值：{p.get('warning_threshold','未知')}\n")

    history_text = ""
    rows = ((ctx.history or {}).get("history")) or []
    if rows:
        hist_lines = []
        for r in rows[-7:]:
            med = r.get("medication_taken")
            med_txt = {True: "是", False: "否", None: "?"}.get(med, med)
            hist_lines.append(f"- {r.get('date','?')}：NRS={r.get('nrs_score')}，"
                              f"睡眠={r.get('sleep_quality')}，按时用药={med_txt}")
        history_text = "\n## 患者近 7 天历史随访\n" + "\n".join(hist_lines) + \
            "\n\n请对照历史判断趋势：本次较前是改善、恶化还是持平。"

    system = f"""你是一位慢性疼痛随访质控医生，请对患者随访会话做结构化智能审阅。

## 风险引擎结果
- 风险等级：{risk_level}
- 风险总分：{total_score}
- 系统摘要：{ctx.agent_summary}
{plan_text}{history_text}

## 随访对话记录
{conv_text}

## 审阅维度
1. summary：1-2 句概括本次随访收集与核心发现。
2. risk_flags：值得医生关注的实际风险（无则空数组），每项含 type/detail/severity(高/中/低)。
3. completion_score：0-100 整数（信息完整度/交互质量/风险识别/患者情绪各约25分）。
4. suggestions：改进建议，每项含 priority(P0/P1/P2)/content。

## 输出格式（严格 JSON）
{{
  "summary": "...",
  "risk_flags": [{{"type":"...","detail":"...","severity":"..."}}],
  "completion_score": 85,
  "suggestions": [{{"priority":"P1","content":"..."}}]
}}"""
    return PromptSpec(system=system, user="请输出审阅 JSON：",
                      temperature=0.2, max_tokens=600, prompt_version=PROMPT_VERSION,
                      response_format={"type": "json_object"})
