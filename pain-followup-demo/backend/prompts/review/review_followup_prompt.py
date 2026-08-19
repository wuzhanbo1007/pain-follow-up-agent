# backend/prompts/review/review_followup_prompt.py
"""review_followup_prompt —— AI 审阅提示词（说明书 8.4）。

单患者结束后触发，不能直接告警或写库。
内容复用原 prompts/review_analysis.build_prompt（输出结构化审阅），
输出对齐 domain.models.ReviewDraft。
"""
from __future__ import annotations

from dataclasses import dataclass

from prompts.prompt_spec import PromptSpec

PROMPT_VERSION = "review-followup-v4-semantic-emotion"


@dataclass
class ReviewContext:
    risk_result: dict = None       # 确定性风险结果（由图注入，非 LLM 产出）
    agent_summary: str = ""
    transcripts: list = None        # [{role, content}]
    plan: dict = None
    history: dict = None            # query_patient_history 返回
    emotion_assessment: dict = None # 程序从患者消息提取的本次情绪依据


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

    emotion = ctx.emotion_assessment or {}
    emotion_text = (
        f"- 程序识别的当前情绪：{emotion.get('state', 'unknown')}\n"
        f"- 情绪强度：{emotion.get('intensity', 'low')}\n"
        f"- 患者原文依据：{', '.join(emotion.get('evidence') or []) or '无明确情绪词'}\n"
        "以上仅是程序安全识别结果，不是最终情绪结论。你必须结合全部患者原话和对话上下文做语义判断，"
        "不得只依赖关键词。若患者在回答整体感受时表达恢复欠佳、状态一般、生活受影响或对病情感到困扰，"
        "可判为 low 并说明依据；但不得仅凭疼痛分数、睡眠时长或用药情况推断情绪。"
        "若为 urgent/distressed，必须在审阅意见中明确写出并提示重点关注。"
    )
    system = f"""你是一位慢性疼痛随访质控医生，请对患者随访会话做结构化智能审阅。

## 风险引擎结果
- 风险等级：{risk_level}
- 风险总分：{total_score}
- 系统摘要：{ctx.agent_summary}
{plan_text}{history_text}

## 患者情绪评估依据
{emotion_text}

## 随访对话记录
{conv_text}

## 审阅维度
输出必须精炼：summary、emotion_summary 各不超过 100 个中文字符；suggestions 最多 3 项，每项 content 不超过 100 个中文字符；risk_flags 最多 3 项，每项 detail 不超过 80 个中文字符。不要重复对话原文或解释推理过程。
1. summary：1-2 句概括本次随访收集与核心发现，不超过 100 个中文字符。
2. risk_flags：值得医生关注的实际风险（无则空数组），最多 3 项，每项含 type/detail/severity(高/中/低)，detail 不超过 80 个中文字符。
3. completion_score：0-100 整数（信息完整度/交互质量/风险识别/患者情绪各约25分）。
4. suggestions：改进建议，最多 3 项，每项含 priority(P0/P1/P2)/content，content 不超过 100 个中文字符。
5. emotion_state：只能从 positive/stable/low/distressed/urgent/unknown 中选择。
6. emotion_intensity：只能从 low/medium/high 中选择。
7. emotion_summary：用中文明确说明患者本次对话中的心情，不超过 100 个中文字符。先按整段语义判断，而非关键词匹配；只有确实缺乏情绪或整体状态依据时，才写“当前对话未出现明确情绪表达，无法仅凭本次对话确定患者心情”。
8. emotion_focus_required：low/distressed/urgent 时为 true，并在 suggestions 中提出后续关注建议；positive/stable/unknown 通常为 false。

## 输出格式（严格 JSON）
{{
  "summary": "...",
  "risk_flags": [{{"type":"...","detail":"...","severity":"..."}}],
  "completion_score": 85,
  "suggestions": [{{"priority":"P1","content":"..."}}],
  "emotion_state": "unknown",
  "emotion_intensity": "low",
  "emotion_summary": "...",
  "emotion_focus_required": false
}}"""
    return PromptSpec(system=system, user="请输出审阅 JSON：",
                      temperature=0.2, max_tokens=600, prompt_version=PROMPT_VERSION,
                      response_format={"type": "json_object"})
