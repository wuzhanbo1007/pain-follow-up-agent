# backend/agents/dispatcher_agent.py
"""DispatcherAgent —— 每日总调度工作流（Agent-centric迁移方案 §4.1 / §5 / §6.1）。

  load_inputs → decide_base_roster → apply_callback_policy → create_episodes → END

§5（独立 Episode）：Dispatcher 只创建 Dispatch + Episode Projection，再发布
episode.start_requested 事件；它不等待每个 Episode 完成。每个 Episode 由
EpisodeService 用独立 thread_id 分别执行（人工 interrupt 只挂起自身，不阻塞
总调度、不阻塞其他 Episode）。总调度状态由 Episode 投影汇总（refresh_dispatch_status）。

输入源是生产数据库（PatientRepository），不是 MANUAL_ASSIGNMENTS 或姓名常量（§5.1）。
节点统一经 Runtime[AppContext] 获取依赖（§7），不再调用全局 get_context()。
"""
from __future__ import annotations

import hashlib
import inspect
import uuid
from datetime import date

from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime

from agents.states import DispatchState
from infrastructure.langgraph import get_checkpointer, thread_config
from infrastructure.runtime_context import AppContext, get_context
from domain.models.followup import DispatchRoster
from domain.models.callback_policy import CallbackPolicy
from domain.services.roster_decider import RosterDecider
from core.logging_config import get_logger

_logger = get_logger("painsmart.dispatcher")


def _snapshot_version(patients: list[dict]) -> str:
    h = hashlib.sha256()
    for p in patients:
        h.update(str(p.get("patient_id")).encode("utf-8"))
    return h.hexdigest()[:8]


def _as_patient_key(pid) -> int | str:
    """患者 ID 统一为 int 键（配置 manual_patient_ids 为 int）；非数字回退字符串比较。"""
    try:
        return int(pid)
    except (TypeError, ValueError):
        return str(pid)


def assign_input_source(send_roster: list[dict],
                        manual_patient_ids: list[int]) -> tuple[list[dict], list[int]]:
    """§12.2：当天应访名单 ∩ manual_patient_ids → 手动演示；其余 → 自动演示。

    返回 (assignments, ignored_manual_ids)。配置中的 ID 不在当天应访名单中 →
    忽略（由调用方记录日志），不强制纳入当天名单。
    input_source 只作为本次 Episode 的运行时信息，不作为患者主数据字段。
    """
    manual_set = {int(m) for m in manual_patient_ids}
    assignments: list[dict] = []
    send_ids: set = set()
    for patient in send_roster:
        pid = _as_patient_key(patient.get("patient_id"))
        send_ids.add(pid)
        item = dict(patient)
        item["input_source"] = "human" if pid in manual_set else "simulator"
        assignments.append(item)
    ignored_manual_ids = sorted(int(m) for m in manual_set if m not in send_ids)
    return assignments, ignored_manual_ids


# ---- 节点 ----

def load_inputs_node(state: DispatchState, runtime: Runtime[AppContext]) -> dict:
    """通过 PatientRepository 批量读取真实患者/计划/状态与业务日期。读取失败终止本次调度。

    日期冻结（§5.2）：业务日期由调度入口（run_dispatch）解析一次写入 state.business_date，
    此处优先复用，仅在图被直接调用（无冻结值）时兜底解析。
    """
    ctx = runtime.context
    if state.get("business_date"):
        bd = date.fromisoformat(state["business_date"])
    else:
        bd = ctx.clock.get_business_date()
    patients = ctx.patient_repository.get_all_patients()
    snap_version = _snapshot_version(patients)
    ctx.event_outbox.dispatch_started(
        dispatch_id=state.get("dispatch_id", ""),
        business_date=bd.isoformat(),
        timezone="Asia/Shanghai",
        patient_snapshot_version=snap_version,
    )
    ctx.event_outbox.dispatch_state_changed(
        dispatch_id=state.get("dispatch_id", ""), status="loading_inputs",
    )
    return {
        "business_date": bd.isoformat(),
        "timezone": "Asia/Shanghai",
        "conversation_policy_version": getattr(
            ctx.conversation_policy, "version", "conv-v1"),
        "patient_snapshot_version": snap_version,
        "patients": patients,
        "dispatch_status": "loading_inputs",
    }


