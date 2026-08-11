# -*- coding: utf-8 -*-
"""
PainSmart 9.0 — Faker + PolyFactory 声明式工厂
按 data/models_mysql.py 的 21 张表自动批量生成中文假数据，
由 seed_mysql.py 控制外键顺序后写入远端 MySQL。

· BaseFactory 注入 Faker('zh_CN') + 固定种子（可复现）。
· 枚举/JSON/临床关联字段用 Use(...) 约束取值，保证数据合理。
· 所有外键列（patient_id/doctor_id/...）在 seeder 中通过 build(kw=...) 覆盖，
  工厂本身不保证外键引用存在。
"""
import random
from datetime import date, datetime, timedelta

from faker import Faker
from polyfactory import Use, PostGenerated
from polyfactory.factories.sqlalchemy_factory import SQLAlchemyFactory

from data.constants import (
    DIAGNOSES, PAIN_TYPES, DIAGNOSIS_TO_PAIN_TYPE, DIAGNOSIS_TO_MEDICATIONS,
    DIAGNOSIS_TO_SURGERY, DEPARTMENTS, DOCTOR_TITLES, STYLE_LABELS, CHANNELS, SEED_RANDOM,
)
from data.models_mysql import (
    Base, Department, Doctor, Patient, Admission, DischargeRecord, FollowupPlan,
    FollowupSession, FollowupRecord, RiskAssessment, DoctorReview, Alert, PainDiary,
    MedicationRecord, PatientChannel, HumanHandoff, EmotionRecord, MultimodalAsset,
    CallRecord, PhysicianPersona, GuardrailEvent, HISSyncLog,
)


# ===== Faker + 固定种子（保证每次生成结果一致）=====
fake = Faker("zh_CN")
Faker.seed(SEED_RANDOM)
random.seed(SEED_RANDOM)


# ===== 枚举取值池 =====
RISK_LEVELS = ["low", "medium", "high", "critical"]
SLEEP = ["好", "一般", "差", "很差"]
MED_TAKEN = ["taken", "not_taken", "partial", "unknown"]
SEVERITY_LIGHT = ["轻", "中", "重"]
MOOD = ["平静", "一般", "焦虑", "抑郁"]
ACTIVITY = ["正常", "受限", "卧床"]
ALERT_LEVEL = ["info", "warning", "critical"]
ALERT_TYPE = [
    "risk_escalation", "no_reply_3days", "nrs_spike", "medication_miss",
    "abnormal_indicator", "emotion_crisis", "guardrail_violation",
    "channel_unreachable", "his_sync_failed", "emotion_persistent_low",
]
CRISIS = ["none", "yellow", "red"]
EVENT_TYPE = [
    "out_of_scope", "diagnosis_request", "prescription_request", "emergency",
    "self_harm", "professional_question", "low_confidence",
]
EVENT_SEVERITY = ["info", "warning", "critical", "emergency"]
ACTION_TAKEN = ["blocked", "warned", "redirected", "escalated", "emergency_protocol"]
CALL_STATUS = ["initiated", "ringing", "answered", "no_answer", "busy", "failed", "completed"]
SYNC_DIR = ["pull", "push"]
SYNC_TYPE = ["diagnosis", "medication", "lab_result", "allergy", "followup_result", "risk_assessment"]
SYNC_STATUS = ["success", "partial", "failed", "pending"]
SESSION_STATUS = ["ongoing", "completed", "aborted"]
ESCALATION = ["none", "pending", "handed_off", "resolved"]
PLAN_STATUS = ["draft", "approved", "modified", "rejected"]
TRACK_STATUS = ["followup_done", "pending_track", "tracking", "resolved", "need_revisit", "transferred"]
TARGET_QUEUE = ["nurse_station", "attending_doctor", "clinical_pharmacist"]
AGENT_ROLE = ["active", "recorder", "standby"]
ASSESSED_BY = ["rule_engine", "llm", "hybrid"]
EXPL_STYLE = ["detailed", "concise", "analogy"]
TERM_LEVEL = ["layman", "mixed", "professional"]
ASSET_TYPE = ["voice", "image", "video"]
CALL_DIR = ["outbound", "inbound"]
TRIGGER_REASON = [
    "professional_question", "low_confidence", "patient_request",
    "guardrail_violation", "emotion_crisis", "emergency",
]
BINDING = ["bound", "unbound", "expired", "revoked"]
PAIN_NATURE = ["刺痛", "钝痛", "灼烧痛", "电击痛"]
FOLLOWUP_NECESSITY = ["必须", "建议", "无需"]


