# backend/agents/__init__.py
"""Agent 层（agents/）—— 两层结构（Agent-centric迁移方案 §2）。

  - 工作流 Agent（agents/*.py）：State + LangGraph 节点 + 条件路由 + 图 + run/resume 入口。
    承担编排与确定性决策（风险/路由/落库），是唯一能接触 State 与 Graph 的层。
  - capability_agents/：单一受限 LLM 能力封装。唯一输入 Schema、唯一提示词文件、
    唯一输出 Schema，经 LLMGateway 调模型，提示词模块只构造文本。

工作流 Agent 调用 capability Agent；任何 Agent 不反向导入编排层（说明书 8.5 / 15.2）。
公共 State/Reducer 统一在 states.py（§7）。
"""
