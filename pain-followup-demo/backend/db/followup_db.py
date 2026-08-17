# backend/db/followup_db.py
# -*- coding: utf-8 -*-
"""
随访计划 / 会话 / 审阅 数据访问层（MySQL 版，对应《数据库重构方案_MySQL版.md》§4.6/4.7/4.10）

· 彻底移除 SQLite；所有读写走 SQLAlchemy 2.0 会话，目标表为 followup_plans /
  followup_sessions / doctor_reviews（21 表设计中的真实表名）。
· 函数签名与旧版保持一致，返回 dict 的字段键亦尽量兼容（doctor_score/comment/reviewer_id
  为 score/comment/doctor_id 的兼容别名；guideline_citations 为 rag_retrieval_context 别名），
  以便 agents / services / routes 无需改动即可平滑切换。
· followup_review 表在 21 表设计中改名为 doctor_reviews，字段 doctor_score→score、
  doctor_comment→comment、reviewer_id→doctor_id，本层在映射时完成转换。
"""
from datetime import date, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from data.db_config import DATABASE_URL
from data.models_mysql import (
    Base, FollowupPlan, FollowupSession, DoctorReview, DischargeRecord,
)


# ======================================================================
# 引擎 / Session（懒加载，便于测试时重定向 DATABASE_URL）
# ======================================================================
_ENGINE = None
_SessionLocal = None


def _get_sessionmaker():
    global _ENGINE, _SessionLocal
    if _SessionLocal is None:
        _ENGINE = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
        _SessionLocal = sessionmaker(bind=_ENGINE, future=True)
    return _SessionLocal


def _session():
    return _get_sessionmaker()()


def _resolve_discharge(patient_id):
    """返回 (discharge_id, doctor_id)；无出院记录时返回 (None, None)。"""
    with _session() as s:
        dis = s.scalars(
            select(DischargeRecord)
            .where(DischargeRecord.patient_id == patient_id)
            .order_by(DischargeRecord.discharge_date.desc())
        ).first()
        if dis:
            return dis.discharge_id, dis.doctor_id
    return None, None


def _resolve_doctor(patient_id):
    _, did = _resolve_discharge(patient_id)
    return did


# ======================================================================
# dict 构造（兼容旧字段键）
# ======================================================================
def _plan_to_dict(p):
    return {
        "plan_id": p.plan_id,
        "patient_id": p.patient_id,
        "discharge_id": p.discharge_id,
        "doctor_id": p.doctor_id,
        "plan_json": p.plan_json,
        "rag_query_text": p.rag_query_text,
        # 21 表设计中本列为 rag_retrieval_context；以下别名保持旧调用兼容
        "rag_retrieval_context": p.rag_retrieval_context,
        "guideline_citations": p.rag_retrieval_context,
        "status": p.status,
        "created_by": p.created_by,
        "original_snapshot": p.original_snapshot,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
    }


def _session_to_dict(s):
    return {
        "session_id": s.session_id,
        "patient_id": s.patient_id,
        "plan_id": s.plan_id,
        "session_status": s.session_status,
        "transcript_json": s.transcript_json or [],
        "agent_summary": s.agent_summary,
        "channel": s.channel,
        "voice_mode": s.voice_mode,
        "escalation_status": s.escalation_status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        # 旧 followup_session 的 risk_result 列已在 21 表设计中移除；下游用 .get 安全读取
    }


