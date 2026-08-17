# backend/agents/capability_agents/__init__.py
"""capability_agents/ —— 单一受限 LLM 能力封装（Agent-centric迁移方案 §2）。

工作流 Agent 负责 LangGraph 编排，capability_agents/ 只负责一次受限的 LLM 调用：
每个 Agent 对应唯一 Prompt，图节点调用 Agent，Agent 经 LLMGateway 调模型。
"""
