# backend/services/dispatch_service.py
"""DispatchService —— 总调度门面（说明书 14.1 followup_service 大幅瘦身）。

只提供 start_dispatch / resume_episode / query_projection 门面与 Context 装配。

§5（独立 Episode）：start_dispatch 跑 Dispatcher 图（只创建 Dispatch + Episode
投影、发布 episode.start_requested），消费事件后逐条启动独立 Episode
（EpisodeService，各自 thread_id）；Episode 落定后由投影汇总调度状态。
Graph 是唯一编排权威；Service 不再另起协程推动同一流程，只做编排与装配。
"""
from __future__ import annotations

import asyncio
from typing import Any

from core.logging_config import get_logger
from infrastructure.runtime_context import AppContext, get_context, set_context
from infrastructure.messaging.outbox import MessageOutbox, EventOutbox

log = get_logger("painsmart.dispatch_service")


class DispatchService:
    """总调度门面：启动调度 / 恢复 episode / 查询投影（真实投影，非占位文案）。"""

    def __init__(self, context: AppContext | None = None, event_bus=None):
        self.context = context or get_context()
        # 注入旧 EventBus（过渡期，供 Outbox 透传给前端）
        if event_bus is not None:
            self.context.event_bus = event_bus
            self.context.message_outbox = MessageOutbox(event_bus)
            self.context.event_outbox = EventOutbox(event_bus)
            set_context(self.context)

    def _dispatch_lock(self) -> set[str]:
        """§P1#27：运行锁放到 AppContext 共享，避免每实例新建导致并发去重无效。"""
        lock = getattr(self.context, "running_dispatches", None)
        if lock is None:
            lock = set()
            self.context.running_dispatches = lock
        return lock

    # ===== 启动每日调度 =====
    async def start_dispatch(self, *, scope: str = "ward-A",
                             callback_policy_version: str | None = None) -> dict:
        """启动每日调度（说明书 13 POST /api/followups/dispatches）。

        §5：跑 Dispatcher 图（创建投影 + 发布 episode.start_requested）→ 消费事件
        逐条启动独立 Episode（各自 thread_id，interrupt 不阻塞其他）→ 投影汇总终态。
        返回投影报告（真实计数）。
        """
        from agents.dispatcher_agent import run_dispatch
        from services.episode_service import EpisodeService
        result = await run_dispatch(scope=scope,
                                    callback_policy_version=callback_policy_version,
                                    context=self.context)
        dispatch_id = result.get("dispatch_id") or ""
        # 消费启动事件（事件驱动路径，§5）；兜底用图返回的 episode_ids
        events = self.context.event_outbox.drain(event_type="episode.start_requested")
        episode_ids = [e["aggregate_id"] for e in events] or result.get("episode_ids") or []
        # 所有 Episode（含电话回访）共用同一并发池，防止 LLM 请求把服务端挤满。
        svc = EpisodeService(self.context)
        max_concurrency = max(1, int(result.get("max_concurrency") or 4))
        semaphore = asyncio.Semaphore(max_concurrency)

        async def start_limited(eid: str):
            async with semaphore:
                return await svc.start(eid)

        tasks = [asyncio.create_task(start_limited(eid)) for eid in episode_ids]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await svc.refresh_dispatch_status(dispatch_id)
        report = await self.query_projection(dispatch_id)
        # 只有所有普通/电话回访 Episode 都进入终态，才发布最终统计。
        # 人工患者处于 waiting_human 时只发布进行中状态，不能提前结束批次。
        self._publish_final_report_if_complete(dispatch_id, report)
        return report

    def start_dispatch_background(self, *, scope: str = "ward-A",
                                   callback_policy_version: str | None = None) -> dict:
        """非阻塞启动（事件循环上创建后台 async 任务，不阻塞调用方）。

        与旧 Comet 并发模型一致：asyncio.create_task + await，不阻塞事件循环。
        """
        lock = self._dispatch_lock()
        if scope in lock:
            return {"status": "running", "scope": scope}
        lock.add(scope)

        async def _run():
            try:
                await self.start_dispatch(scope=scope,
                                          callback_policy_version=callback_policy_version)
            except Exception as exc:
                log.exception("总调度执行失败")
                eb = self.context.event_bus
                if eb is not None:
                    eb.log(f"总调度执行失败：{exc}", level="error")
            finally:
                lock.discard(scope)

        asyncio.create_task(_run())
        return {"status": "started", "scope": scope}

    # ===== 恢复手动患者 episode =====
    async def resume_episode(self, episode_id: str, reply_text: str) -> dict:
        """提交手动患者回复，恢复同一 episode（说明书 13 / §12：REST 与 WebSocket 共用入口）。

        §5 / §P1#12 / §P1#30：恢复经 EpisodeService（同一 thread_id，P0#8 前置校验）；
        发布 resume_started / resume_succeeded / resume_failed 事件，异常写入日志并抛出。
        """
        from services.episode_service import EpisodeService
        eb = self.context.event_bus
        if eb is not None:
            eb.publish("episode:resume_started", {"episode_id": episode_id})
        try:
            result = await EpisodeService(self.context).resume(episode_id, reply_text)
        except Exception as exc:
            log.warning("episode 恢复失败 %s: %s", episode_id, exc)
            if eb is not None:
                eb.publish("episode:resume_failed",
                           {"episode_id": episode_id, "error": str(exc)})
            raise
        if eb is not None:
            eb.publish("episode:resume_succeeded", {"episode_id": episode_id})
        episode = await self.context.episode_repository.get_required(episode_id)
        dispatch_id = episode.get("dispatch_id") or ""
        if dispatch_id:
            svc = EpisodeService(self.context)
            await svc.refresh_dispatch_status(dispatch_id)
            report = await self.query_projection(dispatch_id)
            self._publish_final_report_if_complete(dispatch_id, report)
        return result

    def _publish_final_report_if_complete(self, dispatch_id: str,
                                          report: dict) -> bool:
        """Publish final counts only after the dispatch projection is complete."""
        if report.get("status") != "completed":
            counts = report.get("counts") or {}
            self.context.event_outbox.dispatch_state_changed(
                dispatch_id=dispatch_id, status="waiting_patients", counts=counts)
            return False
        counts = report.get("counts") or {}
        self.context.event_outbox.dispatch_state_changed(
            dispatch_id=dispatch_id, status="completed", counts=counts)
        self.context.event_outbox.dispatch_report_ready(
            dispatch_id=dispatch_id, status="completed", counts=counts,
            reports_by_episode=report.get("reports_by_episode") or {})
        return True

    # ===== 查询投影（无副作用）=====
    async def query_projection(self, dispatch_id: str | None = None) -> dict:
        """读取调度投影（§5/§6：真实状态，非占位文案；只读，不运行图、不改状态）。

        从 dispatch_runs 投影读调度状态/业务日期，从 episode_runs 投影汇总
        total/completed/waiting/failed/handoff 真实计数。dispatch_id 缺省取最近一次。
        """
        repo = self.context.dispatch_repository
        if dispatch_id:
            run = await repo.get(dispatch_id)
        else:
            run = await repo.get_latest()
        if run is None:
            return {"dispatch_id": dispatch_id or "", "status": "idle",
                    "note": "尚无调度记录"}
        did = run["dispatch_id"]
        ec = await self.context.episode_repository.count_episode_statuses(did)
        counts = {
            "total_agents": ec.get("total", 0),
            "completed": ec.get("completed", 0),
            "waiting": ec.get("waiting", 0),
            "failed": ec.get("failed", 0),
            "handoff": ec.get("handoff", 0),
            "unfinished": ec.get("unfinished", 0),
            "send_count": ec.get("total", 0),
        }
        # 患者级统计（StatsPanel 字段），聚合自 Episode 投影，不依赖前端本地累计
        counts.update(await self._build_patient_stats(did))
        payload = run.get("payload") or {}
        reports_by_episode = {
            ep["episode_id"]: ep["report"]
            for ep in await self.context.episode_repository.list_by_dispatch(did)
            if ep.get("report")
        }
        return {
            "dispatch_id": did,
            "status": run.get("status", "unknown"),
            "counts": counts,
            "business_date": payload.get("business_date", ""),
            "callback_policy_version": payload.get("callback_policy_version"),
            "send_roster": payload.get("send_roster") or [],
            "skip_roster": payload.get("skip_roster") or [],
            "roster_details": payload.get("roster_details") or {},
            "reports_by_episode": reports_by_episode,
            "summary": (f"总调度 {run.get('status')}：共 {counts['total_agents']} 位个体 Agent，"
                        f"{counts['completed']} 完成，{counts['waiting']} 等待人工，"
                        f"{counts['handoff']} 转人工，{counts['failed']} 异常"),
        }

    # ===== 患者级统计聚合 =====
    async def _build_patient_stats(self, dispatch_id: str) -> dict:
        """聚合患者级统计（StatsPanel 字段），数据源为 Episode 投影 + 患者主数据。

        每例终态 Episode 的 report 含 name / risk.level / risk.score；未产出终态
        报告的 Episode（等待人工/未回复/失败）计入 no_reply_patients。诊断字段从
        患者主数据补齐（report 本身不含 diagnosis）。
        """
        episodes = await self.context.episode_repository.list_by_dispatch(dispatch_id)
        dispatch = await self.context.dispatch_repository.get(dispatch_id)
        dispatch_payload = (dispatch or {}).get("payload") or {}
        send_roster = dispatch_payload.get("send_roster") or []
        patients: dict[str, dict] = {}
        try:
            for p in self.context.patient_repository.get_all_patients():
                patients[str(p.get("patient_id"))] = p
        except Exception:
            pass  # 主数据不可用时降级为空，不影响 Episode 投影内的姓名

        assessed: list[dict] = []  # 有终态报告（已评分）
        unassessed: list[dict] = []  # 未产出终态报告，不能直接等同于三日未回复
        callback_patients: list[dict] = []  # 数据库字段命中 YAML 电话回访阈值
        follow_up_needed_patients: list[dict] = []
        callback_threshold = max(
            1, int(getattr(self.context.settings, "phone_callback_threshold_days", 3) or 3)
        )
        callback_roster = {
            str(p.get("patient_id")): p
            for p in send_roster
            if int(p.get("consecutive_no_reply_days") or 0) >= callback_threshold
        }
        episode_patient_ids = {str(ep.get("patient_id", "")) for ep in episodes}
        high = medium = low = 0
        for ep in episodes:
            pid = str(ep.get("patient_id", ""))
            pm = patients.get(pid) or {}
            report = ep.get("report") or {}
            # 风险结果可能已经持久化到 Episode，但报告事件还未补齐；不能因此误判为未回复。
            risk = report.get("risk") or ep.get("risk_result") or {}
            level = risk.get("level")
            score = risk.get("score", risk.get("total_score"))
            name = report.get("name") or pm.get("name") or f"患者{pid}"
            diag = pm.get("diagnosis", "")
            no_reply_days = int(pm.get("consecutive_no_reply_days") or 0)
            needs_follow_up = bool(report.get("follow_up_needed")) or report.get(
                "status") in ("incomplete_handoff", "human_handoff")
            # “未回复”只认数据库患者字段，不认旧事件/名单里的残留标记。
            is_callback = no_reply_days >= callback_threshold
            if level in ("high", "medium", "low") and score is not None:
                assessed_item = {"patient_id": pid, "name": name, "diagnosis": diag,
                                 "level": level, "score": score}
                assessed.append(assessed_item)
                if level == "high":
                    high += 1
                elif level == "medium":
                    medium += 1
                else:
                    low += 1
            else:
                patient_item = {
                    "patient_id": pid, "name": name, "diagnosis": diag,
                    "score": None, "consecutive_no_reply_days": no_reply_days,
                    "follow_up_needed": needs_follow_up,
                    "follow_up_reason": report.get("follow_up_reason") or "信息不完整，需进一步随访"
                    if needs_follow_up else "",
                }
                unassessed.append(patient_item)
                if needs_follow_up:
                    follow_up_needed_patients.append(patient_item)
                if is_callback:
                    callback_patients.append(patient_item)

        # 电话回访患者不创建 Episode，因此补入未评估/回访名单，避免统计漏人。
        for pid, patient in callback_roster.items():
            if pid in episode_patient_ids:
                continue
            no_reply_days = int(patient.get("consecutive_no_reply_days") or 0)
            reason = f"连续{no_reply_days}天未回复，需电话回访"
            callback_patients.append({
                "patient_id": pid,
                "name": patient.get("name") or patients.get(pid, {}).get("name", ""),
                "diagnosis": patient.get("diagnosis") or patients.get(pid, {}).get("diagnosis", ""),
                "score": None,
                "consecutive_no_reply_days": no_reply_days,
                "follow_up_needed": True,
                "follow_up_reason": reason,
            })
            unassessed.append(callback_patients[-1])
            follow_up_needed_patients.append(callback_patients[-1])

        # 应随访人数以本次冻结的 send_roster 为准。电话回访患者不创建普通
        # Episode，但仍然属于本次应随访名单，不能只按 Episode 数量统计。
        total_followup = len(send_roster) or (
            len(episodes) + len(
                [pid for pid in callback_roster if pid not in episode_patient_ids]
            )
        )

        high_risk_focus = sorted(
            [r for r in assessed if (r.get("score") or 0) >= 8],
            key=lambda r: r.get("score", 0), reverse=True)
        low_risk_exempt = sorted(
            [r for r in assessed if r.get("score") is not None and (r.get("score") or 0) <= 1],
            key=lambda r: r.get("score", 0))
        return {
            "high_risk_count": high,
            "medium_risk_count": medium,
            "low_risk_count": low,
            "callback_count": len(callback_patients),
            "no_reply_count": len(callback_patients),
            "total_assessed": len(assessed),
            "total_followup": total_followup,
            "total_agents": total_followup,
            "send_count": total_followup,
            "sent_count": total_followup,
            "unassessed_count": len(unassessed),
            "follow_up_needed_count": len(follow_up_needed_patients),
            "follow_up_needed_patients": follow_up_needed_patients,
            # 统计页需要展示具体患者结果，不能只返回分类计数。
            "assessed_patients": assessed,
            "high_risk_focus": high_risk_focus,
            "low_risk_exempt": low_risk_exempt,
            "no_reply_patients": callback_patients,
        }

    # ===== 电话回访策略编译 / 确认 =====
    async def compile_callback_policy(self, natural_language: str) -> dict:
        """编译电话回访自然语言 → 候选策略 + 命中人数预览（说明书 5.4）。"""
        from agents.capability_agents.callback_policy_compiler_agent import CallbackPolicyCompilerAgent
        from domain.models.callback_policy import CallbackPolicy, evaluate_callback_policy
        from domain.services.roster_decider import RosterDecider
        agent = CallbackPolicyCompilerAgent(self.context.llm_gateway)
        draft = await agent.compile(natural_language)
        # 在基础应访名单上预览命中人数
        patients = self.context.patient_repository.get_all_patients()
        bd = self.context.clock.get_business_date()
        roster = RosterDecider().decide_base_roster(
            patients,
            business_date=bd,
            no_reply_threshold=getattr(
                self.context.settings, "phone_callback_threshold_days", 3),
        )
        policy = CallbackPolicy(**draft.model_dump(), policy_date=bd.isoformat())
        hit_count, hits = evaluate_callback_policy(policy, roster.send_list)
        policy.hit_count = hit_count
        policy.hit_patients = hits
        return {"draft": policy.model_dump(), "base_send_count": len(roster.send_list),
                "base_skip_count": len(roster.skip_list)}

    async def approve_callback_policy(self, natural_language: str,
                                       approved_by: str = "") -> dict:
        """确认电话回访策略 → 冻结不可变快照（说明书 5.4）。"""
        preview = await self.compile_callback_policy(natural_language)
        policy_dict = preview["draft"]
        from domain.models.callback_policy import CallbackPolicy
        from infrastructure.date_provider import DateProvider
        policy = CallbackPolicy(**policy_dict)
        snap = self.context.policy_repository.save_policy(
            policy, approved_by=approved_by,
            approved_at=DateProvider().now().isoformat(),
        )
        return {"policy_version": snap.policy_version, "status": "approved",
                "raw_text": snap.raw_text}
