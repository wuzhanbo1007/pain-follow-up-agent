# -*- coding: utf-8 -*-
"""
PainSmart 9.0 — Faker + PolyFactory → MySQL 播种器

分层生成策略（参照 virtual-hospital-agents 的设计）：
- 底层基础信息（人口学 / 枚举 / 分数 / 日期 / ID）：由声明式工厂用「枚举 + 随机选」确定，
  确定性、可复现（固定种子）。
- 上层「基础病例叙述」（主诉 / 现病史 / 既往史 / 诊断详述 / 治疗小结 / 出院小结 /
  出院医嘱 / 疼痛部位）：交给大模型生成（data/clinical_llm.py），LLM 不可用或出错时
  自动降级到模板生成器，保证离线也能播种。

⚠️ 职责边界（与运行时 Agent 的分工，2026-07-30 确认）：
- 本播种器【只】生成「基础病例数据」：科室 / 医生 / 人设 / 患者 / 入出院记录 /
  疼痛日记 / 用药记录 / 患者渠道 / HIS 同步日志。
- 随访计划（followup_plans）、随访对话（followup_records）、风险评估
  （risk_assessments）及其下游（session / review / alert / handoff / emotion /
  multimodal / call / guardrail）全部由运行时 A/B/C Agent 在前端触发后生成并写入
  数据库，播种器不负责、也不预置空行——避免与运行时 Agent 重复造数。

用法：
    python -m data.seed_mysql            # 默认 50 名患者
    python -m data.seed_mysql --patients 500
依赖：sqlalchemy / polyfactory / faker / pymysql / python-dotenv / openai
"""
import argparse
import asyncio
import random
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data.constants import (
    DIAGNOSES, DIAGNOSIS_TO_PAIN_TYPE, DIAGNOSIS_TO_SURGERY, PAIN_LOCATION,
    DEPARTMENTS, SEED_RANDOM,
)
from data.db_config import DATABASE_URL
from data.models_mysql import (
    Base, Department, Doctor, Patient, Admission, DischargeRecord, PainDiary,
    MedicationRecord, PatientChannel, PhysicianPersona, HISSyncLog,
)
from data.factories_mysql import (
    DepartmentFactory, DoctorFactory, PatientFactory, AdmissionFactory,
    DischargeRecordFactory, PainDiaryFactory, MedicationRecordFactory,
    PatientChannelFactory, PhysicianPersonaFactory, HISSyncLogFactory,
    gen_discharge_medication,
)
# 上层「基础病例叙述」（LLM 生成 + 模板降级）
from data.clinical_llm import generate_patient_clinical

# 仅用于构造 LLM prompt 的「职业」人设（不入库，模拟底层枚举+随机）
PROFESSIONS = ["退休教师", "公司职员", "个体经营者", "家庭主妇", "程序员", "农民", "退休工人"]
PAIN_DURATIONS = ["3个月", "半年", "1年", "2年"]


def _build_profile(pat: Patient, diag: str, pain_type: str, surgery: str,
                   meds: list, pain_location: str, pain_duration: str, nrs: int) -> dict:
    """汇总一份「底层基础信息」profile，交给 LLM 生成基础病例叙述。"""
    age = date(2026, 7, 15).year - pat.birth_date.year  # 以 DEMO_TODAY 年份为基准
    meds_str = "、".join(f"{m['drug_name']} {m['dosage']} {m['frequency']}" for m in meds)
    return {
        "name": pat.name,
        "gender": pat.gender,
        "age": age,
        "profession": random.choice(PROFESSIONS),
        "diagnosis": diag,
        "pain_type": pain_type,
        "surgery": surgery,
        "meds": meds_str,
        "pain_location": pain_location,
        "pain_duration": pain_duration,
        "nrs": nrs,
    }


