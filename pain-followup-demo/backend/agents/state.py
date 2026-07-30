"""
多 Agent 共享状态定义

各 Agent 内部用 LangGraph StateGraph 驱动，字段采用同一个 TypedDict 超集，
不同 Agent 使用其中子集。Annotated 字段支持多节点累加（消息日志）。
"""
from typing import Annotated, List, Optional, TypedDict
from langgraph.graph import add_messages


def merge_lists(existing: List, new: List) -> List:
    return (existing or []) + (new or [])


class AgentState(TypedDict, total=False):
    # ---- 患者上下文 ----
    patient_id: str
    patient: dict                       # 患者完整记录（来自 DB）
    diagnosis: str
    discharge_summary: str

    # ---- B 号 RAG 检索结果 ----
    query: str                          # 检索问句
    retrieved_evidence: Annotated[List[dict], merge_lists]  # CitedChunk 序列化

    # ---- A 号 计划 ----
    plan_json: dict
    plan_id: str
    citations: List[dict]
    review_decision: str              # approved | modified | rejected
    doctor_plan_json: dict           # 医生修改后的计划
    doctor_id: str

    # ---- C 号 执行会话 ----
    session_id: str
    transcript: List[dict]            # [{role, content}]
    risk_result: dict
    agent_summary: str
    plan_driven: bool                  # 是否由已批准计划驱动

    # ---- C 号 ReACT 运行态 ----
    # 注：这些字段必须声明为通道，否则 react_reflect 的返回值会被丢弃，
    # _should_continue 永远读不到 react_should_end，导致 ReACT 循环无法终止（无限递归）。
    react_round: int
    react_info_inventory: dict
    react_should_end: bool
    react_decision: dict
    _pending_nurse_msg: str             # 下轮护士追问文本

    # ---- D 号 审阅 ----
    review_id: str
    doctor_score: int
    doctor_comment: str
    track_status: str

    # ---- 通用 ----
    messages: Annotated[List, add_messages]
    error: str
    status: str
