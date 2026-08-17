# backend/agents/capability_agents/history_summary_agent.py
"""HistorySummaryAgent —— 滚动摘要（说明书 8.5 表）。

仅在会话超过上下文窗口时调用，把被截断的早期消息压成累计摘要。
"""
from __future__ import annotations

from prompts.conversation.summarize_history_prompt import build_prompt, HistorySummaryContext


class HistorySummaryAgent:
    def __init__(self, llm_gateway=None):
        self._gw = llm_gateway

    async def summarize(self, existing_summary: str, old_messages: list) -> str | None:
        from infrastructure.llm.llm_gateway import LLMGateway
        gw = self._gw or LLMGateway()
        if not gw.is_llm_available():
            return None
        spec = build_prompt(HistorySummaryContext(
            existing_summary=existing_summary, old_messages=old_messages,
        ))
        try:
            text = await gw.chat(
                [{"role": "system", "content": spec.system},
                 {"role": "user", "content": spec.user}],
                profile="summarize_history",
            )
            return (text or "").strip() or None
        except Exception:
            return None
