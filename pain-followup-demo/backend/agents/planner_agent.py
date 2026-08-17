# backend/agents/planner_agent.py
"""PlannerAgent —— 随访计划生成工作流（Agent-centric迁移方案 §4.6 / §7.6 / §14.1）。

  extract_discharge_context → retrieve_guidelines(RAG) → draft_plan(PlanGenerationAgent)
  → hitl_review(interrupt 医生审批) → persist_plan(PlanRepository)

约定：
  - 计划生成独立于当天调度（§14.1）；
  - HITL：医生审批走 LangGraph interrupt，resume 返回 decision/doctor_plan_json/doctor_id
    （approved / modified / rejected）；
  - 免审批路径：generate_draft 走 draft 图（无 interrupt），供 REST 路由 /api/plans/generate
    生成草稿（plan status=draft），医生随后通过现有 approve/modify API 确认；
  - 节点统一经 Runtime[AppContext] 获取依赖（§7），不再调用全局 get_context()。
"""
from __future__ import annotations

from langgraph.errors import GraphInterrupt
from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agents.states import PlanState
from agents.capability_agents.plan_generation_agent import PlanGenerationAgent
from infrastructure.langgraph import get_checkpointer, thread_config
from infrastructure.runtime_context import AppContext, get_context


# ---- 节点 ----

def extract_discharge_context_node(state: PlanState,
                                   runtime: Runtime[AppContext]) -> dict:
    """读取患者档案/近 7 天记录，拼装患者简报与出院小结（§4.6 extract）。"""
    ctx = runtime.context
    pid = state.get("patient_id", "")
    p = ctx.patient_repository.get_patient(pid) or {}
    history = ctx.patient_repository.get_history(pid, 7) or []
    brief = _build_patient_brief(p, history, state.get("diagnosis"))
    discharge = state.get("discharge_summary") or p.get("discharge_summary") or ""
    return {"patient_brief": brief, "discharge_summary": discharge,
            "diagnosis": p.get("diagnosis", "")}


def retrieve_guidelines_node(state: PlanState) -> dict:
    """RAG 检索指南/共识（§4.6 retrieve_guidelines）。知识库为空或异常时降级。"""
    from knowledge.retriever import retrieve_guidelines
    brief = state.get("patient_brief", "")
    discharge = state.get("discharge_summary", "")
    try:
        chunks = retrieve_guidelines(
            query=discharge or brief,
            diagnosis=state.get("diagnosis", ""),
            discharge_summary=discharge,
            symptoms=brief[:200],
        )
    except Exception:
        chunks = []
    citations = [
        {"source": c.source, "title": c.title, "page": c.page,
         "text": (c.text or "")[:120], "score": round(float(c.score), 3)}
        for c in (chunks or [])
    ]
    evidence = "\n".join(
        f"- [{c.get('source')}] {c.get('title')}: {c.get('text')}" for c in citations[:5]
    ) or "（知识库暂无匹配指南，按通用随访标准生成）"
    return {"citations": citations, "retrieved_evidence": evidence}


async def draft_plan_node(state: PlanState, runtime: Runtime[AppContext]) -> dict:
    """PlanGenerationAgent 生成计划草稿（LLM 不可用时确定性兜底）。"""
    ctx = runtime.context
    agent = PlanGenerationAgent(ctx.llm_gateway)
    plan = await agent.generate(
        patient_brief=state.get("patient_brief", ""),
        discharge_summary=state.get("discharge_summary", ""),
        retrieved_evidence=state.get("retrieved_evidence", ""),
    )
    return {"plan_json": plan or {}}


async def hitl_review_node(state: PlanState) -> dict:
    """医生审批（interrupt）：暂停等待医生 decision，resume 后返回审阅结果。"""
    payload = interrupt({
        "type": "plan_review",
        "patient_id": state.get("patient_id", ""),
        "plan_json": state.get("plan_json", {}),
        "citations": state.get("citations") or [],
    })
    decision = payload.get("decision", "approved") if isinstance(payload, dict) else "approved"
    doctor_plan_json = payload.get("plan_json") if isinstance(payload, dict) else None
    doctor_id = payload.get("doctor_id") if isinstance(payload, dict) else None
    return {"review_decision": decision, "doctor_plan_json": doctor_plan_json,
            "doctor_id": doctor_id}


def persist_plan_node(state: PlanState, runtime: Runtime[AppContext]) -> dict:
    """按医生 decision 落库最终计划（approved/modified → status=approved；rejected 不落库）。

    §11：repo 报错一律抛出，不再吞异常回退旧 plan_id（宁可失败可见，不静默丢数据）。
    """
    ctx = runtime.context
    decision = state.get("review_decision") or "approved"
    if decision == "rejected":
        return {"plan_id": state.get("plan_id"), "plan_json": state.get("plan_json") or {}}
    final_plan = state.get("doctor_plan_json") or state.get("plan_json") or {}
    plan_id = ctx.plan_repository.create_plan(
        patient_id=state.get("patient_id", ""),
        plan_json=final_plan,
        discharge_summary=state.get("discharge_summary", ""),
        citations=state.get("citations") or [],
        status="approved",
    )
    return {"plan_id": plan_id, "plan_json": final_plan}


