"""
共享引擎模块

  - RiskEngine           风险评分引擎
  - AutoReplyEngine      自动回复模拟引擎
  - followup_scheduler   随访计划驱动的当日随访名单判定引擎
  - tool_definitions     Function-Calling 工具 schema + handler
  - tool_executor        工具执行器（接收 LLM tool_calls）
"""
from .risk_engine import RiskEngine
from .auto_reply import AutoReplyEngine
from .followup_scheduler import (
    get_today,
    parse_frequency_to_rule,
    is_followup_due,
    build_today_send_list,
    apply_today_followup_flags,
    compute_next_followup,
)
from .tool_definitions import TOOL_SCHEMAS, HANDLER_MAP, register_handlers

__all__ = [
    "RiskEngine",
    "AutoReplyEngine",
    "get_today",
    "parse_frequency_to_rule",
    "is_followup_due",
    "build_today_send_list",
    "apply_today_followup_flags",
    "compute_next_followup",
    "TOOL_SCHEMAS",
    "HANDLER_MAP",
    "register_handlers",
]
