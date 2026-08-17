# backend/infrastructure/runtime_context.py
"""运行时上下文（说明书 9.3 Runtime Context）。

AppContext 把 DB / EventBus / LLM / Repository / Outbox / DateProvider 等运行时依赖
集中注入，State 只保存 JSON 可序列化业务数据，从而保证 checkpoint 可序列化。

图通过 StateGraph(State, context_schema=AppContext) 和 Runtime[AppContext] 获取依赖，
DB 连接、EventBus、LLM Client、RiskEngine、Simulator 实例不得进入 State。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppContext:
    """运行时依赖容器（注入到 LangGraph context_schema）。

    装配顺序（§7）：先 EventBus / DB → 再 Repository / Outbox → 最后
    build_context 返回完整 Context；新图节点统一通过 ``Runtime[AppContext]``
    获取 ``runtime.context``，State 不持有任何运行时对象。
    """
    patient_repository: Any = None        # PatientRepository
    followup_repository: Any = None       # FollowupRepository
    plan_repository: Any = None           # PlanRepository
    review_repository: Any = None         # ReviewRepository
    policy_repository: Any = None         # PolicyRepository
    dispatch_repository: Any = None       # DispatchRepository（§6 投影）
    episode_repository: Any = None        # EpisodeRepository（§6 投影）
    llm_gateway: Any = None               # LLMGateway
    message_outbox: Any = None            # MessageOutbox
    event_outbox: Any = None              # EventOutbox
    event_bus: Any = None                 # 旧 EventBus（过渡期兼容）
    checkpointer: Any = None              # Checkpointer（唯一工厂，§6）
    risk_evaluator: Any = None            # RiskEvaluator
    coverage_evaluator: Any = None        # CoverageEvaluator
    turn_router: Any = None               # TurnRouter
    conversation_policy: Any = None       # ConversationPolicy
    clock: Any = None                     # DateProvider
    logger: Any = None
    # 运行时参数（config/followup_runtime.yaml：manual_patient_ids / prefill_skip_ids）
    settings: Any = None                  # RuntimeSettings
    # 调度运行锁（P1#27：跨 DispatchService 实例共享，避免并发重复调度）
    running_dispatches: Any = None        # set[str]
    # 旧运行时共享对象（过渡期，逐步移除）
    followup_service: Any = None
    # 工具执行器依赖注入
    tool_registry: Any = None

    def ensure_defaults(self) -> "AppContext":
        """懒加载默认依赖（仅在未注入时构造，避免循环依赖）。"""
        if self.patient_repository is None:
            from infrastructure.repositories.patient_repository import PatientRepository
            self.patient_repository = PatientRepository()
        if self.followup_repository is None:
            from infrastructure.repositories.followup_repository import FollowupRepository
            self.followup_repository = FollowupRepository()
        if self.plan_repository is None:
            from infrastructure.repositories.plan_repository import PlanRepository
            self.plan_repository = PlanRepository()
        if self.policy_repository is None:
            from infrastructure.repositories.policy_repository import PolicyRepository
            self.policy_repository = PolicyRepository()
        if self.review_repository is None:
            from infrastructure.repositories.review_repository import ReviewRepository
            self.review_repository = ReviewRepository()
        if self.dispatch_repository is None:
            from infrastructure.repositories.dispatch_repository import DispatchRepository
            self.dispatch_repository = DispatchRepository()
        if self.episode_repository is None:
            from infrastructure.repositories.episode_repository import EpisodeRepository
            self.episode_repository = EpisodeRepository()
        if self.llm_gateway is None:
            from infrastructure.llm.llm_gateway import LLMGateway
            self.llm_gateway = LLMGateway()
        if self.message_outbox is None:
            from infrastructure.messaging.outbox import MessageOutbox
            self.message_outbox = MessageOutbox(self.event_bus)
        if self.event_outbox is None:
            from infrastructure.messaging.outbox import EventOutbox
            self.event_outbox = EventOutbox(self.event_bus)
        if self.checkpointer is None:
            from infrastructure.langgraph.checkpointer import get_checkpointer
            self.checkpointer = get_checkpointer(self.settings)
        if self.risk_evaluator is None:
            from domain.services.risk_evaluator import RiskEvaluator
            self.risk_evaluator = RiskEvaluator()
        if self.coverage_evaluator is None:
            from domain.services.coverage_evaluator import CoverageEvaluator
            self.coverage_evaluator = CoverageEvaluator()
        if self.turn_router is None:
            from domain.services.turn_router import TurnRouter
            self.turn_router = TurnRouter()
        if self.conversation_policy is None:
            from domain.policies.schedule_spec import ConversationPolicy
            self.conversation_policy = ConversationPolicy()
        if self.clock is None:
            from infrastructure.date_provider import DateProvider
            self.clock = DateProvider()
        if self.settings is None:
            from core.runtime_settings import load_runtime_settings
            self.settings = load_runtime_settings()
        if self.running_dispatches is None:
            self.running_dispatches = set()
        if self.logger is None:
            from core.logging_config import get_logger
            self.logger = get_logger("painsmart.context")
        return self


# 模块级单例（供非图代码 / 旧路径过渡使用）
_context: AppContext | None = None


def get_context() -> AppContext:
    global _context
    if _context is None:
        _context = AppContext().ensure_defaults()
    return _context


def set_context(ctx: AppContext) -> None:
    global _context
    _context = ctx
