# backend/infrastructure/messaging/__init__.py
"""基础设施：消息与事件 Outbox。"""
from infrastructure.messaging.outbox import MessageOutbox, EventOutbox

__all__ = ["MessageOutbox", "EventOutbox"]
