# backend/agents/states.py
"""Agent-centric 统一 State Schema（Agent-centric迁移方案 §7）。

所有工作流 Agent 共享同一份状态定义；每个 State 只保存 JSON 可序列化业务数据，
运行时依赖通过 get_context()/构造函数注入（§9），不进入 State。
状态字段在 Agent 之间按需取子集，不存在万能 AgentState（P0#6）。

Reducer 约定（§7.4）：
  - reports_by_episode 按 episode_id 去重，终态优先（重试结果不重复）；
  - messages 按 message_key 去重，避免 Graph 重试重复发消息/重复入队。
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict


def merge_lists(existing: list, new: list) -> list:
    """通用列表累加（无去重语义的场景使用）。"""
    return (existing or []) + (new or [])


def merge_messages(existing: list, new: list) -> list:
    """消息列表 Reducer：按 message_key 去重（§7.4）。

    同一 message_key 视为同一条消息的重试，覆盖旧值而非重复追加，
    保证 Graph 重试不会向数据库/前端重复写入同一条消息。
    """
    out = list(existing or [])
    seen: dict[str, int] = {}
    for i, m in enumerate(out):
        k = m.get("message_key")
        if k:
            seen[k] = i
    for m in new or []:
        key = m.get("message_key")
        if key and key in seen:
            out[seen[key]] = m
        else:
            if key:
                seen[key] = len(out)
            out.append(m)
    return out


_TERMINAL_STATUS = {"completed", "human_handoff", "incomplete_handoff", "failed"}


def _is_terminal(r: dict) -> bool:
    return (r.get("status") or "") in _TERMINAL_STATUS


def merge_report_by_episode(existing: dict, new: dict | list) -> dict:
    """Reducer：以 episode_id 去重，终态优先（P0#7 修复）。

    Send 子任务返回的是 `{"reports": [report, ...]}` 列表，但 state 字段声明为 dict；
    兼容两种入参形态——若 new 是 dict 则取其 values()，不能把字典按键遍历。

    终态（completed / handoff / failed）不被晚到的非终态（waiting_reply 等）覆盖。
    """
    out: dict = dict(existing or {})
    entries: list[dict]
    if isinstance(new, dict):
        entries = [v for v in new.values() if isinstance(v, dict)]
    else:
        entries = [r for r in (new or []) if isinstance(r, dict)]
    for r in entries:
        eid = r.get("episode_id")
        if eid is None:
            continue
        prev = out.get(eid)
        if prev is not None and _is_terminal(prev) and not _is_terminal(r):
            continue  # 终态结果不被晚到的非终态覆盖
        out[eid] = r
    return out


class DispatchState(TypedDict, total=False):
    """总调度工作流状态（§7.1）。"""
    dispatch_id: str
    scope: str                      # 调度范围（ward-A 等，dispatch_id 组成要素）
    episode_ids: list[str]          # §5：create_episodes 生成的 Episode 投影 ID
    evaluation_time: str
    timezone: str
    business_date: str
    callback_policy_version: str | None      # 电话回访策略版本（独立于对话策略）
    conversation_policy_version: str | None  # 对话策略版本（§9.1.8：两版本分开固化）
    callback_policy_snapshot: dict | None
    patient_snapshot_version: str
    patients: list[dict]                    # load_inputs 读取的原始患者列表
    send_roster: list[dict]                 # 应访名单（带 episode_id / input_source）
    skip_roster: list[dict]
    details: dict                           # {patient_id: RosterDecision}
    phone_callback_count: int
    # 每位患者的最新终态报告，按 episode_id 合并（去重 + 终态优先）
    reports: Annotated[dict, merge_report_by_episode]
    dispatch_status: str
    dispatch_report: dict
    max_concurrency: int


class PatientState(TypedDict, total=False):
    """单患者随访工作流状态（§7.2）。"""
    episode_id: str
    dispatch_id: str
    patient_id: str
    patient_snapshot: dict                  # 不可变快照（可序列化）
    plan_snapshot: dict
    input_source: Literal["human", "simulator"]
    channel: str
    business_date: str                       # 冻结业务日期（调度入口解析一次，整次复用，§5.2）
    evaluation_time: str                     # 本次 episode 的业务日期（报告 started_at 用）
    scenario_id: str | None
    scenario_seed: str | None               # 模拟回复可复现种子（episode_id + seed）
    callback_policy_version: str | None      # 电话回访策略版本（随 episode 固化，§9.1.8）
    conversation_policy_version: str | None  # 对话策略版本
    messages: Annotated[list[dict], merge_messages]   # {role, content, turn_no, message_key}
    turn_no: int
    slots: dict                             # {pain_nrs, sleep_quality, medication_taken, side_effects}
    coverage: dict                          # {complete, collected, missing}
    risk_result: dict
    reply_understanding: dict
    turn_decision: dict                     # TurnRouter 输出
    status: str                             # episode 状态（waiting_human / completed / ...）
    session_id: str | None
    agent_summary: str                      # persist 阶段摘要（ReviewAgent 输入）
    review: dict                            # ReviewDraft（ReviewAgent 输出）
    review_id: str | None
    report: dict                            # PatientReport
    errors: list[dict]
    scenario: dict | None                   # 模拟场景（仅 simulator）
    simulator_target_slot: str | None       # 本轮患者模拟器回答的槽位
    simulator_reply_meta: dict              # 模拟回复来源/校验元数据
    early_summary: str                      # 滚动摘要
    history_summary_message_count: int       # 已纳入滚动摘要的消息数量


class ConversationState(TypedDict, total=False):
    """单轮对话工作流状态（§7.3 ConversationAgent）。

    PatientFollowupAgent 以子图方式调用 ConversationAgent，字段为其状态子集。
    """
    episode_id: str
    dispatch_id: str                       # 消息入队 / 风险事件需要
    patient_id: str
    patient_snapshot: dict
    input_source: Literal["human", "simulator"]
    channel: str
    messages: Annotated[list[dict], merge_messages]
    turn_no: int
    slots: dict
    coverage: dict
    reply_understanding: dict
    risk_result: dict
    turn_decision: dict
    early_summary: str
    history_summary_message_count: int


class ReviewState(TypedDict, total=False):
    """AI 审阅工作流状态（§7.4 ReviewAgent）。

    补 episode_id / dispatch_id / patient_id（P0#3）。
    只生成 ReviewDraft；医生最终确认由 DoctorReviewService 处理。
    """
    dispatch_id: str
    episode_id: str
    patient_id: str
    session_id: str | None
    risk_result: dict
    agent_summary: str
    transcripts: list[dict]
    plan: dict
    history: dict
    draft_review: dict
    review_status: str                       # review_pending → ai_review_ready


class SimState(TypedDict, total=False):
    """患者模拟工作流状态（P1#1/#2）。

    场景驱动而非轮次驱动；用 episode_id + scenario_seed 保证重试/恢复结果可复现。
    只生成患者回复，不计算风险、不决定结束、不写库、不发告警。
    """
    episode_id: str
    patient_id: str
    scenario_seed: str | None
    snapshot: dict
    scenario: dict | None
    transcript: list[dict]
    round_num: int
    last_question: str
    target_slot: str | None                 # 当前应回答的槽位
    known_slots: dict                       # 已由随访流程确认的槽位
    reply: str
    valid: bool
    mentioned_slots: list[str]
    reply_metadata: dict


class PlanState(TypedDict, total=False):
    """随访计划工作流状态（§4.6 PlannerAgent）。

    extract_discharge_context → retrieve_guidelines(RAG) → draft_plan(PlanGenerationAgent)
    → hitl_review(interrupt) → persist_plan(PlanRepository)。
    """
    patient_id: str
    patient_brief: str
    discharge_summary: str
    retrieved_evidence: str
    plan_json: dict
    citations: list[dict]
    plan_id: str
    review_decision: str                     # approved | modified | rejected
    doctor_plan_json: dict | None            # 医生修改后的计划
    doctor_id: str | None
