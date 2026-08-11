"""
启动引导（从 app.py 抽离）

集中处理：SQLite 初始化、旧表清理、患者 / 回复加载、随访计划预填充、
当日随访判定、依赖容器组装，并把结果写入 core.runtime 供 routes / ws 读取。
保持原有逻辑与日志不变（纯搬家）。
"""
import time as _time

from core import config
from core.logging_config import configure_logging, get_logger
from core import runtime

log = get_logger("painsmart")


def bootstrap():
    """初始化数据库与运行时共享状态，填充 core.runtime。"""
    from data.database import PatientDB, init_db
    from engine.followup_scheduler import apply_today_followup_flags, build_today_send_list
    from core.container import build_container
    from db.followup_db import create_plan, get_latest_plan

    # ======== 初始化 MySQL 数据库（幂等建 21 张表 + 首次播种）========
    _t0 = _time.time()
    init_db()
    log.info("init_db 完成 (%.1fs)", _time.time() - _t0)
    # 说明：MySQL 为持久化存储，不再每次启动清空随访计划/会话/审阅；
    # 播种逻辑幂等（患者表为空时才写入），与历史随访状态共存。

    db = PatientDB()
    PATIENTS = db.get_all_patients()

    # ======== 预填充随访计划草稿 ========
    _pt0 = _time.time()
    # 演示用：跳过演示患者（沈桂珍 patient_id=15），由 Demo 流程手动推进
    _PREFILL_SKIP_IDS = {15}
    _prefill_count = 0
    for _p in PATIENTS:
        if _p.get("skip_follow_up"):
            continue
        if _p["patient_id"] in _PREFILL_SKIP_IDS:
            continue
        # 已有计划则跳过（避免重复生成）
        if get_latest_plan(_p["patient_id"]):
            continue
        diag = _p.get("diagnosis", "")
        fu = _p.get("follow_up_plan") or {}
        freq = fu.get("frequency", "每日")
        plan_json = {
            "frequency": freq,
            "duration_days": 90,
            "pain_type": diag,
            "recheck_items": ["每周记录NRS疼痛评分", "每月复诊评估疗效"],
            "medication_adjustment": "维持现有镇痛方案，NRS≥5时由医生评估是否调整剂量或联合用药",
            "warning_threshold": "NRS≥7，或出现爆发痛、睡眠持续变差、药物不良反应时及时就医",
            "health_education": [
                "按时服药，不可自行增减量或停药",
                "每日记录疼痛评分（NRS 0-10）",
                "出现爆发痛及时联系主管医生",
            ],
            "lifestyle": [
                "规律作息，保证充足睡眠",
                "适度活动，避免久坐久站",
                "清淡饮食，戒烟限酒",
            ],
            "frequency_source": "来自出院处方",
        }
        try:
            create_plan(
                patient_id=_p["patient_id"],
                plan_json=plan_json,
                discharge_summary=None,
                citations=[],
                status="draft",
            )
            _prefill_count += 1
        except Exception as _e:
            log.debug("预填充跳过 %s(%s): %s", _p['name'], _p['patient_id'], _e)
    log.info("已预填充 %d 份随访计划草稿 (%.1fs)", _prefill_count, _time.time() - _pt0)

    # 方案 A：用判定引擎把"当日是否随访"回写到每位患者字典（唯一真源）
    try:
        _today_list = apply_today_followup_flags(PATIENTS)
        log.info("当日随访判定: 应随访 %d 人, 免随访 %d 人 (today=%s)",
                 _today_list['send_count'], _today_list['skip_count'], _today_list['today'])
    except Exception as e:
        log.error("当日随访判定异常: %s", e)

    REPLIES = db.get_all_replies()

    container = build_container(
        db=db, patients=PATIENTS, replies=REPLIES, emit_callback=runtime.broadcast,
    )

    # ======== 写入运行时共享状态 ========
    runtime.db = db
    runtime.PATIENTS = PATIENTS
    runtime.REPLIES = REPLIES
    runtime.followup_service = container.followup_service
    runtime.risk_engine = container.risk_engine
    runtime.reply_parser = container.reply_parser
    runtime.message_generator = container.message_generator
    runtime.auto_reply_engine = container.auto_reply_engine