# ===== 中文叙事池（供 TEXT 字段使用）=====
_NARRATIVE = [
    "患者主诉慢性疼痛，影响日常活动与睡眠，已规律用药。",
    "疼痛反复发作，VAS 波动明显，需注意依从性与副作用监测。",
    "建议保持规律作息，按时服药，记录疼痛日记，定期复诊。",
    "疼痛对情绪有负面影响，已进行心理疏导与健康教育。",
    "目前治疗方案总体平稳，继续观察并按需调整剂量。",
    "患者诉疼痛部位固定，与活动相关，休息后部分缓解。",
]
def zh_text() -> str:
    return random.choice(_NARRATIVE)


# ===== JSON 字段生成器 =====
def gen_consent_flags():
    return {
        "voice_consent": fake.boolean(),
        "image_consent": fake.boolean(),
        "emotion_consent": fake.boolean(),
        "his_consent": fake.boolean(),
    }


def gen_plan_json(pain_type: str = "神经病理性疼痛") -> dict:
    return {
        "frequency": random.choice(["每日", "每周1次", "每周3次", "每2周1次"]),
        "duration_days": 90,
        "pain_type": pain_type,
        "recheck_items": ["NRS 疼痛评分", "肝肾功能", "用药依从性评估"],
        "medication_adjustment": "根据 NRS 评分逐步调整镇痛药物剂量",
        "warning_threshold": "NRS≥7 或 出现爆发痛/睡眠持续恶化时及时就医",
        "health_education": ["按时服药，不可自行增减", "每日记录疼痛评分", "出现爆发痛及时联系主管医生"],
        "lifestyle": ["规律作息", "适度活动", "清淡饮食，戒烟限酒"],
        "frequency_source": "来自出院处方",
    }


def gen_discharge_medication(diagnosis: str = "带状疱疹后神经痛") -> list:
    meds = DIAGNOSIS_TO_MEDICATIONS.get(diagnosis, [("普瑞巴林胶囊", "75mg", "bid")])
    return [
        {"drug_name": m[0], "dosage": m[1], "frequency": m[2], "days": random.choice([7, 14, 30, 90])}
        for m in meds
    ]


def gen_emotion_labels() -> list:
    labels = random.sample(["平静", "焦虑", "低落", "绝望", "愤怒"], k=random.randint(1, 3))
    raw = [{"label": l, "score": round(random.uniform(0.2, 0.95), 2)} for l in labels]
    total = sum(x["score"] for x in raw) or 1.0
    for x in raw:
        x["score"] = round(x["score"] / total, 2)
    return raw


def gen_transcript() -> list:
    now = datetime.now()
    return [
        {"role": "system", "content": "随访开场，确认患者身份。", "timestamp": (now - timedelta(minutes=10)).isoformat()},
        {"role": "assistant", "content": "您好，今天疼痛情况怎么样？", "timestamp": (now - timedelta(minutes=9)).isoformat()},
        {"role": "user", "content": random.choice(["今天好多了，基本不疼了", "还是有点疼，大概5分", "疼得厉害，昨晚没睡好"]),
         "timestamp": (now - timedelta(minutes=8)).isoformat()},
    ]


def gen_tts_segments() -> list:
    return [
        {"text": "您好，这里是疼痛随访中心。", "audio_url": f"/tts/{random.randint(1000, 9999)}.mp3", "duration": random.randint(2, 6)}
        for _ in range(random.randint(2, 5))
    ]


def gen_context_package() -> dict:
    return {
        "patient_summary": "慢性疼痛随访中",
        "dialog_summary": "患者咨询用药与疼痛控制",
        "steps_done": ["身份确认", "风险评估"],
        "pending_questions": ["是否需要调整剂量"],
    }