def decide_base_roster_node(state: DispatchState, runtime: Runtime[AppContext]) -> dict:
    """依据数据库计划到期/状态/豁免计算应访/免访基础名单（§5.1），再按配置分流。

    §6 / §11-11 / §12.2：名单判定与输入来源分配是两个步骤——
      RosterDecider 只决定 send_roster / skip_roster；
      DispatcherAgent 再按外部配置 manual_patient_ids ∩ send_roster 分配
      input_source（human / simulator），不读取患者表中的自动/手动模式字段。
    """
    ctx = runtime.context
    patients = state.get("patients") or []
    decider = RosterDecider()
    bd = date.fromisoformat(state["business_date"]) if state.get("business_date") else None
    roster_kwargs = {"business_date": bd}
    # 兼容旧版/测试替身的两参数 RosterDecider，同时保留正式实现的
    # 电话回访阈值配置；避免一个可选参数让整批并行 Episode 无法启动。
    if "no_reply_threshold" in inspect.signature(
        decider.decide_base_roster
    ).parameters:
        roster_kwargs["no_reply_threshold"] = getattr(
            ctx.settings, "phone_callback_threshold_days", 3
        )
    roster = decider.decide_base_roster(patients, **roster_kwargs)
    # 手动 ID 来自外部结构化配置（config/followup_runtime.yaml），不来自患者表字段
    manual_ids = list(getattr(ctx.settings, "manual_patient_ids", []) or [])
    assignments, ignored_ids = assign_input_source(roster.send_list, manual_ids)
    for pid in ignored_ids:
        _logger.info(
            "manual_patient_not_in_roster",
            extra={"patient_id": pid, "business_date": state.get("business_date", "")},
        )
    ctx.event_outbox.roster_ready(
        dispatch_id=state.get("dispatch_id", ""),
        send_count=len(assignments), skip_count=len(roster.skip_list),
        phone_callback_count=roster.phone_callback_count, send_roster=assignments,
        skip_roster=roster.skip_list, callback_policy_version=None,
        details=roster.details,
    )
    ctx.event_outbox.dispatch_state_changed(
        dispatch_id=state.get("dispatch_id", ""), status="calculating_base_roster",
        counts={"send_count": len(assignments), "skip_count": len(roster.skip_list)},
    )
    return {
        "send_roster": assignments, "skip_roster": roster.skip_list,
        "details": roster.details, "dispatch_status": "calculating_base_roster",
    }


def apply_callback_policy_node(state: DispatchState, runtime: Runtime[AppContext]) -> dict:
    """在应访名单上叠加电话回访标记（不得把免访患者重新纳入）。"""
    ctx = runtime.context
    policy_version = state.get("callback_policy_version")
    policy: CallbackPolicy | None = None
    if policy_version:
        snap = ctx.policy_repository.get_snapshot(policy_version)
        if snap is not None:
            from domain.models.callback_policy import CallbackPolicy as CP
            policy = CP(**snap.compiled_json)
    decider = RosterDecider()
    roster = DispatchRoster(
        send_list=state.get("send_roster") or [],
        skip_list=state.get("skip_roster") or [],
        details=state.get("details") or {},
        phone_callback_count=sum(
            1 for patient in (state.get("send_roster") or [])
            if patient.get("phone_callback")),
    )
    roster = decider.apply_callback_policy(roster, policy)
    ctx.event_outbox.dispatch_state_changed(
        dispatch_id=state.get("dispatch_id", ""), status="applying_callback_policy",
        counts={"phone_callback_count": roster.phone_callback_count},
    )
    return {"send_roster": roster.send_list, "details": roster.details,
            "phone_callback_count": roster.phone_callback_count,
            "dispatch_status": "applying_callback_policy"}


