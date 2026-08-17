# backend/domain/__init__.py
"""领域层（domain/）—— 纯业务契约与确定性服务。

被所有上层（api/graphs/nodes/agents）读取，但不反向依赖上层。
包含：
  - models/   Pydantic 输入/输出契约（无副作用、可序列化）
  - policies/ 可版本化的策略对象（CallbackPolicy / RiskPolicy / ScheduleSpec）
  - services/ 确定性决策器（RosterDecider / TurnRouter / CoverageEvaluator / RiskEvaluator）
  - prompt_registry.py  PromptKey → 唯一 Agent / 文件，用于启动校验与测试

设计原则（说明书 3.2 / 9 / 12）：
  - State 只保存 JSON 可序列化业务数据；运行时依赖通过 Context 注入。
  - 临床确定性：风险、告警、完成条件由确定性节点控制，LLM 不决定。
"""