async def seed(session, n_patients: int = 50):
    """在给定 session 内生成并写入「基础病例数据」（不含运行时随访生命周期）。

    调用方负责 commit。
    """
    random.seed(SEED_RANDOM)

    # ---------- 1) departments ----------
    for _ in range(len(DEPARTMENTS)):
        session.add(DepartmentFactory.build())
    session.flush()
    dept_ids = [d.department_id for d in session.query(Department).all()]

    # ---------- 2) doctors ----------
    doctors = [DoctorFactory.build(department_id=random.choice(dept_ids)) for _ in range(20)]
    session.add_all(doctors)
    session.flush()
    doctor_ids = [d.doctor_id for d in doctors]

    # ---------- 3) physician_personas ----------
    personas = [PhysicianPersonaFactory.build(doctor_id=did) for did in doctor_ids]
    session.add_all(personas)
    session.flush()

    # ---------- 4) patients（底层基础信息，枚举+随机）----------
    patients = [PatientFactory.build() for _ in range(n_patients)]
    session.add_all(patients)
    session.flush()

    # ---------- 5) 构造每个患者的「底层基础上下文」并并发生成基础病例叙述 ----------
    contexts = []
    for pat in patients:
        pid = pat.patient_id
        did = random.choice(doctor_ids)
        dept = random.choice(dept_ids)

        diag = random.choice(DIAGNOSES)
        pain_type = DIAGNOSIS_TO_PAIN_TYPE[diag]
        surgery = DIAGNOSIS_TO_SURGERY[diag]
        meds = gen_discharge_medication(diag)
        pain_location = PAIN_LOCATION.get(diag, "术区")
        pain_duration = random.choice(PAIN_DURATIONS)
        nrs = random.randint(2, 8)

        profile = _build_profile(pat, diag, pain_type, surgery, meds,
                                 pain_location, pain_duration, nrs)
        contexts.append({
            "pat": pat, "pid": pid, "did": did, "dept": dept,
            "diag": diag, "pain_type": pain_type, "surgery": surgery,
            "meds": meds, "pain_location": pain_location,
            "pain_duration": pain_duration, "nrs": nrs,
            "profile": profile,
        })

    # 限流并发调用 LLM 生成全部患者的基础病例叙述。
    # 远端 LLM 为纯推理模型，单次耗时 60~100s+；无限制全并发会导致请求排队超时
    # （LLM_TIMEOUT=120s）→ 全部降级模板。用信号量限流为 3 并发，逐个稳定完成。
    _SEM = asyncio.Semaphore(3)

    async def _limited(ctx):
        async with _SEM:
            return await generate_patient_clinical(ctx["profile"], 0)

    clinicals = await asyncio.gather(
        *(_limited(c) for c in contexts)
    )

    # ---------- 6) 用基础信息 + 病例叙述按 FK 依赖顺序落库 ----------
    stats = {"departments": len(dept_ids), "doctors": len(doctor_ids),
             "personas": len(personas), "patients": n_patients}
    child_counts: dict = {}

    for idx, (ctx, clinical) in enumerate(zip(contexts, clinicals)):
        pat = ctx["pat"]
        pid, did, dept = ctx["pid"], ctx["did"], ctx["dept"]
        diag, pain_type, surgery = ctx["diag"], ctx["pain_type"], ctx["surgery"]
        meds, pain_location, nrs = ctx["meds"], ctx["pain_location"], ctx["nrs"]
        case = clinical["case"]

        # admission（主诉/现病史/既往史来自 LLM 基础病例叙述）
        adm = AdmissionFactory.build(
            patient_id=pid, doctor_id=did, department_id=dept,
            chief_complaint=case.chief_complaint,
            present_illness=case.present_illness,
            past_history=case.past_history,
        )
        session.add(adm)
        session.flush()
        aid = adm.admission_id

        discharge_dt = adm.admission_date + timedelta(days=random.randint(3, 30))

        # discharge（出院小结组来自 LLM 基础病例叙述）
        dis = DischargeRecordFactory.build(
            patient_id=pid, admission_id=aid, doctor_id=did,
            discharge_date=discharge_dt, discharge_diagnosis=diag, pain_type=pain_type,
            surgery_name=surgery, discharge_medication=meds,
            nrs_at_discharge=nrs, pain_duration=ctx["pain_duration"],
            diagnosis_detail=case.diagnosis_detail,
            treatment_summary=case.treatment_summary,
            discharge_summary=case.discharge_summary,
            discharge_instructions=case.discharge_instructions,
            pain_location=case.pain_location,
        )
        session.add(dis)
        session.flush()
        dis_id = dis.discharge_id

        # —— 随访生命周期（plan/session/record/risk/review/alert/handoff/emotion/
        #    multimodal/call/guardrail）在此处有意跳过：由运行时 A/B/C Agent 生成。 ——

        # pain_diaries（每患者 2~5 条，patient 级，不依赖随访会话）
        n_diary = random.randint(2, 5)
        for _ in range(n_diary):
            session.add(PainDiaryFactory.build(patient_id=pid))
        child_counts["pain_diaries"] = child_counts.get("pain_diaries", 0) + n_diary

        # medication_records（每患者 1~3 条，关联出院带药，不依赖随访会话）
        for m in meds[:random.randint(1, min(3, len(meds)))]:
            session.add(MedicationRecordFactory.build(
                patient_id=pid, discharge_id=dis_id,
                drug_name=m["drug_name"], dosage=m["dosage"], frequency=m["frequency"],
            ))

        # patient_channels（每患者 1~3 个渠道，patient 级）
        n_ch = random.randint(1, 3)
        for c in range(n_ch):
            session.add(PatientChannelFactory.build(
                patient_id=pid, channel_type=random.choice(["wechat", "work_wechat", "phone", "app", "h5"]),
                is_preferred=(c == 0),
            ))

        # his_sync_logs（每患者 1~2 条，patient 级）
        for _ in range(random.randint(1, 2)):
            session.add(HISSyncLogFactory.build(patient_id=pid))

        # 每 25 名患者提交一次，避免单事务过大
        if (idx + 1) % 25 == 0:
            session.commit()

    # 统计已播种子表数量
    stats["admissions"] = session.query(Admission).count()
    stats["discharge_records"] = session.query(DischargeRecord).count()
    stats["pain_diaries"] = session.query(PainDiary).count()
    stats["medication_records"] = session.query(MedicationRecord).count()
    stats["patient_channels"] = session.query(PatientChannel).count()
    stats["his_sync_logs"] = session.query(HISSyncLog).count()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Faker+PolyFactory+LLM 生成基础病例数据写入 MySQL")
    parser.add_argument("--patients", type=int, default=50, help="生成患者数量")
    parser.add_argument("--reset", action="store_true", help="先清空全部表再写入")
    args = parser.parse_args()

    engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
    # 建表（幂等）—— 仍建全部 21 张表，供运行时 Agent 写入随访生命周期
    Base.metadata.create_all(engine)
    if args.reset:
        with engine.begin() as conn:
            for tbl in reversed(Base.metadata.sorted_tables):
                conn.execute(tbl.delete())

    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as s:
        stats = asyncio.run(seed(s, n_patients=args.patients))
        s.commit()

    print("✅ MySQL 基础病例数据播种完成（Faker + PolyFactory 基础信息 / LLM 基础病例叙述）")
    print("   ⚠️ 随访计划 / 随访对话 / 风险评估 由运行时 A/B/C Agent 生成，本脚本不预置。")
    for k, v in stats.items():
        print(f"   {k:22s}: {v}")


if __name__ == "__main__":
    main()
