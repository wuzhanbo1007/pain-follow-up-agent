# backend/services/episode_service.py
"""EpisodeService —— 独立 Episode 门面（Agent-centric 重构 §5 / §11）。

每个 Episode 用独立 thread_id 运行：
  - start(episode_id)  从 Episode 投影取 payload 启动（DispatchService 消费
    episode.start_requested 事件后逐条调用）；
  - resume(episode_id) 提交人工回复恢复同一 thread（P0#8：非 waiting_human 拒绝）；
  - refresh_dispatch_status 由 Episode 投影汇总一次总调度的状态/计数。

Episode 挂起（interrupt）只发生在独立会话图内，不阻塞其他 Episode，也不阻塞总调度图。
"""
from __future__ import annotations

from infrastructure.langgraph import thread_config
from infrastructure.runtime_context import AppContext, get_context
from agents.patient_followup_agent import patient_followup_workflow, resume_patient_episode


class EpisodeService:
    """工作流 Agent 门面：独立 Episode 的启动 / 恢复 / 调度状态汇总。"""

    def __init__(self, context: AppContext | None = None):
        self._context = context or get_context()

    async def start(self, episode_id: str) -> dict:
        """启动独立 Episode（§5）：从投影取 payload，episode_id 即 thread_id。

        运行结束后把终态写回 Episode 投影（interrupt → waiting_human；
        完成 → 终态 report；异常 → failed）。幂等键 episode:{id}:completed 防止
        重复投递重复写报告（§6.3 complete_once）。
        """
        episode = await self._context.episode_repository.get_required(episode_id)
        payload = dict(episode.get("payload") or {})
        payload.setdefault("episode_id", episode_id)
        payload.setdefault("turn_no", 0)
        payload.setdefault("slots", {})
        payload.setdefault("messages", [])
        config = thread_config(episode_id)
        try:
            result = await patient_followup_workflow.ainvoke(
                payload, context=self._context, config=config)
        except Exception as exc:
            await self._context.episode_repository.mark_failed(episode_id, exc)
            raise
        await self._record_progress(episode_id, result)
        return result

    async def resume(self, episode_id: str, reply: str) -> dict:
        """恢复挂起的人工 Episode（同一 thread_id）。

        P0#8：恢复前校验 checkpoint 状态必须为 waiting_human，否则抛
        EpisodeNotWaitingHuman（避免在错误的流程阶段注入人工回复）。
        """
        result = await resume_patient_episode(
            episode_id=episode_id, reply_text=reply, context=self._context)
        await self._record_progress(episode_id, result)
        return result

    async def _record_progress(self, episode_id: str, result: dict) -> None:
        repo = self._context.episode_repository
        if "__interrupt__" in result:
            await repo.update_status(episode_id, "waiting_human")
            return
        report = result.get("report") or {}
        status = report.get("status") or "completed"
        await repo.complete_once(
            episode_id, report, key=f"episode:{episode_id}:completed", status=status)

    async def refresh_dispatch_status(self, dispatch_id: str) -> dict | None:
        """§5 refresh_dispatch_status：由 Episode 投影汇总调度状态。

        所有 Episode 均为终态 → completed；仍有 waiting/failed/pending → dispatching。
        """
        counts = await self._context.episode_repository.count_episode_statuses(dispatch_id)
        status = "completed" if counts["unfinished"] == 0 else "dispatching"
        return await self._context.dispatch_repository.update_status(
            dispatch_id, status=status, counts=counts)
