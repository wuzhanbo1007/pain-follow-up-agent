"""
编排器（Orchestrator，多 Agent 架构改造 - 任务 5）

把 B 判定 → C 执行 → D 审阅 串接成一条随访流水线：

    run_followup_pipeline(patient_id, ...) -> dict
        ctx = {
            "patient_id", "patient",
            "b_decision":  <scheduler_agent.decide_today 结果>,
            "c_session":   <execution.run_session 结果>,
            "d_review":    <review_agent.run_review 结果>,
            "review_id":   <落库后的 review_id>,
            "summary": ...
        }

  - B 判定为“今日无需随访”时提前返回（ctx 含 early_return=True）。
  - 每步结果写入 ctx，最后返回汇总；返回结构向前兼容（原 run_session 字段保留，可增字段）。

多 Agent 职责一览（详见 agents/__init__.py 文档）：
  A 号 planner       ：随访计划生成工作流（LLM + RAG + HITL）
  B 号 daily_scheduler：当日随访判定（规则优先 + LLM 兜底）
  C 号 execution     ：随访执行 Agent（ReACT 自主循环）
  D 号 review_agent  ：随访会话审阅 Agent（LLM 结构化审阅 + 规则兜底）
  orchestrator      ：上述 Agent 的串行编排器
"""
from data.database import PatientDB
from db import followup_db
from services.daily_scheduler import decide_today
from agents.execution import run_session
from agents.summarizer import summarize_session


async def run_followup_pipeline(patient_id: str, plan_id: str = None, today=None, auto_review: bool = True) -> dict:
    """串行执行 B → C → D，并把每步结果写入 ctx 返回（全链路 async，与 Comet 编排一致）。

    Args:
        patient_id: 患者 ID
        plan_id:    已批准计划 ID（透传给 C 执行 Agent）
        today:      判定用“今天”
        auto_review: 是否在 C 之后自动跑 D 审阅并落库
    Returns:
        dict: 流水线上下文（见模块 docstring）
    """
    ctx: dict = {"patient_id": patient_id}

    db = PatientDB()
    patient = db.get_patient(patient_id)
    if not patient:
        return {**ctx, "error": f"未找到患者 {patient_id}"}
    ctx["patient"] = patient

    # ---- B 号：当日随访判定 ----
    plan = followup_db.get_latest_plan(patient_id)
    b_decision = await decide_today(patient, plan=plan, today=today)
    ctx["b_decision"] = b_decision

    # B 判定为不需要随访 → 提前返回
    if not b_decision.get("need_followup", True):
        ctx["early_return"] = True
        ctx["summary"] = f"今日无需随访：{b_decision.get('reason', '')}"
        return ctx

    # plan_id 未透传时，回退到最新计划的 id（followup_sessions.plan_id 列 NOT NULL，不能为 None）
    if plan_id is None and plan:
        plan_id = plan.get("plan_id")

    # ---- C 号：随访执行 ----
    c = await run_session(patient_id, plan_id=plan_id)
    ctx["c_session"] = c
    session_id = c.get("session_id")

    # ---- D 号：会话审阅 + 落库 ----
    if auto_review and session_id:
        session = followup_db.get_session(session_id) or {}
        transcripts = session.get("transcript_json", []) if isinstance(session, dict) else []
        d_review = await summarize_session(session=session, transcripts=transcripts, plan=plan)
        ctx["d_review"] = d_review

        # 落库：若会话已有 review 则附加，否则新建（避免重复生成 AI 审阅）
        existing = followup_db.get_review_by_session(session_id)
        if existing:
            followup_db.attach_ai_review(existing["review_id"], d_review)
            ctx["review_id"] = existing["review_id"]
        else:
            review_id = followup_db.create_review(
                session_id=session_id,
                patient_id=patient_id,
                track_status="followup_done",
                audit_snapshot={"source": "orchestrator", "b_decision": b_decision, "ai_review": d_review},
            )
            ctx["review_id"] = review_id

    ctx["summary"] = (c.get("agent_summary") or "")
    return ctx
