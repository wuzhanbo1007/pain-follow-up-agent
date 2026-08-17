# backend/core/event_bus.py
"""统一事件总线。

业务层只发布领域事件，不直接依赖 Socket.IO。当前适配器保持原有前端事件名，
用于渐进替换 engine/orchestrator.py 中散落的 self.emit 调用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from core.logging_config import get_logger

log = get_logger("painsmart.event_bus")

EmitCallback = Callable[[str, Any], None]


@dataclass
class EventBus:
    emit_callback: EmitCallback
    logs: list[dict[str, Any]] = field(default_factory=list)
    max_logs: int = 500

    def publish(self, event: str, payload: Any | None = None) -> None:
        """发布一个领域事件。

        内部统一封装为 Comet 风格的类型化事件包 ``{"type": event, "data": payload}``，
        再交给 emit_callback（传输适配器，如 WebSocket）分发。业务层只认事件类型，
        不感知 Socket.IO 等传输细节——事件总线与传输彻底解耦。
        """
        envelope = {"type": event, "data": payload or {}}
        try:
            self.emit_callback(envelope)
        except Exception as exc:
            log.warning("事件发布失败 %s: %s", event, exc)

    def log(self, message: str, level: str = "info") -> None:
        item = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
        }
        self.logs.append(item)
        if len(self.logs) > self.max_logs:
            self.logs[:] = self.logs[-self.max_logs:]
        self.publish("agent:log", item)

    def state_change(
        self,
        state: str,
        description: str | None = None,
        previous_state: str | None = None,
        previous_description: str | None = None,
    ) -> None:
        self.publish("agent:state_change", {
            "from": previous_state,
            "to": state,
            "from_label": previous_description or previous_state or "",
            "to_label": description or state,
            "state": state,
            "description": description or state,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        })

    def stats(self, stats: dict[str, Any]) -> None:
        self.publish("agent:stats", stats)

    def wechat_sent(self, payload: dict[str, Any]) -> None:
        payload.setdefault("timestamp", datetime.now().strftime("%H:%M:%S"))
        self.publish("agent:wechat_sent", payload)

    def patient_reply(self, payload: dict[str, Any]) -> None:
        payload.setdefault("timestamp", datetime.now().strftime("%H:%M:%S"))
        self.publish("patient:reply", payload)

    def review_ready(self, payload: dict[str, Any]) -> None:
        self.publish("review:session_ready", payload)