def gen_field_mapping() -> list:
    return [{"his_field": "diagnosis_name", "local_field": "discharge_diagnosis", "value": "示例", "deidentified": True}]


def gen_risk_factors() -> list:
    return [{"factor": random.choice(["NRS 升高", "睡眠差", "用药中断"]),
             "weight": round(random.uniform(0.1, 0.4), 2), "detail": zh_text()}]


def gen_confidence() -> dict:
    return {"nrs": random.choice(["high", "medium", "low"]),
            "sleep": random.choice(["high", "medium", "low"]),
            "medication": random.choice(["high", "medium", "low"])}


def gen_dominant_emotion(labels: list) -> str:
    if not labels:
        return "平静"
    return max(labels, key=lambda x: x["score"])["label"]


# ======================================================================
# 工厂基类
# ======================================================================
class BaseFactory(SQLAlchemyFactory):
    """所有工厂继承此类，统一使用中文 faker + 固定种子。"""
    faker = fake
    __is_base_factory__ = True

    def __init_subclass__(cls, **kwargs):
        """子类创建时，把自增主键字段设为 None，交给数据库 AUTO_INCREMENT 生成。

        背景：polyfactory 默认会给所有列（含自增主键）生成随机值，固定种子下
        大量生成会主键碰撞（Duplicate entry for key 'PRIMARY'）。
        此处读取 SQLAlchemy 模型的 primary_key + autoincrement 列，注入 None，
        插入时由 MySQL 自增，彻底消除碰撞。
        """
        super().__init_subclass__(**kwargs)
        model = cls.__dict__.get("__model__")
        if model is None:
            return
        try:
            for col in model.__table__.columns:
                if col.primary_key and col.autoincrement:
                    setattr(cls, col.name, None)
        except Exception:
            pass


# ======================================================================
# 各表工厂
# ======================================================================
class DepartmentFactory(BaseFactory):
    __model__ = Department
    name = Use(lambda: fake.random_element(DEPARTMENTS))
    description = Use(zh_text)


class DoctorFactory(BaseFactory):
    __model__ = Doctor
    name = Use(lambda: fake.name())
    title = Use(lambda: fake.random_element(DOCTOR_TITLES))
    specialty = Use(lambda: fake.random_element(["慢性疼痛", "神经阻滞", "癌痛管理", "脊柱微创", "关节置换"]))
    phone = Use(lambda: fake.phone_number())
    email = Use(lambda: fake.email())
    is_active = Use(lambda: True)
    voice_print_status = Use(lambda: fake.random_element(["none", "cloned", "disabled"]))


class PatientFactory(BaseFactory):
    __model__ = Patient
    # 一致性修复（参照参考项目 generate_pat_base_info 的 enforce_logic_consistency）：
    # 姓名必须跟随性别，杜绝「女性姓名 + 男」这类字段互相矛盾的组合。
    # polyfactory 3.3.0 没有 __post_build__ 钩子，改用 PostGenerated——
    # 它在所有普通字段生成后执行，可从已生成的 result["gender"] 推导 name。
    gender = Use(lambda: fake.random_element(["男", "女"]))
    name = PostGenerated(
        lambda field_name, result: (
            fake.name_male() if result.get("gender") == "男" else fake.name_female()
        )
    )
    birth_date = Use(lambda: fake.date_of_birth(minimum_age=25, maximum_age=85))
    phone = Use(lambda: fake.phone_number())
    address = Use(lambda: fake.address())
    emergency_contact = Use(lambda: fake.name())
    emergency_phone = Use(lambda: fake.phone_number())
    preferred_channel = Use(lambda: fake.random_element(CHANNELS))
    wechat_binding_status = Use(lambda: fake.random_element(["bound", "unbound", "expired"]))
    phone_callable = Use(lambda: fake.boolean(chance_of_getting_true=85))
    emergency_contact_role = Use(lambda: fake.random_element(["配偶", "子女", "父母"]))
    consent_flags = Use(gen_consent_flags)
    # 连续未回复天数：正常 0-3 天（polyfactory 默认会生成随机大整数，
    # 导致判定引擎 C0 规则误判数千天未回复 → 全员电话回访）
    consecutive_no_reply_days = Use(lambda: fake.random_int(min=0, max=3))
    daily_status = Use(lambda: fake.random_element(["normal", "normal", "normal", "on_leave"]))


