# backend/core/bootstrap.py
"""
启动引导（从 app.py 抽离）

集中处理：MySQL 初始化、患者 / 回复加载、随访计划预填充、AppContext 装配。

装配顺序（§7）：先建 EventBus 与数据库，再建 Repository / Outbox，最后
build_context() 返回完整 AppContext；新图节点通过 Runtime[AppContext] 获取
runtime.context（不再调用全局 get_context()）。State 不持有运行时对象（可序列化）。
旧 DI 容器 core.container 与 engine 兼容层已退役（见 ARCHITECTURE_REFACTOR.md）。
"""
import time as _time

from core.realtime import emit
from core.logging_config import configure_logging, get_logger

log = get_logger("painsmart")


def build_database(settings=None):
    """构建数据库访问对象（MySQL，幂等建表 + 首次播种）。"""
    from data.database import PatientDB
    return PatientDB()


def build_logger(settings=None):
    from core.logging_config import get_logger as _gl
    return _gl("painsmart")


def build_context(settings):
    """装配完整 AppContext（§7 build_context）。

    顺序：EventBus / DB → Repository / Outbox → Context（含 Checkpointer）。
    所有运行时依赖集中注入，新图节点经 context_schema 获取；State 只存 JSON 数据。
    """
    from core.event_bus import EventBus
    from core.runtime_settings import RuntimeSettings
    from infrastructure.runtime_context import AppContext
    from infrastructure.repositories.patient_repository import PatientRepository
    from infrastructure.repositories.followup_repository import (
        FollowupRepository, PlanRepository,
    )
    from infrastructure.repositories.review_repository import ReviewRepository
    from infrastructure.repositories.policy_repository import PolicyRepository
    from infrastructure.repositories.dispatch_repository import DispatchRepository
    from infrastructure.repositories.episode_repository import EpisodeRepository
    from infrastructure.messaging.outbox import MessageOutbox, EventOutbox
    from infrastructure.llm.llm_gateway import LLMGateway
    from infrastructure.langgraph.checkpointer import get_checkpointer

    if settings is None:
        settings = RuntimeSettings()
    db = build_database(settings)
    event_bus = EventBus(emit_callback=emit)

    ctx = AppContext()
    ctx.event_bus = event_bus
    ctx.settings = settings
    ctx.patient_repository = PatientRepository(db)
    ctx.followup_repository = FollowupRepository()
    ctx.plan_repository = PlanRepository()
    ctx.review_repository = ReviewRepository()
    ctx.policy_repository = PolicyRepository()
    ctx.dispatch_repository = DispatchRepository()
    ctx.episode_repository = EpisodeRepository()
    ctx.message_outbox = MessageOutbox(event_bus)
    ctx.event_outbox = EventOutbox(event_bus)
    ctx.llm_gateway = LLMGateway()
    ctx.checkpointer = get_checkpointer(settings)
    # 补齐领域服务 / clock / logger / running_dispatches（已在上下文中注入的不覆盖）
    ctx.ensure_defaults()
    return ctx


def bootstrap():
    """初始化数据库并装配 AppContext（§7 build_context 唯一装配入口）。"""
    from core.runtime_settings import load_runtime_settings
    from infrastructure.date_provider import DateProvider

    settings = load_runtime_settings()

    # ======== 演示日期启动校验（§4.1 / §12.1）========
    # 演示环境业务日期必须来自配置 DEMO_TODAY：缺失或格式错误直接报错，
    # 不允许静默回退到服务器当天日期（避免配置遗漏后悄悄使用系统日期）。
    try:
        _business_date = DateProvider().get_business_date()
    except RuntimeError as exc:
        log.error("演示环境业务日期配置无效: %s", exc)
        raise
    log.info("业务日期已冻结: %s (来源 DEMO_TODAY)", _business_date.isoformat())

    # ======== 初始化 MySQL 数据库（幂等建 21 张表 + 首次播种）========
    # 说明：MySQL 为持久化存储，播种逻辑幂等（患者表为空时才写入）。
    _t0 = _time.time()
    db = build_database(settings)
    log.info("init_db 完成 (%.1fs)", _time.time() - _t0)

    # ======== 装配 AppContext（§7：build_context 唯一装配入口）========
    try:
        from infrastructure.runtime_context import set_context
        ctx = build_context(settings)
        set_context(ctx)
        log.info("AppContext 已装配（build_context，engine 兼容层已退役）")
    except Exception as e:
        log.warning("AppContext 初始化失败（新架构接口降级）: %s", e)
        ctx = None

    # ======== PromptRegistry 启动校验（§12.1：无孤儿 / 无多 Agent 复用）========
    # 校验失败不阻断启动（演示环境降级），但逐条记录 error 便于尽早发现配置遗漏。
    try:
        from domain.prompt_registry import REGISTRY as _PR_REGISTRY
        from domain.prompt_registry import validate_registry as _validate_registry
        _problems = _validate_registry()
        if _problems:
            log.error("PromptRegistry 校验未通过（%d 项，共 %d 个注册）:",
                      len(_problems), len(_PR_REGISTRY))
            for _p in _problems:
                log.error("  - %s", _p)
        else:
            log.info("PromptRegistry 校验通过（%d 个提示词注册，无孤儿/无复用）",
                     len(_PR_REGISTRY))
    except Exception as _e:
        log.warning("PromptRegistry 启动校验跳过: %s", _e)

    # ======== 预填充随访计划草稿 ========
    # 演示用：跳过手动演示患者（§P2#4：患者 ID 不硬编码，由
    # config/followup_runtime.yaml 的 manual_patient_ids / prefill_skip_ids 配置）。
    _pt0 = _time.time()
    _PREFILL_SKIP_IDS = set(settings.prefill_skip_ids)
    _prefill_count = 0
    if ctx is not None:
        patients = ctx.patient_repository.get_all_patients()
        for _p in patients:
            if _p.get("skip_follow_up"):
                continue
            if _p["patient_id"] in _PREFILL_SKIP_IDS:
                continue
            # 已有计划则跳过（避免重复生成）
            if ctx.plan_repository.get_latest_plan(_p["patient_id"]):
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
                ctx.plan_repository.create_plan(
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
    else:
        log.warning("AppContext 未装配，跳过随访计划预填充")

    # ======== 旧版共享状态（core.runtime）已删除 ========
    # 全部 REST/WS 状态改从 get_context()（AppContext）读取；db 仅保留上面的
    # 初始化日志与建表副作用，不再写入任何全局运行时对象。
