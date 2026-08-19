# backend/prompts/conversation/summarize_history_prompt.py
"""summarize_history_prompt —— 滚动摘要提示词（说明书 8.5 表）。

仅在会话超过上下文窗口时调用，把被截断的早期消息压成累计摘要。
内容复用原 prompts/react_prompts.build_summary_prompt。
"""
from __future__ import annotations

from dataclasses import dataclass

from prompts.prompt_spec import PromptSpec

PROMPT_VERSION = "summarize-history-v1"


@dataclass
class HistorySummaryContext:
    existing_summary: str = ""
    old_messages: list = None   # [{role, content}]


def build_prompt(ctx: HistorySummaryContext) -> PromptSpec:
    old = (ctx.existing_summary or "").strip() or "（无）"
    lines = []
    for m in (ctx.old_messages or []):
        role = "护士" if m.get("role") in ("nurse", "assistant") else "患者"
        lines.append(f"{role}：{m.get('content', '')}")
    dialogue = "\n".join(lines) if lines else "（无）"
    system = """你是随访对话摘要助手。请把一段更早的医患微信对话与已有摘要合并，
压成一段完整但简洁的『前情提要』，供护士在后续长对话中不丢失早期关键信息。

要求：
1. 保留关键信息：疼痛程度/趋势、睡眠、用药、副作用、风险信号、患者情绪、重要事件（复诊/住院/请假等）。
2. 必须融合已有摘要，不能遗漏其中的关键点；保留已经确认的NRS数值、睡眠、用药和副作用结论，不要把患者对某个问题的回答改写成总体状态。
3. 输出一段连续中文文本，400字以内，不要分条列点，不要出现『摘要』二字。"""
    user = f"""已有摘要：{old}

本批新对话：
{dialogue}

请输出合并后的摘要："""
    return PromptSpec(system=system, user=user, temperature=0.3,
                      max_tokens=500, prompt_version=PROMPT_VERSION)
