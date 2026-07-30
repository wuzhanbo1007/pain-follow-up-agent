"""
随访流程多 Agent 编排包（A / B / C / D + Orchestrator）

各 Agent 职责：
  - planner(Graph)    A 号：随访计划生成工作流（LLM + RAG 检索 + HITL 医生审阅）
  - daily_scheduler   B 号：当日随访判定（规则引擎优先，频次解析不确定时 LLM 兜底）
  - execution(Graph)  C 号：随访执行 Agent（ReACT 自主循环：反思决策 → 追问 → 解析 → 评分）
  - summarizer        D 号：随访内容总结（LLM 结构化摘要/风险标记/完成度/建议）

共享内核：
  - state             多 Agent 共享的 LangGraph 状态定义（TypedDict 超集）。
  - react_core        ReACT 处理引擎（位于 engine/，供两条执行路径共用）。

所有 LLM 能力均保持"无 LLM 时规则/模板兜底"（与既有代码风格一致）。
"""
from .planner import planner_graph, build_planner_graph
from .execution import execution_graph, run_session
from services.daily_scheduler import decide_today, decide_today_list
from .summarizer import summarize_session
from .orchestrator import run_followup_pipeline
from services.doctor_review import (
    build_review_graph, submit_review, list_review_queue, get_review_statistics,
)

__all__ = [
    "planner_graph", "build_planner_graph",
    "execution_graph", "run_session",
    "build_review_graph", "submit_review", "list_review_queue",
    "get_review_statistics",
    "decide_today", "decide_today_list",
    "summarize_session",
    "run_followup_pipeline",
]
