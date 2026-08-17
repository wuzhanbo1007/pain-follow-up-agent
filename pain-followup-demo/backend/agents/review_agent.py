# backend/agents/review_agent.py
"""ReviewAgent —— AI 审阅工作流（Agent-centric迁移方案 §4.5 / §8.4 / §11 P0#3）。

工作流：单个 episode 完成后只触发一次，生成 ReviewDraft 并幂等落库
followup_review，发布 review_ready 事件。医生最终确认由 DoctorReviewService 处理。

从旧 persist_episode_node 拆出：session/告警 由 PatientFollowupAgent.persist_episode
负责，审阅独立成 Agent（§4.5），保证"单个 episode 完成后只触发一次 ReviewAgent"。

节点统一经 Runtime[AppContext] 获取依赖（§7），不再调用全局 get_context()。
"""
from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime

from agents.states import ReviewState
from agents.capability_agents.ai_review_agent import AIReviewAgent
from infrastructure.runtime_context import AppContext, get_context


async def _generate_ai_review_node(state: ReviewState,
                                   runtime: Runtime[AppContext]) -> dict:
    """调用 AIReviewAgent 生成 ReviewDraft（只做生成，不落库）。"""
    ctx = runtime.context
    agent = AIReviewAgent(ctx.llm_gateway)
    draft = await agent.review(
        risk_result=state.get("risk_result") or {},
        agent_summary=state.get("agent_summary") or "",
        transcripts=state.get("transcripts") or [],
        plan=state.get("plan") or {},
        history=state.get("history") or {},
    )
    return {"draft_review": draft.model_dump(), "review_status": "ai_review_ready"}


def build_review_graph():
    g = StateGraph(ReviewState, context_schema=AppContext)
    g.add_node("generate_ai_review", _generate_ai_review_node)
    g.add_edge(START, "generate_ai_review")
    g.add_edge("generate_ai_review", END)
    return g.compile()


review_workflow = build_review_graph()


class ReviewAgent:
    """工作流 Agent：AI 审阅（§4.5）。

    入口 run_review_episode：跑 review_workflow 生成 ReviewDraft，幂等落库，
    发 review_ready。构造注入 AppContext（§9），State 只传业务数据。
    """

    def __init__(self, ctx=None):
        self._ctx = ctx or get_context()

    async def run_review_episode(self, *, dispatch_id: str, episode_id: str,
                                 patient_id: str, session_id, risk_result: dict,
                                 agent_summary: str, transcripts: list[dict],
                                 plan: dict, history: dict) -> dict:
        """§10.1：按 review_key=ai-review:{episode_id} 幂等触发一次 AI 审阅。

        create_ai_pending_once 已存在 → 直接返回已有记录（重试/重复投递不重复
        生成审阅）；首次创建 → 生成 ReviewDraft、置 ai_review_ready、写穿到
        医生面板的 MySQL 审阅行并发布 review_ready。
        """
        ctx = self._ctx
        review_key = f"ai-review:{episode_id}"
        created = ctx.review_repository.create_ai_pending_once(
            review_key=review_key, episode_id=episode_id,
            dispatch_id=dispatch_id, session_id=session_id)
        record = ctx.review_repository.get_required(review_key)
        if not created:
            return {"review_id": record["review_id"],
                    "review": record.get("ai_review_json") or {},
                    "review_status": record["status"]}
        result = await review_workflow.ainvoke({
            "dispatch_id": dispatch_id, "episode_id": episode_id,
            "patient_id": patient_id, "session_id": session_id,
            "risk_result": risk_result, "agent_summary": agent_summary,
            "transcripts": transcripts, "plan": plan, "history": history,
        }, context=ctx)
        draft = result.get("draft_review") or {}
        ctx.review_repository.attach_ai_review(review_key, draft)
        audit_snapshot = {
            "source": "review_agent",
            "dispatch_id": dispatch_id,
            "episode_id": episode_id,
            "ai_review": draft,
            "risk_result": risk_result or {},
        }
        # 写穿到 MySQL 审阅行（医生面板兼容；有 session 才建行）
        if session_id:
            legacy = ctx.followup_repository.get_review_by_session(session_id)
            if legacy:
                ctx.followup_repository.update_review_audit_snapshot(
                    legacy.get("review_id"), audit_snapshot)
                ctx.followup_repository.attach_ai_review(legacy.get("review_id"), draft)
            else:
                ctx.followup_repository.create_review(
                    session_id=session_id, patient_id=patient_id,
                    track_status="followup_done",
                    audit_snapshot=audit_snapshot,
                    review_key=review_key,
                )
        ctx.event_outbox.review_ready(
            dispatch_id=dispatch_id, episode_id=episode_id,
            patient_id=patient_id, review_key=review_key,
            ai_review=draft,
        )
        return {"review_id": record["review_id"], "review": draft,
                "review_status": "ai_review_ready"}