async def create_episodes_node(state: DispatchState,
                               runtime: Runtime[AppContext]) -> dict:
    """为应访患者创建 Dispatch + Episode Projection，发布启动事件（§5）。

    Dispatcher 只创建投影、发布 episode.start_requested；不等待每个 Episode 完成。
    Episode 由 EpisodeService 用独立 thread_id 分别执行（interrupt 不阻塞总调度）。
    """
    ctx = runtime.context
    dispatch_id = state.get("dispatch_id", "")
    send_roster = state.get("send_roster") or []
    business_date = state.get("business_date", "")
    # 总调度投影（幂等创建；真实计数由 Episode 投影汇总后回填）
    await ctx.dispatch_repository.create(
        dispatch_id, status="dispatching",
        payload={
            "scope": state.get("scope", "ward-A"),
            "business_date": business_date,
            "callback_policy_version": state.get("callback_policy_version"),
            "conversation_policy_version": state.get("conversation_policy_version"),
            "send_count": len(send_roster),
            # 写入调度投影，供微信独立窗口重连后恢复完整会话名单。
            "send_roster": send_roster,
            "skip_roster": state.get("skip_roster") or [],
            "roster_details": state.get("details") or {},
        },
    )
    episode_ids: list[str] = []
    for p in send_roster:
        pid = p.get("patient_id")
        episode_id = f"episode:{dispatch_id}:{pid}"
        # 连续未回复达到 YAML 配置阈值的患者也要记录“本轮已触达”。
        # 但她不会进入微信/模拟对话 Episode，终态直接转人工电话回访。
        if p.get("phone_callback"):
            no_reply_days = int(p.get("consecutive_no_reply_days") or 0)
            reason = f"连续{no_reply_days}天未回复本次随访消息，需电话回访"
            patient_name = p.get("name") or f"患者{pid}"
            await ctx.episode_repository.create(
                episode_id=episode_id, dispatch_id=dispatch_id,
                patient_id=str(pid), status="pending",
                payload={
                    "episode_id": episode_id,
                    "dispatch_id": dispatch_id,
                    "patient_id": str(pid),
                    "input_source": p.get("input_source", "simulator"),
                    "channel": p.get("channel", "phone"),
                    "business_date": business_date,
                    "callback_policy_version": state.get("callback_policy_version"),
                    "conversation_policy_version": state.get("conversation_policy_version"),
                    "phone_callback": True,
                },
            )
            from agents.patient_followup_agent import run_no_reply_episode
            await run_no_reply_episode(
                episode_id=episode_id, dispatch_id=dispatch_id,
                patient_id=str(pid), patient_name=patient_name,
                message_content=(
                    f"{patient_name}您好，系统已发送本次疼痛随访提醒。"
                    f"因您已连续{no_reply_days}天未回复，已为您安排人工电话回访，"
                    "请保持电话畅通。"
                ),
                no_reply_days=no_reply_days, business_date=business_date,
                callback_policy_version=state.get("callback_policy_version"),
                conversation_policy_version=state.get("conversation_policy_version"),
                input_source=p.get("input_source", "simulator"),
                channel=p.get("channel", "phone"), context=ctx,
            )
            ctx.event_outbox.turn_decision(
                dispatch_id=dispatch_id, episode_id=episode_id,
                patient_id=str(pid), patient_name=patient_name,
                turn_no=1, input_source=p.get("input_source", "simulator"),
                decision={
                    "action": "incomplete_handoff",
                    "reason": reason,
                    "missing_slots": [],
                },
                coverage={"missing": []},
            )
            ctx.event_outbox.callback_alert(
                dispatch_id=dispatch_id, episode_id=episode_id,
                patient_id=str(pid), patient_name=patient_name,
                no_reply_days=no_reply_days, reason=reason,
                alert_key=f"callback-alert:{episode_id}",
            )
            continue
        # 幂等创建 Episode 投影（payload 与旧 Send 负载一致，EpisodeService.start 直接运行）
        await ctx.episode_repository.create(
            episode_id=episode_id, dispatch_id=dispatch_id,
            patient_id=str(pid), status="pending",
            payload={
                "episode_id": episode_id,
                "dispatch_id": dispatch_id,
                "patient_id": pid,
                "input_source": p.get("input_source", "simulator"),
                "channel": p.get("channel", "wechat"),
                "scenario_seed": p.get("scenario_seed"),
                "business_date": business_date,
                "callback_policy_version": state.get("callback_policy_version"),
                "conversation_policy_version": state.get("conversation_policy_version"),
            },
        )
        episode_ids.append(episode_id)
        # §5：发布启动事件（幂等 event_key）；消费端 DispatchService drain 后逐条启动
        ctx.event_outbox.enqueue(
            event_type="episode.start_requested", aggregate_id=episode_id,
            payload={"episode_id": episode_id, "dispatch_id": dispatch_id},
        )
    ctx.event_outbox.dispatch_state_changed(
        dispatch_id=dispatch_id, status="dispatching",
        counts={"send_count": len(send_roster)},
    )
    return {"episode_ids": episode_ids, "dispatch_status": "dispatching"}


