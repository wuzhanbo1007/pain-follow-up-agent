"""
患者 / 随访名单 / 统计 / 日志 / 回复 REST 路由

共享状态（db / PATIENTS / REPLIES / followup_service 等）从 core.runtime 读取，
避免反向 import app（app 以 __main__ 运行时 import app 会二次加载）。

Comet 风格：涉及 LLM 的路由为 async def + await（ChatOpenAI.ainvoke → AsyncOpenAI，
事件循环非阻塞）；纯 DB/内存路由保持同步 def，由 FastAPI 线程池托管。
"""
from datetime import datetime as _dt

from fastapi import APIRouter, Query, HTTPException

from core import runtime
from core.logging_config import get_logger
from engine.followup_scheduler import (
    apply_today_followup_flags, build_today_send_list, get_today, compute_next_followup,
)
from services.daily_scheduler import decide_today_list

log = get_logger("painsmart.patients")

patients_router = APIRouter(tags=["patients"])


@patients_router.get("/api/patients")
def api_get_patients():
    """获取所有患者列表（简版）
    # 返回的患者对象已携带判定引擎回写的 skip_follow_up / skip_reason /
    followup_detail，使前端"应随访/免随访"分组直接由计划规则驱动。
    """
    rows = runtime.db.get_all_patients()
    apply_today_followup_flags(rows)  # 纯计算，无需线程化
    summary = []
    for p in rows:
        summary.append({
            "patient_id": p["patient_id"],
            "name": p["name"],
            "age": p["age"],
            "diagnosis": p["diagnosis"],
            "doctor_name": p.get("doctor_name", ""),
            "skip_follow_up": p.get("skip_follow_up", False),
            "skip_reason": p.get("skip_reason"),
            "followup_detail": p.get("followup_detail"),
            "has_reply": p["patient_id"] in runtime.REPLIES,
        })
    return summary


@patients_router.get("/api/patients/{patient_id}")
def api_get_patient_detail(patient_id: str):
    """获取患者详情 + 历史 + 预置回复"""
    patient = runtime.db.get_patient(patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail={"error": "未找到患者"})
    patient["history"] = runtime.db.get_history(patient_id)
    patient["today_reply"] = runtime.db.get_reply(patient_id)
    return patient


@patients_router.get("/api/patients/{patient_id}/history")
def api_get_patient_history(patient_id: str, days: int = Query(7)):
    """获取患者历史数据"""
    history = runtime.db.get_history(patient_id, days)
    return {"patient_id": patient_id, "history": history}


@patients_router.get("/api/followup/today")
async def api_followup_today(today: str = Query(None)):
    """只读返回当日判定名单（应随访/免随访/逐人判定详情）。
    供前端"随访名单"页展示与调试，全部由判定引擎（方案 A）实时计算。
    """
    try:
        today_str = today or get_today().isoformat()
        today_d = _dt.strptime(today_str, "%Y-%m-%d").date()
    except Exception:
        today_d = None
    # decide_today_list 规则不确定时会走 LLM 二次判定 → await（事件循环非阻塞）
    res = await decide_today_list(runtime.PATIENTS, today_d)

    def _strip(p):
        return {k: v for k, v in p.items() if k != "followup_detail"}

    send_list = []
    for p in res["send_list"]:
        item = _strip(p)
        try:
            item["next_followup_date"] = compute_next_followup(p, today_d)
        except Exception:
            item["next_followup_date"] = None
        send_list.append(item)

    skip_list = []
    for p in res["skip_list"]:
        merged = _strip(p)
        merged.update(p.get("followup_detail") or {})
        skip_list.append(merged)

    return {
        "today": res["today"],
        "total": res["total"],
        "send_count": res["send_count"],
        "skip_count": res["skip_count"],
        "send_list": send_list,
        "skip_list": skip_list,
        "details": res["details"],
    }


