# backend/routes/patients.py
"""
患者 / 随访名单 REST 路由（仅保留前端在用的两个端点）

共享状态统一来自 get_context()（AppContext + PatientRepository），不反向 import app，
避免 app 以 __main__ 运行时二次加载造成状态分裂（旧 core.runtime 机制已删除）。

已删除的死端点：/api/stats、/api/logs、/api/replies、/api/followup/today、
/api/patients/{id}/history（前端 src 与旧 dist 构建均不引用）。

Comet 风格：涉及 LLM 的路由为 async def + await（事件循环非阻塞）；
纯 DB/内存路由保持同步 def，由 FastAPI 线程池托管。
"""
from fastapi import APIRouter, HTTPException

from domain.services.roster_decider import RosterDecider
from infrastructure.runtime_context import get_context

_roster_decider = RosterDecider()


def _apply_flags_legacy(rows):
    """用 RosterDecider 结果回写每位患者的应访/免访标记（复刻旧 apply_flags）。"""
    roster = _roster_decider.decide_base_roster(rows, business_date=None)
    by_id = {p["patient_id"]: p for p in (roster.send_list + roster.skip_list)}
    for p in rows:
        d = by_id.get(p["patient_id"])
        if not d:
            continue
        p["skip_follow_up"] = d.get("skip_follow_up", False)
        p["skip_reason"] = d.get("skip_reason")
        p["followup_detail"] = roster.details.get(p["patient_id"])

patients_router = APIRouter(tags=["patients"])


@patients_router.get("/api/patients")
def api_get_patients():
    """获取所有患者列表（简版）
    # 返回的患者对象已携带判定引擎回写的 skip_follow_up / skip_reason /
    followup_detail，使前端"应随访/免随访"分组直接由计划规则驱动。
    """
    ctx = get_context()
    rows = ctx.patient_repository.get_all_patients()
    # 唯一真源：总调度 Agent（规则引擎 + 配置覆盖 + 手动指派）回写名单标记
    _apply_flags_legacy(rows)  # 用新架构 RosterDecider 回写名单标记
    replies = ctx.patient_repository.get_all_replies()
    summary = []
    for p in rows:
        summary.append({
            "patient_id": p["patient_id"],
            "name": p["name"],
            "age": p["age"],
            "diagnosis": p["diagnosis"],
            "doctor_name": p.get("doctor_name", ""),
            "doctor_title": p.get("doctor_title", ""),
            "department_name": p.get("department_name", ""),
            "hospital_name": p.get("hospital_name", ""),
            "skip_follow_up": p.get("skip_follow_up", False),
            "skip_reason": p.get("skip_reason"),
            "followup_detail": p.get("followup_detail"),
            "has_reply": p["patient_id"] in replies,
        })
    return summary


@patients_router.get("/api/patients/{patient_id}")
def api_get_patient_detail(patient_id: str):
    """获取患者详情 + 历史 + 预置回复"""
    ctx = get_context()
    patient = ctx.patient_repository.get_patient(patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail={"error": "未找到患者"})
    patient["history"] = ctx.patient_repository.get_history(patient_id)
    patient["today_reply"] = ctx.patient_repository.get_reply(patient_id)
    return patient