def _review_to_dict(r):
    return {
        "review_id": r.review_id,
        "session_id": r.session_id,
        "patient_id": r.patient_id,
        # 兼容别名（旧列名 → 新列名）
        "doctor_score": r.score,
        "doctor_comment": r.comment,
        "reviewer_id": r.doctor_id,
        # 真实列名
        "score": r.score,
        "comment": r.comment,
        "doctor_id": r.doctor_id,
        "track_status": r.track_status,
        "ai_review": r.ai_review,
        "audit_snapshot": r.audit_snapshot,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ======================================================================
# followup_plans
# ======================================================================
def create_plan(patient_id, plan_json, discharge_summary=None,
               citations=None, doctor_id=None, status="draft"):
    """写入一条随访计划；自动解析 discharge_id / doctor_id（NOT NULL 兜底为 0）。"""
    dis_id, d_id = _resolve_discharge(patient_id)
    if doctor_id is None:
        doctor_id = d_id
    with _session() as s:
        p = FollowupPlan(
            patient_id=patient_id,
            discharge_id=dis_id if dis_id is not None else 0,
            doctor_id=doctor_id if doctor_id is not None else 0,
            plan_json=plan_json or {},
            rag_retrieval_context=citations or [],
            status=status,
            created_by="AI",
        )
        s.add(p)
        s.flush()
        pid = p.plan_id
        s.commit()
        return pid


def get_plan(plan_id):
    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        return None
    with _session() as s:
        p = s.get(FollowupPlan, plan_id)
        return _plan_to_dict(p) if p else None


def get_latest_plan(patient_id):
    with _session() as s:
        p = s.scalars(
            select(FollowupPlan)
            .where(FollowupPlan.patient_id == patient_id)
            .order_by(FollowupPlan.created_at.desc())
        ).first()
        return _plan_to_dict(p) if p else None


def list_plans_by_doctor(doctor_id):
    try:
        doctor_id = int(doctor_id)
    except (TypeError, ValueError):
        return []
    with _session() as s:
        rows = s.scalars(
            select(FollowupPlan)
            .where(FollowupPlan.doctor_id == doctor_id)
            .order_by(FollowupPlan.created_at.desc())
        ).all()
        return [_plan_to_dict(r) for r in rows]


def list_all_latest_plans():
    """每位患者最新一条随访计划（按 patient_id 去重）。"""
    with _session() as s:
        rows = s.scalars(
            select(FollowupPlan)
            .order_by(FollowupPlan.patient_id, FollowupPlan.created_at.desc())
        ).all()
    latest = {}
    for r in rows:
        if r.patient_id not in latest:
            latest[r.patient_id] = r
    return [_plan_to_dict(r) for r in latest.values()]


def approve_plan(plan_id, doctor_id=None):
    """医生同意：状态→approved"""
    return _update_plan_status(plan_id, "approved", doctor_id)


def modify_plan(plan_id, plan_json, doctor_id=None):
    """医生修改后确认：保留原文快照，状态→modified"""
    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        return False
    with _session() as s:
        p = s.get(FollowupPlan, plan_id)
        if not p:
            return False
        p.original_snapshot = p.plan_json
        p.plan_json = plan_json
        p.status = "modified"
        if doctor_id is not None:
            p.doctor_id = doctor_id
        p.reviewed_at = datetime.now()
        s.commit()
        return True


def batch_approve_plans(doctor_id=None, only_draft=True):
    """批量同意：按患者去重后，将每位患者最新一条 draft/modified 计划置为 approved。"""
    with _session() as s:
        rows = s.scalars(
            select(FollowupPlan)
            .order_by(FollowupPlan.patient_id, FollowupPlan.created_at.desc())
        ).all()
    latest = {}
    for r in rows:
        if r.patient_id not in latest:
            latest[r.patient_id] = r
    plan_ids = [
        r.plan_id for r in latest.values()
        if r.status in ("draft", "modified")
    ]
    now = datetime.now()
    with _session() as s:
        for pid in plan_ids:
            p = s.get(FollowupPlan, pid)
            if p:
                p.status = "approved"
                if doctor_id is not None:
                    p.doctor_id = doctor_id
                p.reviewed_at = now
        s.commit()
    return {"approved": len(plan_ids), "plan_ids": plan_ids}


def _update_plan_status(plan_id, status, doctor_id=None):
    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        return False
    with _session() as s:
        p = s.get(FollowupPlan, plan_id)
        if not p:
            return False
        p.status = status
        if doctor_id is not None:
            p.doctor_id = doctor_id
        p.reviewed_at = datetime.now()
        s.commit()
        return True


# ======================================================================
# followup_sessions
# ======================================================================
def create_session(patient_id, transcript_json, risk_result=None,
                  agent_summary=None, plan_id=None):
    """写入一条随访会话；risk_result 在 21 表设计中已无对应列，忽略。"""
    with _session() as s:
        if plan_id is None:
            # plan_id 列 NOT NULL：未指定时回退到该患者最新计划，避免 INSERT 失败
            p = s.scalars(
                select(FollowupPlan)
                .where(FollowupPlan.patient_id == patient_id)
                .order_by(FollowupPlan.created_at.desc())
            ).first()
            plan_id = p.plan_id if p else None
        sess = FollowupSession(
            patient_id=patient_id,
            plan_id=int(plan_id) if plan_id is not None else None,
            transcript_json=transcript_json or [],
            agent_summary=agent_summary or "",
            channel="app",
            completed_at=datetime.now(),  # channel/completed_at 列 NOT NULL 无默认，给占位
            session_status="ongoing",
        )
        s.add(sess)
        s.flush()
        sid = sess.session_id
        s.commit()
        return sid


def get_today_session(patient_id):
    """返回该患者今天已有的随访会话（若有），用于去重，避免重复生成记录。"""
    today = date.today().isoformat()
    with _session() as s:
        rows = s.scalars(
            select(FollowupSession)
            .where(FollowupSession.patient_id == patient_id)
            .order_by(FollowupSession.created_at.desc())
        ).all()
    for r in rows:
        if r.created_at and r.created_at.strftime("%Y-%m-%d") == today:
            return _session_to_dict(r)
    return None


def update_session(session_id, transcript_json=None, risk_result=None,
                   agent_summary=None):
    """更新已有会话的对话/摘要（risk_result 已无对应列，忽略）。"""
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return False
    with _session() as s:
        sess = s.get(FollowupSession, session_id)
        if not sess:
            return False
        if transcript_json is not None:
            sess.transcript_json = transcript_json
        if agent_summary is not None:
            sess.agent_summary = agent_summary
        s.commit()
        return True


def get_session(session_id):
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return None
    with _session() as s:
        sess = s.get(FollowupSession, session_id)
        if not sess:
            return None
        d = _session_to_dict(sess)
        d["ai_review"] = get_review_ai(session_id)
        d["risk_result"] = get_review_risk(session_id, d.get("agent_summary"))
        return d


def get_review_ai(session_id):
    """返回该会话最新一条审阅中的 AI（D 号 Agent）审阅意见。"""
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return None
    with _session() as s:
        r = s.scalars(
            select(DoctorReview)
            .where(DoctorReview.session_id == session_id)
            .order_by(DoctorReview.reviewed_at.desc())
        ).first()
        return r.ai_review if r else None


def _review_risk_result(review, agent_summary=""):
    """从审阅快照恢复风险结果，兼容旧版未在 session 表保存风险的记录。"""
    if not review:
        review_risk = None
        ai_review = None
    else:
        snapshot = review.audit_snapshot or {}
        review_risk = snapshot.get("risk_result") if isinstance(snapshot, dict) else None
        ai_review = review.ai_review or {}

    if isinstance(review_risk, dict) and review_risk:
        normalized = dict(review_risk)
        if normalized.get("total_score") is None and normalized.get("score") is not None:
            normalized["total_score"] = normalized["score"]
        return normalized

    # 旧记录没有 risk_result 时，AI 审阅仍可能保留风险等级和评分。
    ai_level = ai_review.get("risk_level") if isinstance(ai_review, dict) else None
    ai_score = ai_review.get("total_score") if isinstance(ai_review, dict) else None
    if ai_level:
        labels = {
            "high": "高风险", "high_risk": "高风险",
            "medium": "中风险", "medium_risk": "中风险",
            "low": "低风险", "low_risk": "低风险",
            "unknown": "未评估",
        }
        return {
            "level": ai_level,
            "score": ai_score,
            "total_score": ai_score,
            "level_label": labels.get(ai_level, ai_level),
        }

    # 未回复患者没有评分，但需要明确展示为“未评估”，而不是“未知”。
    if "未回复" in (agent_summary or ""):
        return {
            "level": "unknown",
            "score": None,
            "total_score": None,
            "level_label": "未评估",
            "action": "电话回访",
        }
    return None


def get_review_risk(session_id, agent_summary=""):
    """返回会话对应的风险结果，优先读取审阅快照。"""
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return None
    with _session() as s:
        review = s.scalars(
            select(DoctorReview)
            .where(DoctorReview.session_id == session_id)
            .order_by(DoctorReview.reviewed_at.desc())
        ).first()
        return _review_risk_result(review, agent_summary)


def _review_dispatch_id(review):
    if not review:
        return None
    snapshot = review.audit_snapshot or {}
    if not isinstance(snapshot, dict):
        return None
    return snapshot.get("dispatch_id")


def list_sessions_by_doctor(doctor_id, patient_id=None):
    """医生名下待审阅会话（通过出院记录关联医生）。"""
    try:
        doctor_id = int(doctor_id)
    except (TypeError, ValueError):
        return []
    with _session() as s:
        pids = [pid for (pid,) in s.execute(
            select(DischargeRecord.patient_id)
            .where(DischargeRecord.doctor_id == doctor_id)
        ).all()]
    if patient_id is not None:
        pids = [patient_id]
    with _session() as s:
        if pids:
            rows = s.scalars(
                select(FollowupSession)
                .where(FollowupSession.patient_id.in_(pids))
                .order_by(FollowupSession.created_at.desc())
            ).all()
        else:
            rows = []
    return [_session_to_dict(r) for r in rows]


def get_latest_transcripts():
    """返回每个患者最新一次随访会话的 transcript（dict: patient_id -> [{role, content}])。"""
    with _session() as s:
        rows = s.scalars(
            select(FollowupSession)
            .order_by(FollowupSession.patient_id, FollowupSession.created_at.desc())
        ).all()
    result = {}
    seen = set()
    for r in rows:
        if r.patient_id in seen:
            continue  # 已保留该患者最新一条
        seen.add(r.patient_id)
        result[r.patient_id] = r.transcript_json or []
    return result


# ======================================================================
# doctor_reviews（原 followup_review）
# ======================================================================
def create_review(session_id, patient_id, doctor_score=None,
                  doctor_comment=None, track_status="followup_done",
                  reviewer_id=None, audit_snapshot=None, ai_review=None):
    """写入一条医生审阅。

    注意：session_id 可能传入字符串（如演示用 "no-reply-<pid>"），而 21 表设计中
    session_id 为 INT（可空）。此处强转 int，失败则置 None（生成孤儿审阅，不影响主流程）。
    """
    sid = None
    if session_id is not None:
        try:
            sid = int(session_id)
        except (TypeError, ValueError):
            sid = None
    did = None
    if reviewer_id is not None:
        try:
            did = int(reviewer_id)
        except (TypeError, ValueError):
            did = None
    if did is None:
        did = _resolve_doctor(patient_id)
    if did is None:
        did = 0
    with _session() as s:
        # ai_review 优先用显式参数；兼容旧调用（audit_snapshot 里塞 ai_review 的写法）
        if ai_review is None and isinstance(audit_snapshot, dict):
            ai_review = audit_snapshot.get("ai_review")
        rv = DoctorReview(
            session_id=sid,
            patient_id=patient_id,
            doctor_id=did,
            score=doctor_score,
            comment=doctor_comment,
            track_status=track_status,
            audit_snapshot=audit_snapshot or {},
            ai_review=ai_review,
        )
        s.add(rv)
        s.flush()
        rid = rv.review_id
        s.commit()
        return rid


def get_review_by_session(session_id):
    """返回该会话已有的审阅行（用于避免重复生成 AI 审阅）。"""
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return None
    with _session() as s:
        r = s.scalars(
            select(DoctorReview)
            .where(DoctorReview.session_id == session_id)
            .order_by(DoctorReview.reviewed_at.desc())
        ).first()
        return _review_to_dict(r) if r else None


def attach_ai_review(review_id, ai_review):
    """把 D 号 Agent 的结构化审阅结果写入 review 行的 ai_review 列。"""
    try:
        review_id = int(review_id)
    except (TypeError, ValueError):
        return False
    with _session() as s:
        r = s.get(DoctorReview, review_id)
        if not r:
            return False
        r.ai_review = ai_review
        s.commit()
        return True


def update_review_audit_snapshot(review_id, patch):
    """幂等补充审阅快照，供已有审阅记录补写风险结果。"""
    try:
        review_id = int(review_id)
    except (TypeError, ValueError):
        return False
    with _session() as s:
        review = s.get(DoctorReview, review_id)
        if not review:
            return False
        snapshot = dict(review.audit_snapshot or {})
        snapshot.update(patch or {})
        review.audit_snapshot = snapshot
        s.commit()
        return True


def list_reviews(status=None, patient_id=None):
    with _session() as s:
        stmt = select(DoctorReview)
        if status:
            stmt = stmt.where(DoctorReview.track_status == status)
        if patient_id is not None:
            stmt = stmt.where(DoctorReview.patient_id == patient_id)
        stmt = stmt.order_by(DoctorReview.reviewed_at.desc())
        rows = s.scalars(stmt).all()
    return [_review_to_dict(r) for r in rows]


def get_latest_sessions(today=None, dispatch_id=None):
    """返回每个患者最新一次随访会话（按 created_at DESC 去重），并附带该会话的审阅信息。"""
    with _session() as s:
        sessions = s.scalars(
            select(FollowupSession)
            .order_by(FollowupSession.patient_id, FollowupSession.created_at.desc())
        ).all()
        reviews = s.scalars(select(DoctorReview)).all()
    rev_by_session = {}
    for r in reviews:
        if r.session_id is None:
            continue
        if r.session_id not in rev_by_session:
            rev_by_session[r.session_id] = r
    if dispatch_id:
        sessions = [
            sess for sess in sessions
            if _review_dispatch_id(rev_by_session.get(sess.session_id)) == dispatch_id
        ]
    latest = {}
    for sess in sessions:
        if sess.patient_id in latest:
            continue
        latest[sess.patient_id] = sess
    result = []
    for pid, sess in latest.items():
        if today:
            sd = sess.created_at.strftime("%Y-%m-%d") if sess.created_at else None
            if sd != today:
                continue
        d = _session_to_dict(sess)
        rev = rev_by_session.get(sess.session_id)
        d["risk_result"] = _review_risk_result(rev, d.get("agent_summary"))
        if rev:
            d["review_id"] = rev.review_id
            d["doctor_score"] = rev.score
            d["doctor_comment"] = rev.comment
            d["track_status"] = rev.track_status
            d["review_time"] = rev.reviewed_at.isoformat() if rev.reviewed_at else None
            d["ai_review"] = rev.ai_review
        else:
            d["review_id"] = None
            d["doctor_score"] = None
            d["doctor_comment"] = None
            d["track_status"] = None
            d["review_time"] = None
            d["ai_review"] = None
        result.append(d)
    return result


def review_stats(today=None, dispatch_id=None):
    """统计基于每个患者最新一次随访会话的审阅。"""
    with _session() as s:
        sessions = s.scalars(select(FollowupSession)).all()
        reviews = s.scalars(select(DoctorReview)).all()
    latest_sids = set()
    seen = set()
    for sess in sessions:
        if sess.patient_id in seen:
            continue
        seen.add(sess.patient_id)
        latest_sids.add(sess.session_id)
    latest_reviews = [r for r in reviews if r.session_id in latest_sids]
    if dispatch_id:
        latest_reviews = [
            r for r in latest_reviews
            if _review_dispatch_id(r) == dispatch_id
        ]
    if today:
        latest_reviews = [
            r for r in latest_reviews
            if r.reviewed_at and r.reviewed_at.strftime("%Y-%m-%d") == today
        ]
    total = len(latest_reviews)
    pending = sum(1 for r in latest_reviews if r.track_status == "pending_track")
    tracking = sum(1 for r in latest_reviews if r.track_status == "tracking")
    resolved = sum(1 for r in latest_reviews if r.track_status == "resolved")
    need_revisit = sum(1 for r in latest_reviews if r.track_status == "need_revisit")
    scores = [r.score for r in latest_reviews if r.score is not None]
    avg_score = round(sum(scores) / len(scores), 2) if scores else None
    return {
        "total": total,
        "pending_track": pending,
        "tracking": tracking,
        "resolved": resolved,
        "need_revisit": need_revisit,
        "avg_score": avg_score,
    }


__all__ = [
    "create_plan", "get_plan", "get_latest_plan", "list_plans_by_doctor",
    "list_all_latest_plans", "approve_plan", "modify_plan", "batch_approve_plans",
    "create_session", "get_today_session", "update_session", "get_session",
    "get_review_ai", "list_sessions_by_doctor", "get_latest_transcripts",
    "create_review", "get_review_by_session", "attach_ai_review",
    "update_review_audit_snapshot",
    "list_reviews", "get_latest_sessions", "review_stats",
]