@patients_router.get("/api/stats")
def api_get_stats():
    """获取统计：DB 静态数据为底，运行时动态数据覆盖
    # 应随访/免随访人数由判定引擎提供，保证与前端名单一致。
    """
    base = runtime.db.get_stats()
    runtime_stats = runtime.followup_service.stats
    merged = {**base}
    for k, v in runtime_stats.items():
        if v != 0 or k in ("sent_count", "replied_count"):
            merged[k] = v
    # —— 用判定引擎覆盖应随访/免随访口径 ——
    try:
        today_res = build_today_send_list(runtime.PATIENTS)
        merged["total_patients"] = today_res["total"]
        merged["send_count"] = today_res["send_count"]
        merged["skip_count"] = today_res["skip_count"]
        # 低风险按"应随访 - 高 - 中"重算，避免用旧的 skip=0 口径膨胀
        hi = merged.get("high_risk_count", 0) or 0
        mi = merged.get("medium_risk_count", 0) or 0
        merged["low_risk_count"] = max(0, today_res["send_count"] - hi - mi)
    except Exception as e:
        log.warning("/api/stats 引擎覆盖失败（沿用 DB 值）: %s", e)
    return merged


@patients_router.get("/api/logs")
def api_get_logs():
    """纯内存读取，无需线程化"""
    return runtime.followup_service.logs[-50:]


@patients_router.get("/api/replies")
def api_get_replies():
    """
    返回每位患者的预置回复及实际风险评分（由 ReplyParser + RiskEngine 实时计算）
    使用批量加载优化：一次加载所有患者和历史，避免逐个查库
    """
    # 用内存中已有的 PATIENTS 构建患者索引，无需查库
    patient_map = {p["patient_id"]: p for p in runtime.PATIENTS}

    # 批量加载所有回复患者的历史（单次 SQLite 连接）
    reply_pids = list(runtime.REPLIES.keys())
    histories_batch = runtime.db.get_histories_batch(reply_pids, days=7)

    result = {}
    for pid, data in runtime.REPLIES.items():
        reply_text = data.get("reply_text", "")
        patient = patient_map.get(pid)
        history = histories_batch.get(pid, [])

        if not patient:
            result[pid] = {
                "reply_text": reply_text,
                "risk_tendency": data.get("risk_tendency", "medium_risk"),
            }
            continue

        history_ctx = "\n".join([
            f"{h['date']}: NRS={h.get('nrs_score', '?')}, 睡眠={h.get('sleep_quality', '?')}"
            for h in history[-5:]
        ]) if history else ""

        # 用解析器提取结构化数据（批量场景直接走同步关键词解析，不经 LLM）
        parsed = runtime.reply_parser._keyword_parse(reply_text)
        today_data = {
            "nrs": parsed.get("nrs_score"),
            "sleep_quality": parsed.get("sleep_quality"),
            "medication_taken": parsed.get("medication_taken"),
        }

        # 检测模糊回复：NRS/睡眠/用药三项全空 → 标记为 ambiguous
        is_ambiguous = (
            parsed.get("nrs_score") is None
            and parsed.get("sleep_quality") is None
            and parsed.get("medication_taken") is None
        )

        if is_ambiguous:
            result[pid] = {
                "reply_text": reply_text,
                "risk_tendency": "ambiguous",
                "risk_level": "ambiguous",
                "risk_score": None,
                "risk_label": "模糊回复",
                "base_score": None,
                "dynamic_score": None,
            }
            continue

        # 计算实际风险评分（纯计算，无需线程化）
        risk_result = runtime.risk_engine.calculate(pid, patient, today_data, history[-7:])

        result[pid] = {
            "reply_text": reply_text,
            "risk_tendency": risk_result["level"] + "_risk",
            "risk_level": risk_result["level"],
            "risk_score": risk_result["total_score"],
            "risk_label": risk_result["level_label"],
            "base_score": risk_result["base_score"],
            "dynamic_score": risk_result["dynamic_score"],
        }
    return result