class AdmissionFactory(BaseFactory):
    __model__ = Admission
    admission_number = Use(lambda: fake.bothify(text="ZY######"))
    admission_date = Use(lambda: fake.date_between(start_date="-120d", end_date="-30d"))
    chief_complaint = Use(zh_text)
    present_illness = Use(zh_text)
    past_history = Use(zh_text)
    admission_diagnosis = Use(lambda: fake.random_element(DIAGNOSES))


class DischargeRecordFactory(BaseFactory):
    __model__ = DischargeRecord
    discharge_date = Use(lambda: fake.date_between(start_date="-90d", end_date="today"))
    pain_location = Use(zh_text)
    nrs_at_discharge = Use(lambda: fake.random_int(min=2, max=8))
    pain_duration = Use(lambda: fake.random_element(["3个月", "半年", "1年", "2年"]))
    discharge_medication = Use(lambda: gen_discharge_medication())
    diagnosis_detail = Use(zh_text)
    treatment_summary = Use(zh_text)
    discharge_summary = Use(zh_text)
    discharge_instructions = Use(zh_text)
    follow_up_necessity = Use(lambda: fake.random_element(FOLLOWUP_NECESSITY))


class FollowupPlanFactory(BaseFactory):
    __model__ = FollowupPlan
    plan_json = Use(lambda: gen_plan_json())
    rag_query_text = Use(zh_text)
    status = Use(lambda: fake.random_element(PLAN_STATUS))
    created_by = Use(lambda: fake.random_element(["AI", "doctor"]))


class FollowupSessionFactory(BaseFactory):
    __model__ = FollowupSession
    session_status = Use(lambda: fake.random_element(SESSION_STATUS))
    transcript_json = Use(gen_transcript)
    agent_summary = Use(zh_text)
    channel = Use(lambda: fake.random_element(CHANNELS))
    voice_mode = Use(lambda: fake.boolean(chance_of_getting_true=30))
    escalation_status = Use(lambda: fake.random_element(ESCALATION))


class FollowupRecordFactory(BaseFactory):
    __model__ = FollowupRecord
    followup_date = Use(lambda: fake.date_between(start_date="-60d", end_date="today"))
    followup_round = Use(lambda: fake.random_int(min=1, max=8))
    message_sent = Use(zh_text)
    reply_text = Use(zh_text)
    nrs_score = Use(lambda: fake.random_int(min=0, max=10))
    sleep_quality = Use(lambda: fake.random_element(SLEEP))
    medication_taken = Use(lambda: fake.random_element(MED_TAKEN))
    side_effects = Use(zh_text)
    side_effects_severity = Use(lambda: fake.random_element(SEVERITY_LIGHT))
    functional_impact = Use(zh_text)
    confidence = Use(gen_confidence)
    follow_up_needed = Use(lambda: random.choice([[], ["sleep_quality"], ["medication_taken", "nrs_score"]]))
    mood_status = Use(lambda: fake.random_element(MOOD))
    activity_level = Use(lambda: fake.random_element(ACTIVITY))
    additional_notes = Use(zh_text)
    reply_parsed = Use(lambda: {"nrs_score": fake.random_int(0, 10), "sleep_quality": fake.random_element(SLEEP)})
    voice_url = Use(lambda: f"/voice/{fake.random_int(10000, 99999)}.wav")
    image_asset_ids = Use(lambda: random.choice([[], [fake.random_int(1, 999)]]))


class RiskAssessmentFactory(BaseFactory):
    __model__ = RiskAssessment
    risk_level = Use(lambda: fake.random_element(RISK_LEVELS))
    risk_score = Use(lambda: round(random.uniform(0.0, 10.0), 1))
    risk_factors = Use(gen_risk_factors)
    recommendation = Use(zh_text)
    assessed_by = Use(lambda: fake.random_element(ASSESSED_BY))


