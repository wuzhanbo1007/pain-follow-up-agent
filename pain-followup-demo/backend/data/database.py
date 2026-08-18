# backend/data/database.py
# -*- coding: utf-8 -*-
"""
PainSmart 9.0 — MySQL 数据访问层（Faker + PolyFactory 生成假数据，存入远端 MySQL）
替代原 SQLite 方案：init_db() 建全部声明式 ORM 表并用声明式工厂播种；PatientDB 提供运行时查询。

依赖：sqlalchemy / polyfactory / faker / pymysql / python-dotenv
远端库配置见 data/db_config.py（backend/.env）。
"""
import os
import sys
from datetime import date, datetime

# 保证 backend 目录在 sys.path，便于 `from data.xxx` 导入
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from data.db_config import DATABASE_URL
from data.models_mysql import (
    Base, Patient, Doctor, Department, DischargeRecord, FollowupPlan, FollowupSession,
    FollowupRecord,
)
from data.seed_mysql import seed as _seed_mysql_data


# ======================================================================
# 引擎 / Session 单例
# ======================================================================
_engine = None
_SessionLocal = None


def _get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
        _SessionLocal = sessionmaker(bind=_engine, future=True)
    return _engine, _SessionLocal


# ======================================================================
# 数据库初始化 + 播种
# ======================================================================
def init_db(force=False):
    """初始化运行时数据库。

    远程 MySQL 的表结构由迁移/播种流程维护。运行时默认不执行
    ``Base.metadata.create_all``，避免应用每次启动都对整套表申请 metadata
    lock。首次建库或明确需要补表时，设置 ``DB_AUTO_CREATE_TABLES=1``，或
    显式调用 ``init_db(force=True)``。
    """
    engine, SessionLocal = _get_engine()
    auto_create = os.getenv("DB_AUTO_CREATE_TABLES", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if force or auto_create:
        Base.metadata.create_all(engine)

    with SessionLocal() as s:
        cnt = s.scalar(select(func.count()).select_from(Patient))
        if cnt == 0 or force:
            _seed_mysql_data(s, n_patients=50)
            s.commit()
    return DATABASE_URL


# ======================================================================
# 患者字典构建（将 21 表数据映射为原 demo 合同字段）
# ======================================================================
def _patient_to_dict(pat, session):
    d = {
        "patient_id": pat.patient_id,
        "name": pat.name,
        "gender": pat.gender,
        "age": (date.today() - pat.birth_date).days // 365 if pat.birth_date else None,
        "diagnosis": None,
        "discharge_date": None,
        "doctor_id": None,
        "doctor_name": None,
        "doctor_title": None,
        "department_name": None,
        "hospital_name": None,   # 由主治医生的 hospital_name 解析（存 DB）
        "pain_type": None,
        "follow_up_plan": None,
        "skip_follow_up": bool(pat.skip_follow_up),
        "skip_reason": pat.skip_reason,
        "daily_status": pat.daily_status,
        "consecutive_no_reply_days": pat.consecutive_no_reply_days,
        "emotion_consent": bool(
            (pat.consent_flags or {}).get("emotion_consent", True)
            if isinstance(pat.consent_flags, dict) else True
        ),
    }
    dis = session.scalar(
        select(DischargeRecord)
        .where(DischargeRecord.patient_id == pat.patient_id)
        .order_by(DischargeRecord.discharge_date.desc())
    )
    if dis:
        d["diagnosis"] = dis.discharge_diagnosis
        d["discharge_date"] = dis.discharge_date.isoformat() if dis.discharge_date else None
        d["doctor_id"] = dis.doctor_id
        d["pain_type"] = dis.pain_type
        # 出院叙述类字段：出院小结 / 出院医嘱 / 诊断详述 / 治疗小结
        # 这些由种子脚本经 LLM 生成，A-planner 生成随访计划时需作为上下文使用
        d["discharge_summary"] = dis.discharge_summary
        d["discharge_instructions"] = dis.discharge_instructions
        d["diagnosis_detail"] = dis.diagnosis_detail
        d["treatment_summary"] = dis.treatment_summary
        doc = session.get(Doctor, dis.doctor_id)
        if doc:
            d["doctor_name"] = doc.name
            d["doctor_title"] = doc.title
            d["hospital_name"] = doc.hospital_name or "协和医院"   # 医生所属医院（存 DB）
            if doc.department_id:
                dept = session.get(Department, doc.department_id)
                d["department_name"] = dept.name if dept else None
    plan = session.scalar(
        select(FollowupPlan)
        .where(FollowupPlan.patient_id == pat.patient_id)
        .order_by(FollowupPlan.created_at.desc())
    )
    if plan:
        d["follow_up_plan"] = plan.plan_json
    return d


def _record_to_history(r):
    return {
        "patient_id": r.patient_id,
        "date": r.followup_date.isoformat() if r.followup_date else "",
        "nrs_score": r.nrs_score,
        "sleep_quality": r.sleep_quality,
        "medication_taken": r.medication_taken in ("taken", "partial"),
        "reply_text": r.reply_text,
    }


# ======================================================================
# 数据访问对象
# ======================================================================
class PatientDB:
    """患者数据访问对象（MySQL 后端）"""

    def __init__(self):
        init_db()  # 自动初始化 + 播种

    # -------- 患者 --------
    def get_all_patients(self):
        _, SessionLocal = _get_engine()
        with SessionLocal() as s:
            pats = s.scalars(select(Patient).order_by(Patient.patient_id)).all()
            return [_patient_to_dict(p, s) for p in pats]

    def get_patient(self, patient_id):
        _, SessionLocal = _get_engine()
        with SessionLocal() as s:
            pat = s.get(Patient, patient_id)
            return _patient_to_dict(pat, s) if pat else None

    def get_patients_by_doctor(self, doctor_id):
        _, SessionLocal = _get_engine()
        with SessionLocal() as s:
            pats = s.scalars(select(Patient).where(Patient.patient_id.isnot(None))).all()
            # 通过出院记录过滤主治医生
            result = []
            for p in pats:
                d = _patient_to_dict(p, s)
                if d.get("doctor_id") == doctor_id:
                    result.append(d)
            return result

    def get_send_list(self):
        _, SessionLocal = _get_engine()
        with SessionLocal() as s:
            pats = s.scalars(select(Patient).where(Patient.skip_follow_up == 0)).all()
            return [_patient_to_dict(p, s) for p in pats]

    def get_skip_list(self):
        _, SessionLocal = _get_engine()
        with SessionLocal() as s:
            pats = s.scalars(select(Patient).where(Patient.skip_follow_up == 1)).all()
            return [_patient_to_dict(p, s) for p in pats]

    def update_patient(self, patient_id, **kwargs):
        if not kwargs:
            return
        allowed = {"name", "gender", "skip_follow_up", "skip_reason", "daily_status",
                   "consecutive_no_reply_days", "phone_callable", "preferred_channel"}
        sets = {k: v for k, v in kwargs.items() if k in allowed}
        if not sets:
            return
        _, SessionLocal = _get_engine()
        with SessionLocal() as s:
            pat = s.get(Patient, patient_id)
            if pat:
                for k, v in sets.items():
                    setattr(pat, k, v)
                s.commit()

    # -------- 随访历史（来自 followup_records）--------
    def get_history(self, patient_id, days=7):
        _, SessionLocal = _get_engine()
        with SessionLocal() as s:
            recs = s.scalars(
                select(FollowupRecord)
                .where(FollowupRecord.patient_id == patient_id)
                .order_by(FollowupRecord.followup_date.desc())
            ).all()
            recs = list(recs)[:days]
            recs.reverse()
            return [_record_to_history(r) for r in recs]

    def get_histories_batch(self, patient_ids, days=7):
        if not patient_ids:
            return {}
        _, SessionLocal = _get_engine()
        result = {pid: [] for pid in patient_ids}
        with SessionLocal() as s:
            recs = s.scalars(
                select(FollowupRecord)
                .where(FollowupRecord.patient_id.in_(patient_ids))
                .order_by(FollowupRecord.patient_id, FollowupRecord.followup_date.desc())
            ).all()
            grouped = {}
            for r in recs:
                grouped.setdefault(r.patient_id, []).append(r)
            for pid, rows in grouped.items():
                rows = rows[:days]
                rows.reverse()
                result[pid] = [_record_to_history(r) for r in rows]
        return result

    def add_history(self, patient_id, nrs_score, sleep_quality, medication_taken, reply_text):
        _, SessionLocal = _get_engine()
        with SessionLocal() as s:
            plan = s.scalar(
                select(FollowupPlan)
                .where(FollowupPlan.patient_id == patient_id)
                .order_by(FollowupPlan.created_at.desc())
            )
            sess = s.scalar(
                select(FollowupSession)
                .where(FollowupSession.patient_id == patient_id)
                .order_by(FollowupSession.created_at.desc())
            )
            if sess is None:
                # 预置回复/历史写入场景：补齐 FollowupSession 全部 NOT NULL 无默认列
                sess = FollowupSession(patient_id=patient_id, plan_id=plan.plan_id if plan else None,
                                       transcript_json=[], agent_summary="", channel="app",
                                       completed_at=datetime.now())
                s.add(sess)
                s.flush()
            rec = FollowupRecord(
                patient_id=patient_id,
                plan_id=plan.plan_id if plan else None,
                session_id=sess.session_id,
                followup_date=date.today(),
                followup_round=1,
                message_sent="",
                reply_text=reply_text,
                nrs_score=nrs_score,
                sleep_quality=sleep_quality,
                medication_taken="taken" if medication_taken else "not_taken",
                side_effects="", side_effects_severity="", functional_impact="",
                confidence={}, follow_up_needed=[], mood_status="", activity_level="",
                additional_notes="", reply_parsed={}, voice_url="", image_asset_ids=[],
                guardrail_triggered=False, handoff_id=0,
            )
            s.add(rec)
            s.commit()

    # -------- 预置回复（合成自最新随访记录；无独立 preset_replies 表）--------
    def get_all_replies(self):
        _, SessionLocal = _get_engine()
        out = {}
        with SessionLocal() as s:
            sub = (
                select(FollowupRecord.patient_id, func.max(FollowupRecord.followup_date).label("mx"))
                .group_by(FollowupRecord.patient_id)
            ).subquery()
            latest = s.execute(
                select(FollowupRecord)
                .join(sub, (FollowupRecord.patient_id == sub.c.patient_id) &
                      (FollowupRecord.followup_date == sub.c.mx))
            ).scalars().all()
            for r in latest:
                nrs = r.nrs_score
                risk = "high" if (nrs and nrs >= 7) else ("medium" if (nrs and nrs >= 5) else "low")
                out[r.patient_id] = {
                    "patient_id": r.patient_id,
                    "reply_text": r.reply_text,
                    "risk_tendency": risk,
                    "nrs_expected": nrs,
                    "sleep_expected": r.sleep_quality,
                    "medication_expected": 1 if r.medication_taken in ("taken", "partial") else 0,
                }
        return out

    def get_reply(self, patient_id):
        return self.get_all_replies().get(patient_id)

    def set_reply(self, patient_id, reply_text, risk_tendency="medium_risk", **extra):
        _, SessionLocal = _get_engine()
        with SessionLocal() as s:
            plan = s.scalar(
                select(FollowupPlan)
                .where(FollowupPlan.patient_id == patient_id)
                .order_by(FollowupPlan.created_at.desc())
            )
            sess = s.scalar(
                select(FollowupSession)
                .where(FollowupSession.patient_id == patient_id)
                .order_by(FollowupSession.created_at.desc())
            )
            if sess is None:
                # 预置回复/历史写入场景：补齐 FollowupSession 全部 NOT NULL 无默认列
                sess = FollowupSession(patient_id=patient_id, plan_id=plan.plan_id if plan else None,
                                       transcript_json=[], agent_summary="", channel="app",
                                       completed_at=datetime.now())
                s.add(sess)
                s.flush()
            rec = FollowupRecord(
                patient_id=patient_id,
                plan_id=plan.plan_id if plan else None,
                session_id=sess.session_id,
                followup_date=date.today(),
                followup_round=1,
                message_sent="",
                reply_text=reply_text,
                nrs_score=extra.get("nrs_expected") or 0,
                sleep_quality=extra.get("sleep_expected") or "一般",
                medication_taken="taken" if extra.get("medication_expected") else "not_taken",
                side_effects="", side_effects_severity="", functional_impact="",
                confidence={}, follow_up_needed=[], mood_status="", activity_level="",
                additional_notes="", reply_parsed={}, voice_url="", image_asset_ids=[],
                guardrail_triggered=False, handoff_id=0,
            )
            s.add(rec)
            s.commit()

    # -------- 统计 --------
    def get_stats(self):
        _, SessionLocal = _get_engine()
        with SessionLocal() as s:
            total = s.scalar(select(func.count()).select_from(Patient))
            skip = s.scalar(select(func.count()).select_from(Patient).where(Patient.skip_follow_up == 1))
            # 每位患者最新一次随访记录的 NRS -> 风险分层
            sub = (
                select(FollowupRecord.patient_id, func.max(FollowupRecord.followup_date).label("mx"))
                .group_by(FollowupRecord.patient_id)
            ).subquery()
            latest = s.execute(
                select(FollowupRecord)
                .join(sub, (FollowupRecord.patient_id == sub.c.patient_id) &
                      (FollowupRecord.followup_date == sub.c.mx))
            ).scalars().all()
            high = medium = low = 0
            for r in latest:
                nrs = r.nrs_score
                if nrs is None:
                    low += 1
                elif nrs >= 7:
                    high += 1
                elif nrs >= 5:
                    medium += 1
                else:
                    low += 1
            return {
                "total_patients": total,
                "skip_count": skip,
                "send_count": total - skip,
                "sent_count": 0,
                "replied_count": 0,
                "high_risk_count": high,
                "medium_risk_count": medium,
                "low_risk_count": low,
                "alert_count": 0,
            }

    def close(self):
        pass


# 兼容旧调用：原 DB_PATH 已废弃（SQLite 移除）
DB_PATH = DATABASE_URL


# ======================================================================
# 命令行入口
# ======================================================================
if __name__ == "__main__":
    url = init_db(force=True)
    print(f"MySQL 数据库已初始化并播种: {url}")
    db = PatientDB()
    stats = db.get_stats()
    print(f"患者总数: {stats['total_patients']}")
    print(f"待随访:   {stats['send_count']}")
    print(f"免随访:   {stats['skip_count']}")
    print(f"高风险:   {stats['high_risk_count']}")
    print(f"中风险:   {stats['medium_risk_count']}")
    print(f"低风险:   {stats['low_risk_count']}")
    replies = db.get_all_replies()
    print(f"预置回复: {len(replies)} 条")
