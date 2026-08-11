"""依赖组装容器。

app.py 只负责协议入口，具体对象创建集中在这里，避免入口文件继续膨胀。
工具采用 function-calling 模式（engine/tool_definitions.py）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.event_bus import EventBus
from engine.auto_reply import AutoReplyEngine
from engine.risk_engine import RiskEngine
from engine.tool_definitions import register_handlers
from llm.message_generator import MessageGenerator
from llm.parser import ReplyParser
from data.database import PatientDB
from services.followup_service import FollowupService


@dataclass
class AppContainer:
    db: PatientDB
    patients: list[dict[str, Any]]
    replies: dict[str, dict[str, Any]]
    event_bus: EventBus
    risk_engine: RiskEngine
    reply_parser: ReplyParser
    message_generator: MessageGenerator
    auto_reply_engine: AutoReplyEngine
    followup_service: FollowupService


def build_container(db: PatientDB, patients: list[dict[str, Any]], replies: dict[str, dict[str, Any]], emit_callback) -> AppContainer:
    event_bus = EventBus(emit_callback=emit_callback)
    risk_engine = RiskEngine()
    reply_parser = ReplyParser()
    message_generator = MessageGenerator()

    # 注册 function-calling 工具 handler 依赖
    register_handlers(
        reply_parser=reply_parser,
        risk_engine=risk_engine,
        db=db,
        patients=patients,
        event_bus=event_bus,
    )

    auto_reply_engine = AutoReplyEngine(patients, replies, risk_engine)
    followup_service = FollowupService(
        patients=patients,
        replies=replies,
        db=db,
        message_generator=message_generator,
        reply_parser=reply_parser,
        risk_engine=risk_engine,
        event_bus=event_bus,
    )
    return AppContainer(
        db=db,
        patients=patients,
        replies=replies,
        event_bus=event_bus,
        risk_engine=risk_engine,
        reply_parser=reply_parser,
        message_generator=message_generator,
        auto_reply_engine=auto_reply_engine,
        followup_service=followup_service,
    )