class DoctorReviewFactory(BaseFactory):
    __model__ = DoctorReview
    score = Use(lambda: fake.random_int(min=1, max=5))
    comment = Use(zh_text)
    track_status = Use(lambda: fake.random_element(TRACK_STATUS))
    ai_review = Use(lambda: {"summary": zh_text(), "suggest": fake.random_element(["维持", "调整剂量", "加强随访"])})


class AlertFactory(BaseFactory):
    __model__ = Alert
    alert_level = Use(lambda: fake.random_element(ALERT_LEVEL))
    alert_type = Use(lambda: fake.random_element(ALERT_TYPE))
    alert_reason = Use(zh_text)
    is_read = Use(lambda: fake.boolean(chance_of_getting_true=40))
    is_resolved = Use(lambda: fake.boolean(chance_of_getting_true=50))


class PainDiaryFactory(BaseFactory):
    __model__ = PainDiary
    record_date = Use(lambda: fake.date_between(start_date="-30d", end_date="today"))
    nrs_score = Use(lambda: fake.random_int(min=0, max=10))
    pain_location = Use(zh_text)
    pain_nature = Use(lambda: fake.random_element(PAIN_NATURE))
    triggers = Use(zh_text)
    relief_factors = Use(zh_text)
    notes = Use(zh_text)


class MedicationRecordFactory(BaseFactory):
    __model__ = MedicationRecord
    drug_name = Use(lambda: fake.random_element(["普瑞巴林胶囊", "塞来昔布胶囊", "加巴喷丁胶囊", "吗啡缓释片", "甲钴胺片"]))
    dosage = Use(lambda: fake.random_element(["75mg", "200mg", "300mg", "30mg"]))
    frequency = Use(lambda: fake.random_element(["bid", "tid", "qd", "qn"]))
    start_date = Use(lambda: fake.date_between(start_date="-90d", end_date="-10d"))
    adherence_note = Use(zh_text)


class PatientChannelFactory(BaseFactory):
    __model__ = PatientChannel
    channel_type = Use(lambda: fake.random_element(CHANNELS))
    external_id = Use(lambda: fake.bothify(text="EXT######"))
    binding_status = Use(lambda: fake.random_element(BINDING))
    is_preferred = Use(lambda: fake.boolean(chance_of_getting_true=30))
    last_active_at = Use(lambda: fake.date_time_between(start_date="-30d", end_date="now"))


class HumanHandoffFactory(BaseFactory):
    __model__ = HumanHandoff
    trigger_reason = Use(lambda: fake.random_element(TRIGGER_REASON))
    trigger_confidence = Use(lambda: round(random.uniform(0.0, 1.0), 2))
    intent_label = Use(zh_text)
    context_package = Use(gen_context_package)
    target_queue = Use(lambda: fake.random_element(TARGET_QUEUE))
    status = Use(lambda: fake.random_element(["pending", "accepted", "in_progress", "resolved", "rejected"]))
    resolution_note = Use(zh_text)
    agent_role = Use(lambda: fake.random_element(AGENT_ROLE))


class EmotionRecordFactory(BaseFactory):
    __model__ = EmotionRecord
    emotion_labels = Use(gen_emotion_labels)
    dominant_emotion = Use(lambda: gen_dominant_emotion(gen_emotion_labels()))
    crisis_level = Use(lambda: fake.random_element(CRISIS))
    text_features = Use(lambda: {"sentiment": round(random.uniform(-1, 1), 2)})
    voice_features = Use(lambda: {"pitch": round(random.uniform(80, 300), 1)})
    intervention = Use(zh_text)
    assessed_by = Use(lambda: fake.random_element(ASSESSED_BY))


