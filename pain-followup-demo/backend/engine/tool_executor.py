"""
工具执行器 — 接收 LLM tool_calls 并分发到对应 handler。

与旧 ToolDispatcher 的区别：
  - 不再需要预先注册 TOOL_REGISTRY 元数据字典；
  - handler 来自 tool_definitions.HANDLER_MAP；
  - 执行结果直接返回为 ToolMessage 格式，供 LLM 继续推理。
"""

from __future__ import annotations

import json

from engine.tool_definitions import HANDLER_MAP


async def execute_tool(tool_name: str, tool_args: dict) -> str:
    """执行单个工具调用，返回 JSON 字符串（ToolMessage content 格式）。

    Args:
        tool_name: 工具名（如 parse_patient_reply）
        tool_args: 工具参数 dict

    Returns:
        JSON 字符串化的工具执行结果
    """
    handler = HANDLER_MAP.get(tool_name)
    if handler is None:
        return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)
    try:
        result = await handler(**tool_args)
    except Exception as exc:
        result = {"error": f"工具 {tool_name} 执行失败: {exc}"}
    return json.dumps(result, ensure_ascii=False, default=str)