def persist_draft_node(state: PlanState, runtime: Runtime[AppContext]) -> dict:
    """免审批路径：落库草稿（status=draft），供医生后续 approve/modify。

    §11：repo 报错一律抛出，不再吞异常静默返回空 plan_id。
    """
    ctx = runtime.context
    plan_id = ctx.plan_repository.create_plan(
        patient_id=state.get("patient_id", ""),
        plan_json=state.get("plan_json") or {},
        discharge_summary=state.get("discharge_summary", ""),
        citations=state.get("citations") or [],
        status="draft",
    )
    return {"plan_id": plan_id}


# ---- 图 ----

def build_draft_graph():
    """免审批草稿图：extract → RAG → draft → 落库 draft。"""
    g = StateGraph(PlanState, context_schema=AppContext)
    g.add_node("extract_discharge_context", extract_discharge_context_node)
    g.add_node("retrieve_guidelines", retrieve_guidelines_node)
    g.add_node("draft_plan", draft_plan_node)
    g.add_node("persist_draft", persist_draft_node)
    g.add_edge(START, "extract_discharge_context")
    g.add_edge("extract_discharge_context", "retrieve_guidelines")
    g.add_edge("retrieve_guidelines", "draft_plan")
    g.add_edge("draft_plan", "persist_draft")
    g.add_edge("persist_draft", END)
    return g.compile()


def build_planner_graph():
    """HITL 图：extract → RAG → draft → 医生 interrupt 审批 → 落库最终计划。"""
    g = StateGraph(PlanState, context_schema=AppContext)
    g.add_node("extract_discharge_context", extract_discharge_context_node)
    g.add_node("retrieve_guidelines", retrieve_guidelines_node)
    g.add_node("draft_plan", draft_plan_node)
    g.add_node("hitl_review", hitl_review_node)
    g.add_node("persist_plan", persist_plan_node)
    g.add_edge(START, "extract_discharge_context")
    g.add_edge("extract_discharge_context", "retrieve_guidelines")
    g.add_edge("retrieve_guidelines", "draft_plan")
    g.add_edge("draft_plan", "hitl_review")
    g.add_edge("hitl_review", "persist_plan")
    g.add_edge("persist_plan", END)
    return g.compile(checkpointer=get_checkpointer())


planner_workflow = build_planner_graph()
draft_workflow = build_draft_graph()


async def generate_draft(patient_id: str, *, diagnosis: str | None = None,
                         discharge_summary: str | None = None,
                         context=None) -> dict:
    """免审批草稿入口（REST /api/plans/generate，医生随后 approve/modify）。

    返回 {plan_id, plan_json, citations, diagnosis, patient_brief, discharge_summary}。
    context：显式注入 AppContext（§7）；缺省取全局单例。
    """
    if context is None:
        context = get_context()
    result = await draft_workflow.ainvoke({
        "patient_id": patient_id, "diagnosis": diagnosis,
        "discharge_summary": discharge_summary or "",
    }, context=context)
    return {
        "plan_id": result.get("plan_id"),
        "plan_json": result.get("plan_json") or {},
        "citations": result.get("citations") or [],
        "diagnosis": result.get("diagnosis", ""),
        "patient_brief": result.get("patient_brief", ""),
        "discharge_summary": result.get("discharge_summary", ""),
    }


async def run_planner(patient_id: str, *, diagnosis: str | None = None,
                      discharge_summary: str | None = None,
                      thread_id: str | None = None,
                      context=None) -> dict:
    """HITL 入口：跑完整图，在医生审批处 interrupt 挂起。

    返回 {"status": "waiting_review", "interrupt": payload}；resume_planner 恢复。
    """
    if context is None:
        context = get_context()
    thread_id = thread_id or f"plan:{patient_id}"
    try:
        result = await planner_workflow.ainvoke(
            {"patient_id": patient_id, "diagnosis": diagnosis,
             "discharge_summary": discharge_summary or ""},
            config=thread_config(thread_id),
            context=context,
        )
        return {"status": "approved", **result}
    except GraphInterrupt as exc:
        payload = exc.interrupt_value if hasattr(exc, "interrupt_value") else None
        return {"status": "waiting_review", "interrupt": payload,
                "patient_id": patient_id}


async def resume_planner(thread_id: str, *, decision: str = "approved",
                         plan_json: dict | None = None,
                         doctor_id: str | None = None,
                         context=None) -> dict:
    """医生审批后恢复 HITL 图：decision ∈ approved | modified | rejected。"""
    from langgraph.types import Command
    if context is None:
        context = get_context()
    payload = {"decision": decision, "plan_json": plan_json, "doctor_id": doctor_id}
    result = await planner_workflow.ainvoke(
        Command(resume=payload),
        config=thread_config(thread_id),
        context=context,
    )
    return result


def _build_patient_brief(p: dict, history: list[dict], diagnosis_override=None) -> str:
    diagnosis = diagnosis_override or p.get("diagnosis", "")
    lines = [f"患者：{p.get('name','')}，{p.get('age','')}岁，{p.get('gender','')}",
             f"诊断：{diagnosis}",
             f"疼痛类型：{p.get('pain_type','慢性疼痛')}"]
    if p.get("discharge_date"):
        lines.append(f"出院日期：{p.get('discharge_date')}")
    if history:
        h = history[-1]
        lines.append(
            f"最近记录：NRS={h.get('nrs_score')}, 睡眠={h.get('sleep_quality')}, "
            f"用药={h.get('medication_taken')}")
    return "\n".join(lines)