# ---- 图 ----

def _dispatch_id(business_date: str, scope: str, snapshot_version: str,
                 policy_version: str | None, run_token: str | None = None) -> str:
    # 同一天允许重新演示/重跑；每次启动必须有独立批次，避免审阅页复用上一轮会话。
    raw = (
        f"dispatch:{business_date}:{scope}:{snapshot_version}:"
        f"{policy_version or 'none'}:{run_token or uuid.uuid4().hex}"
    )
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"dispatch:{business_date}:{scope}:{h}"


def build_dispatcher_graph():
    g = StateGraph(DispatchState, context_schema=AppContext)
    g.add_node("load_inputs", load_inputs_node)
    g.add_node("decide_base_roster", decide_base_roster_node)
    g.add_node("apply_callback_policy", apply_callback_policy_node)
    g.add_node("create_episodes", create_episodes_node)

    g.add_edge(START, "load_inputs")
    g.add_edge("load_inputs", "decide_base_roster")
    g.add_edge("decide_base_roster", "apply_callback_policy")
    g.add_edge("apply_callback_policy", "create_episodes")
    g.add_edge("create_episodes", END)
    return g.compile(checkpointer=get_checkpointer())


dispatcher_workflow = build_dispatcher_graph()


async def run_dispatch(*, scope: str = "ward-A",
                       callback_policy_version: str | None = None,
                       context=None) -> dict:
    """启动每日调度（§13 POST /api/followups/dispatches）。

    只跑 Dispatcher 图：创建 Dispatch + Episode 投影并发布启动事件，快速返回
    {dispatch_id, episode_ids, ...}；Episode 实际执行由 DispatchService 消费
    episode.start_requested 事件后逐条启动（§5）。
    context：显式注入 AppContext（§7）；缺省取全局单例。
    """
    if context is None:
        context = get_context()
    ctx = context
    bd = ctx.clock.get_business_date()
    # 先用临时 snapshot_version 算 dispatch_id（load_inputs 内会回填真实版本）
    patients = ctx.patient_repository.get_all_patients()
    sv = hashlib.sha256(
        "".join(str(p.get("patient_id")) for p in patients).encode("utf-8")
    ).hexdigest()[:8]
    dispatch_id = _dispatch_id(
        bd.isoformat(), scope, sv, callback_policy_version,
        run_token=uuid.uuid4().hex[:12],
    )
    return await dispatcher_workflow.ainvoke(
        {"dispatch_id": dispatch_id, "scope": scope,
         "callback_policy_version": callback_policy_version,
         "business_date": bd.isoformat(),  # §5.2：调度入口解析一次，图内各节点复用冻结值
         "max_concurrency": 8},
        config=thread_config(dispatch_id),
        context=context,
    )
