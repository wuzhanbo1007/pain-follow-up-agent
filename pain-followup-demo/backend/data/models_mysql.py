# -*- coding: utf-8 -*-
"""
PainSmart 9.0 — MySQL 21 表 SQLAlchemy 2.0 ORM 模型
字段/长度/枚举与《数据库重构方案_MySQL版.md》§4.1–4.21 严格对应。

设计说明：为兼顾「远端 MySQL（目标库）」与「本地 SQLite（冒烟测试）」双运行，
枚举列统一用 String(VARCHAR) 承载（应用层保证取值，便于跨库移植）；
TINYINT(1) 用 Boolean；DECIMAL 用 Numeric；JSON 用 JSON 类型。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String, Integer, Date, DateTime, Text, JSON, Numeric, Boolean, SmallInteger,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


# ===================== 4.1 departments =====================
class Department(Base):
    __tablename__ = "departments"
    department_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.2 doctors =====================
class Doctor(Base):
    __tablename__ = "doctors"
    doctor_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id: Mapped[int] = mapped_column(Integer, nullable=False)
    hospital_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # 所属医院（演示统一协和医院）
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(32))
    specialty: Mapped[str] = mapped_column(String(128))
    phone: Mapped[str] = mapped_column(String(20))
    email: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    persona_id: Mapped[int] = mapped_column(Integer)
    voice_print_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_print_status: Mapped[str] = mapped_column(String(16), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.3 patients =====================
class Patient(Base):
    __tablename__ = "patients"
    patient_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    gender: Mapped[str] = mapped_column(String(4), nullable=False)  # '男'/'女'
    birth_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    phone: Mapped[str] = mapped_column(String(20))
    id_card_hash: Mapped[str] = mapped_column(String(64))
    address: Mapped[str] = mapped_column(String(256))
    emergency_contact: Mapped[str] = mapped_column(String(32))
    emergency_phone: Mapped[str] = mapped_column(String(20))
    preferred_channel: Mapped[str] = mapped_column(String(16))  # wechat/work_wechat/phone/app/h5
    wechat_openid: Mapped[str] = mapped_column(String(64))
    wechat_binding_status: Mapped[str] = mapped_column(String(16), default="unbound")
    phone_callable: Mapped[bool] = mapped_column(Boolean, default=True)
    emergency_contact_role: Mapped[str] = mapped_column(String(32))
    consent_flags: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    # ===== demo 运行时字段（与随访调度契约相关，保留以支持既有调度逻辑）=====
    skip_follow_up: Mapped[int] = mapped_column(Integer, default=0)
    skip_reason: Mapped[str] = mapped_column(Text)
    daily_status: Mapped[str] = mapped_column(String(16), default="normal")
    consecutive_no_reply_days: Mapped[int] = mapped_column(Integer, default=0)


# ===================== 4.4 admissions =====================
class Admission(Base):
    __tablename__ = "admissions"
    admission_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    department_id: Mapped[int] = mapped_column(Integer, nullable=False)
    admission_number: Mapped[str] = mapped_column(String(32))
    admission_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    chief_complaint: Mapped[str] = mapped_column(Text)
    present_illness: Mapped[str] = mapped_column(Text)
    past_history: Mapped[str] = mapped_column(Text)
    admission_diagnosis: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.5 discharge_records（RAG 核心） =====================
class DischargeRecord(Base):
    __tablename__ = "discharge_records"
    discharge_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    admission_id: Mapped[int] = mapped_column(Integer, nullable=False)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    discharge_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    discharge_diagnosis: Mapped[str] = mapped_column(String(256), nullable=False)
    diagnosis_detail: Mapped[str] = mapped_column(Text)
    surgery_name: Mapped[str] = mapped_column(String(256))
    surgery_date: Mapped[datetime] = mapped_column(Date)
    surgery_summary: Mapped[str] = mapped_column(Text)
    pain_type: Mapped[str] = mapped_column(String(32))  # 4 类疼痛机制之一
    pain_location: Mapped[str] = mapped_column(String(128))
    nrs_at_discharge: Mapped[int] = mapped_column(SmallInteger)
    pain_duration: Mapped[str] = mapped_column(String(64))
    discharge_medication: Mapped[list] = mapped_column(JSON)
    treatment_summary: Mapped[str] = mapped_column(Text)
    discharge_summary: Mapped[str] = mapped_column(Text, nullable=False)
    discharge_instructions: Mapped[str] = mapped_column(Text)
    follow_up_necessity: Mapped[str] = mapped_column(String(8), default="必须")  # 必须/建议/无需
    follow_up_recommendation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.6 followup_plans =====================
class FollowupPlan(Base):
    __tablename__ = "followup_plans"
    plan_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    discharge_id: Mapped[int] = mapped_column(Integer, nullable=False)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    rag_query_text: Mapped[Optional[str]] = mapped_column(Text)
    rag_retrieval_context: Mapped[Optional[list]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft/approved/modified/rejected
    created_by: Mapped[str] = mapped_column(String(8), default="AI")  # AI/doctor
    original_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


# ===================== 4.7 followup_sessions =====================
class FollowupSession(Base):
    __tablename__ = "followup_sessions"
    session_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    session_status: Mapped[str] = mapped_column(String(16), default="ongoing")
    transcript_json: Mapped[list] = mapped_column(JSON)
    agent_summary: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(16))  # wechat/work_wechat/phone/app/h5
    voice_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_status: Mapped[str] = mapped_column(String(16), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    completed_at: Mapped[datetime] = mapped_column(DateTime)


# ===================== 4.8 followup_records =====================
class FollowupRecord(Base):
    __tablename__ = "followup_records"
    record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    followup_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    followup_round: Mapped[int] = mapped_column(Integer, default=1)
    message_sent: Mapped[str] = mapped_column(Text)
    reply_text: Mapped[str] = mapped_column(Text)
    nrs_score: Mapped[int] = mapped_column(SmallInteger)
    sleep_quality: Mapped[str] = mapped_column(String(8))  # 好/一般/差/很差
    medication_taken: Mapped[str] = mapped_column(String(16))  # taken/not_taken/partial/unknown
    side_effects: Mapped[str] = mapped_column(Text)
    side_effects_severity: Mapped[str] = mapped_column(String(8))  # 轻/中/重
    functional_impact: Mapped[str] = mapped_column(Text)
    confidence: Mapped[dict] = mapped_column(JSON)
    follow_up_needed: Mapped[list] = mapped_column(JSON)
    mood_status: Mapped[str] = mapped_column(String(32))
    activity_level: Mapped[str] = mapped_column(String(32))
    additional_notes: Mapped[str] = mapped_column(Text)
    reply_parsed: Mapped[dict] = mapped_column(JSON)
    voice_url: Mapped[str] = mapped_column(String(512))
    image_asset_ids: Mapped[list] = mapped_column(JSON)
    guardrail_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    handoff_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.9 risk_assessments =====================
class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    assessment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[int] = mapped_column(Integer)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)  # low/medium/high/critical
    risk_score: Mapped[float] = mapped_column(Numeric(3, 1), nullable=False)
    risk_factors: Mapped[list] = mapped_column(JSON)
    recommendation: Mapped[str] = mapped_column(Text)
    assessed_by: Mapped[str] = mapped_column(String(16), default="rule_engine")
    assessed_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.10 doctor_reviews =====================
class DoctorReview(Base):
    __tablename__ = "doctor_reviews"
    review_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 演示"no-reply-x"等无真实会话时可为空
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    track_status: Mapped[str] = mapped_column(String(16), default="followup_done")
    ai_review: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    audit_snapshot: Mapped[dict] = mapped_column(JSON)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.11 alerts =====================
class Alert(Base):
    __tablename__ = "alerts"
    alert_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[int] = mapped_column(Integer)
    alert_level: Mapped[str] = mapped_column(String(16), nullable=False)  # info/warning/critical
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    alert_reason: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.12 pain_diaries =====================
class PainDiary(Base):
    __tablename__ = "pain_diaries"
    diary_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    record_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    nrs_score: Mapped[int] = mapped_column(SmallInteger)
    pain_location: Mapped[str] = mapped_column(String(128))
    pain_nature: Mapped[str] = mapped_column(String(64))
    triggers: Mapped[str] = mapped_column(Text)
    relief_factors: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.13 medication_records =====================
class MedicationRecord(Base):
    __tablename__ = "medication_records"
    med_record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    discharge_id: Mapped[int] = mapped_column(Integer)
    drug_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dosage: Mapped[str] = mapped_column(String(64))
    frequency: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    end_date: Mapped[datetime] = mapped_column(Date)
    adherence_note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.14 patient_channels（★新增） =====================
class PatientChannel(Base):
    __tablename__ = "patient_channels"
    channel_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False)  # wechat/work_wechat/phone/app/h5
    external_id: Mapped[str] = mapped_column(String(128))
    binding_status: Mapped[str] = mapped_column(String(16), default="unbound")
    is_preferred: Mapped[bool] = mapped_column(Boolean, default=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime)
    channel_metadata: Mapped[dict] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.15 human_handoffs（★新增） =====================
class HumanHandoff(Base):
    __tablename__ = "human_handoffs"
    handoff_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_confidence: Mapped[float] = mapped_column(Numeric(3, 2))
    intent_label: Mapped[str] = mapped_column(String(64))
    context_package: Mapped[dict] = mapped_column(JSON)
    target_queue: Mapped[str] = mapped_column(String(32), default="nurse_station")
    assigned_to: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    resolution_note: Mapped[str] = mapped_column(Text)
    agent_role: Mapped[str] = mapped_column(String(16), default="recorder")
    resumed_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.16 emotion_records（★新增） =====================
class EmotionRecord(Base):
    __tablename__ = "emotion_records"
    emotion_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    record_id: Mapped[int] = mapped_column(Integer)
    emotion_labels: Mapped[list] = mapped_column(JSON, nullable=False)
    dominant_emotion: Mapped[str] = mapped_column(String(32))
    crisis_level: Mapped[str] = mapped_column(String(16), default="none")
    text_features: Mapped[dict] = mapped_column(JSON)
    voice_features: Mapped[dict] = mapped_column(JSON)
    intervention: Mapped[str] = mapped_column(Text)
    assessed_by: Mapped[str] = mapped_column(String(16), default="hybrid")
    assessed_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.17 multimodal_assets（★新增） =====================
class MultimodalAsset(Base):
    __tablename__ = "multimodal_assets"
    asset_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[int] = mapped_column(Integer)
    record_id: Mapped[int] = mapped_column(Integer)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)  # voice/image/video
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_url: Mapped[str] = mapped_column(String(512))
    duration_sec: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(64))
    file_size: Mapped[int] = mapped_column(Integer)
    asr_text: Mapped[str] = mapped_column(Text)
    asr_confidence: Mapped[float] = mapped_column(Numeric(3, 2))
    vlm_analysis: Mapped[dict] = mapped_column(JSON)
    vlm_description: Mapped[str] = mapped_column(Text)
    is_deidentified: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.18 call_records（★新增） =====================
class CallRecord(Base):
    __tablename__ = "call_records"
    call_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[int] = mapped_column(Integer)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    call_direction: Mapped[str] = mapped_column(String(16), default="outbound")
    call_status: Mapped[str] = mapped_column(String(16), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime)
    answer_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    duration_sec: Mapped[int] = mapped_column(Integer)
    tts_segments: Mapped[list] = mapped_column(JSON)
    asr_full_text: Mapped[str] = mapped_column(Text)
    dtmf_input: Mapped[str] = mapped_column(String(32))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    fallback_action: Mapped[str] = mapped_column(String(16), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.19 physician_personas（★新增） =====================
class PhysicianPersona(Base):
    __tablename__ = "physician_personas"
    persona_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doctor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    style_label: Mapped[str] = mapped_column(String(64))
    address_habit: Mapped[str] = mapped_column(String(128))
    explanation_style: Mapped[str] = mapped_column(String(16))  # detailed/concise/analogy
    terminology_level: Mapped[str] = mapped_column(String(16))  # layman/mixed/professional
    sample_phrases: Mapped[list] = mapped_column(JSON)
    tone_intensity: Mapped[int] = mapped_column(SmallInteger, default=5)
    voice_model_url: Mapped[str] = mapped_column(String(512))
    voice_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    voice_consent_at: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.20 guardrail_events（★新增） =====================
class GuardrailEvent(Base):
    __tablename__ = "guardrail_events"
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    record_id: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger_text: Mapped[str] = mapped_column(Text)
    rule_matched: Mapped[str] = mapped_column(String(128))
    llm_moderation: Mapped[str] = mapped_column(Text)
    action_taken: Mapped[str] = mapped_column(String(32), nullable=False)
    emergency_response: Mapped[str] = mapped_column(Text)
    handoff_id: Mapped[int] = mapped_column(Integer)
    resolved_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


# ===================== 4.21 his_sync_logs（★新增） =====================
class HISSyncLog(Base):
    __tablename__ = "his_sync_logs"
    sync_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sync_direction: Mapped[str] = mapped_column(String(16), nullable=False)  # pull/push
    sync_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sync_status: Mapped[str] = mapped_column(String(16), nullable=False)
    his_record_id: Mapped[str] = mapped_column(String(128))
    local_record_id: Mapped[str] = mapped_column(String(128))
    field_mapping: Mapped[list] = mapped_column(JSON)
    error_message: Mapped[str] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


__all__ = [
    "Base", "Department", "Doctor", "Patient", "Admission", "DischargeRecord",
    "FollowupPlan", "FollowupSession", "FollowupRecord", "RiskAssessment",
    "DoctorReview", "Alert", "PainDiary", "MedicationRecord", "PatientChannel",
    "HumanHandoff", "EmotionRecord", "MultimodalAsset", "CallRecord",
    "PhysicianPersona", "GuardrailEvent", "HISSyncLog",
]