class MultimodalAssetFactory(BaseFactory):
    __model__ = MultimodalAsset
    asset_type = Use(lambda: fake.random_element(ASSET_TYPE))
    file_path = Use(lambda: f"/data/assets/{fake.random_int(1000, 9999)}/{fake.uuid4()}.dat")
    file_url = Use(lambda: f"https://cdn.example.com/a/{fake.random_int(1000, 9999)}")
    duration_sec = Use(lambda: fake.random_int(min=5, max=300))
    mime_type = Use(lambda: fake.random_element(["audio/wav", "image/jpeg", "video/mp4"]))
    file_size = Use(lambda: fake.random_int(min=1024, max=10_000_000))
    asr_text = Use(zh_text)
    asr_confidence = Use(lambda: round(random.uniform(0.75, 0.99), 2))
    vlm_description = Use(zh_text)
    vlm_analysis = Use(lambda: {"wound_size": f"{fake.random_int(1, 5)}cm", "redness": fake.random_element(["轻", "中", "重"])})
    is_deidentified = Use(lambda: fake.boolean(chance_of_getting_true=90))
    consent_given = Use(lambda: fake.boolean(chance_of_getting_true=80))


class CallRecordFactory(BaseFactory):
    __model__ = CallRecord
    phone_number = Use(lambda: fake.phone_number())
    call_direction = Use(lambda: fake.random_element(CALL_DIR))
    call_status = Use(lambda: fake.random_element(CALL_STATUS))
    start_time = Use(lambda: fake.date_time_between(start_date="-30d", end_date="now"))
    duration_sec = Use(lambda: fake.random_int(min=30, max=300))
    tts_segments = Use(gen_tts_segments)
    asr_full_text = Use(zh_text)
    dtmf_input = Use(lambda: fake.random_element(["", "", "1", "2", "3"]))
    retry_count = Use(lambda: fake.random_int(min=0, max=2))
    fallback_action = Use(lambda: fake.random_element(["none", "sms", "family_notify", "handoff"]))


class PhysicianPersonaFactory(BaseFactory):
    __model__ = PhysicianPersona
    style_label = Use(lambda: fake.random_element(STYLE_LABELS))
    address_habit = Use(lambda: fake.random_element(["阿姨", "叔叔", "您", "老王", "小李"]))
    explanation_style = Use(lambda: fake.random_element(EXPL_STYLE))
    terminology_level = Use(lambda: fake.random_element(TERM_LEVEL))
    sample_phrases = Use(lambda: [zh_text() for _ in range(random.randint(3, 5))])
    tone_intensity = Use(lambda: fake.random_int(min=1, max=10))
    voice_consent = Use(lambda: fake.boolean(chance_of_getting_true=20))
    is_active = Use(lambda: True)


class GuardrailEventFactory(BaseFactory):
    __model__ = GuardrailEvent
    event_type = Use(lambda: fake.random_element(EVENT_TYPE))
    severity = Use(lambda: fake.random_element(EVENT_SEVERITY))
    trigger_text = Use(zh_text)
    rule_matched = Use(lambda: fake.random_element(["out_of_scope_rule", "rx_request_rule", "dx_request_rule"]))
    llm_moderation = Use(zh_text)
    action_taken = Use(lambda: fake.random_element(ACTION_TAKEN))
    emergency_response = Use(zh_text)


class HISSyncLogFactory(BaseFactory):
    __model__ = HISSyncLog
    sync_direction = Use(lambda: fake.random_element(SYNC_DIR))
    sync_type = Use(lambda: fake.random_element(SYNC_TYPE))
    sync_status = Use(lambda: fake.random_element(SYNC_STATUS))
    his_record_id = Use(lambda: fake.bothify(text="HIS######"))
    local_record_id = Use(lambda: fake.bothify(text="local:######"))
    field_mapping = Use(gen_field_mapping)
    error_message = Use(zh_text)


__all__ = ["BaseFactory", "DepartmentFactory", "DoctorFactory", "PatientFactory", "AdmissionFactory",
           "DischargeRecordFactory", "FollowupPlanFactory", "FollowupSessionFactory",
           "FollowupRecordFactory", "RiskAssessmentFactory", "DoctorReviewFactory", "AlertFactory",
           "PainDiaryFactory", "MedicationRecordFactory", "PatientChannelFactory",
           "HumanHandoffFactory", "EmotionRecordFactory", "MultimodalAssetFactory",
           "CallRecordFactory", "PhysicianPersonaFactory", "GuardrailEventFactory", "HISSyncLogFactory"]
