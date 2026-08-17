# backend/prompts/prompt_spec.py
"""PromptSpec —— 统一提示词输出契约（说明书 8.5 调用与引用规范 2）。

每个提示词文件只导出 build_prompt(context) -> PromptSpec；
PromptSpec 含 system / user / temperature / max_tokens / prompt_version。
图节点调 Agent，Agent 通过 LLMGateway 调模型，提示词模块只负责构造文本，三者不互相越权。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptSpec:
    system: str
    user: str
    temperature: float = 0.5
    max_tokens: int | None = None
    prompt_version: str = ""
    response_format: dict | None = None

    def render(self, variables: dict | None = None) -> list[dict]:
        """§12.1：把 PromptSpec 渲染为 OpenAI messages 列表（system/user 消息）。

        现有 build_prompt 均以 f-string 完成变量替换，render 只做 message 组装；
        若调用方传入 variables（针对含 ``{占位符}`` 的提示词），补做一次 format，
        format 失败（已渲染文本含未配对花括号等）时原样返回，不破坏提示词。
        """
        def _fill(text: str) -> str:
            if not text or not variables:
                return text
            try:
                return text.format(**variables)
            except (KeyError, IndexError, ValueError):
                return text

        messages: list[dict] = []
        if self.system:
            messages.append({"role": "system", "content": _fill(self.system)})
        if self.user:
            messages.append({"role": "user", "content": _fill(self.user)})
        return messages
