"""
D 号审阅 Agent 的审阅分析 Prompt

输出结构化审阅结果：summary / risk_flags（含 severity）/ completion_score（4 维拆分）/
urgency_level / suggestions（含优先级）/ follow_up_actions。
"""

import json


def build_prompt(session: dict, transcripts: list, plan: dict | None = None) -> str:
    """构建 D 号审阅 Prompt。

    Args:
        session: 会话字典（含 risk_result / agent_summary 等）
        transcripts: 对话记录 [{"role": "nurse"/"patient", "content": ...}]
        plan: 随访计划（可选，用于对比审阅）
    Returns:
        str: 完整 prompt
    """
    risk_result = session.get("risk_result", {}) or {}
    risk_level = risk_result.get("level_label") or risk_result.get("level") or "未知"
    total_score = risk_result.get("total_score")
    agent_summary = session.get("agent_summary", "")

    # 对话文本
    lines = []
    for m in transcripts:
        role = "护士" if m.get("role") == "nurse" else "患者"
        lines.append(f"{role}：{m.get('content', '')}")
    conv_text = "\n".join(lines) if lines else "（无对话记录）"

    # 随访计划摘要
    plan_text = ""
    if plan:
        plan_text = f"""## 随访计划
- 频次：{plan.get('frequency', '未知')}
- 周期：{plan.get('duration_days', '未知')} 天
- 疼痛类型：{plan.get('pain_type', '未知')}
- 预警阈值：{plan.get('warning_threshold', '未知')}
"""

    return f"""你是一位慢性疼痛随访质控医生，请对下面这份患者随访会话做结构化智能审阅。

══════════════════════════════════
  会话背景
══════════════════════════════════

## 风险引擎结果
- 风险等级：{risk_level}
- 风险总分：{total_score}
- 系统摘要：{agent_summary}
{plan_text}

══════════════════════════════════
  随访对话记录
══════════════════════════════════
{conv_text}

══════════════════════════════════
  审阅维度与评分锚点
══════════════════════════════════

1. summary（会话摘要）
   用 1-2 句话概括：本次随访完成了哪些信息收集、核心发现是什么、是否需要关注。

2. risk_flags（风险标记，含严重度）
   仅列出值得医生关注的实际风险，无则空数组。
   每个标记包含：
   - type: 风险类型（"高疼痛"/"用药中断"/"副作用"/"情绪异常"/"信息缺失"/"随访中断"等）
   - detail: 一句话描述（引用对话原文关键句）
   - severity: "高"（需立即处理）/ "中"（下次随访前关注）/ "低"（常规关注）

3. completion_score（完成度评分，拆分为 4 个子维度）
   每个子维度 0-25 分，总分 0-100：
   ┌────────────────────┬──────────────────────────────────────────┐
   │ 信息完整度 (0-25)  │ 四项关键信息收集了几项？每项约 6 分       │
   │ 交互质量   (0-25)  │ 对话自然度、追问是否恰当、有无机械感       │
   │ 风险识别   (0-25)  │ 是否准确识别了高风险信号？漏了扣分         │
   │ 患者情绪   (0-25)  │ 护士是否表达共情？情绪管理是否妥当？       │
   └────────────────────┴──────────────────────────────────────────┘

4. urgency_level（医生查看紧急度）
   - "immediate": 建议医生立即查看——NRS≥7、出现严重副作用、患者表达绝望/极端情绪
   - "today": 建议当天查看——NRS 5-6、用药中断、情绪明显低落
   - "routine": 常规审阅即可——信息完整、低风险、平稳

5. suggestions（改进建议，含优先级）
   P0（必须改进）→ 信息严重缺失、高风险漏判
   P1（建议改进）→ 追问策略可优化、语气可更自然
   P2（锦上添花）→ 细节打磨
   每条建议包含："priority": "P0"/"P1"/"P2", "content": "建议内容"

6. follow_up_actions（后续动作建议）
   列出医生可以做的一两项具体跟进动作：
   - 如："建议医生主动联系患者确认疼痛评分，当前 NRS=8 已超过预警阈值"
   - 如："下次随访时重点询问副作用（便秘），本次患者未回应"
   - 无特别需要则空数组

══════════════════════════════════
  输出格式（严格 JSON，不要任何额外内容）
══════════════════════════════════

{{
  "summary": "1-2句话会话摘要",
  "risk_flags": [
    {{
      "type": "高疼痛",
      "detail": "患者报告NRS=8，超过预警阈值",
      "severity": "高"
    }}
  ],
  "completion_score": {{
    "total": 85,
    "breakdown": {{
      "info_completeness": 22,
      "interaction_quality": 20,
      "risk_detection": 23,
      "empathy_handling": 20
    }}
  }},
  "urgency_level": "routine",
  "suggestions": [
    {{ "priority": "P1", "content": "患者回复'还行'时未追问具体评分，建议下次更精准追问" }}
  ],
  "follow_up_actions": [
    "建议医生在48小时内确认患者用药情况"
  ]
}}"""
