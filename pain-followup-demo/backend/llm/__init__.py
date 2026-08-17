# backend/llm/__init__.py
"""LLM 交互模块 —— 仅保留旧 client / model（被 LLMGateway 与 data/ 复用）。

说明：MessageGenerator 与 ReplyParser 已随架构重构退役删除
（说明书 14.2：message_generator + 三类消息 Prompt 删除后拆分；parser.py +
reply_parsing.py 重构为 ReplyUnderstandingAgent）。
"""
