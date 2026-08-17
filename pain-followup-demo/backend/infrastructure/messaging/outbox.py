# backend/infrastructure/messaging/outbox.py
"""消息与事件 Outbox（说明书 11.4）。

图节点在同一事务中写业务结果和 outbox_event；独立 Worker 投递 WebSocket / 短信 / 告警，
成功后标记 delivered。这样图恢复或节点重试不会直接重复广播。

演示实现：进程内队列 + 即时投递（经旧 EventBus 透传，过渡期兼容）；
每条事件带幂等 event_key，重复入队自动去重。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from core.logging_config import get_logger

log = get_logger("painsmart.outbox")


class _OutboxBase:
    def __init__(self, event_bus=None):
        self._event_bus = event_bus
        self._delivered: dict[str, float] = {}  # event_key -> ts（幂等去重）

    def _is_duplicate(self, key: str) -> bool:
        return key in self._delivered

    def _mark(self, key: str) -> None:
        self._delivered[key] = time.time()


class MessageOutbox(_OutboxBase):
    """护士消息幂等入队（说明书 6.2 send_message）。"""

    def enqueue(self, *, episode_id: str, patient_id: str, patient_name: str,
                role: str, content: str, turn_no: int, message_key: str,
                dispatch_id: str = "") -> bool:
        if self._is_duplicate(message_key):
            log.debug("消息已投递，跳过 %s", message_key)
            return False
        self._mark(message_key)
        payload = {
            "dispatch_id": dispatch_id,
            "episode_id": episode_id,
            "patient_id": patient_id,
            "patient_name": patient_name,
            "role": role,
            "content": content,
            "turn_no": turn_no,
            "message_key": message_key,
        }
        self._emit("episode:message", payload)
        self._emit("agent:message", payload)  # 过渡期兼容旧前端
        if role == "nurse":
            self._emit("agent:wechat_sent", {
                "patient_id": patient_id, "patient_name": patient_name,
                "text": content, "message": content, "role": "nurse",
                "source": "patient_followup_graph",
            })
        return True

    def _emit(self, event: str, payload: dict) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.publish(event, payload)
        except Exception as exc:
            log.warning("消息投递失败 %s: %s", event, exc)


class EventOutbox(_OutboxBase):
    """状态/风险/告警/审阅事件幂等发布（说明书 11.4）。

    §5：通用 enqueue/drain 用于"发布启动事件 → 独立 Worker 消费"的解耦路径
    （Dispatcher 发 episode.start_requested，DispatchService drain 后逐条启动独立 Episode）。
    """

    def __init__(self, event_bus=None):
        super().__init__(event_bus)
        self._pending: list[dict] = []
        self._pending_guard = threading.RLock()

    def publish(self, event: str, payload: dict, *, event_key: str) -> bool:
        if self._is_duplicate(event_key):
            return False
        self._mark(event_key)
        if self._event_bus is not None:
            try:
                self._event_bus.publish(event, payload)
            except Exception as exc:
                log.warning("事件投递失败 %s: %s", event, exc)
        return True

    def enqueue(self, *, event_type: str, aggregate_id: str,
                payload: dict | None = None, event_key: str | None = None) -> bool:
        """通用事件入队（§5 episode.start_requested）：幂等 + 本地待投递队列。

        图节点与业务写入同批入队；消费端 drain() 逐条取出处理。重复入队
        （重试 / 重复投递）按 event_key 自动去重。
        """
        key = event_key or f"{event_type}:{aggregate_id}"
        if self._is_duplicate(key):
            return False
        self._mark(key)
        record = {
            "event_type": event_type, "aggregate_id": aggregate_id,
            "payload": payload or {}, "event_key": key,
        }
        with self._pending_guard:
            self._pending.append(record)
        if self._event_bus is not None:
            try:
                self._event_bus.publish(event_type, record["payload"])
            except Exception as exc:
                log.warning("事件投递失败 %s: %s", event_type, exc)
        return True

    def drain(self, *, event_type: str | None = None) -> list[dict]:
        """消费端拉取并清空待投递事件（§5：DispatchService 逐条启动独立 Episode）。

        取走即视为已消费；不投递重试消息。返回 [{"event_type", "aggregate_id",
        "payload", "event_key"}, ...]。
        """
        with self._pending_guard:
            if event_type is None:
                out, self._pending = list(self._pending), []
                return out
            out = [r for r in self._pending if r["event_type"] == event_type]
            self._pending = [r for r in self._pending if r["event_type"] != event_type]
            return out

    def episode_state_changed(self, *, dispatch_id: str, episode_id: str,
                              patient_id: str, status: str,
                              input_source: str = "simulator",
                              channel: str = "wechat") -> None:
        key = f"state:{episode_id}:{status}"
        self.publish("episode:state_changed", {
            "dispatch_id": dispatch_id, "episode_id": episode_id,
            "patient_id": patient_id, "status": status,
            "input_source": input_source, "channel": channel,
        }, event_key=key)
        # 过渡期兼容旧前端
        self.publish("agent:progress", {
            "phase": status, "episode_id": episode_id,
            "patient_id": patient_id,
        }, event_key=key + ":progress")

    def waiting_human(self, *, dispatch_id: str, episode_id: str,
                      patient_id: str, question: str, thread_id: str) -> None:
        key = f"waiting:{episode_id}"
        self.publish("episode:waiting_human", {
            "dispatch_id": dispatch_id, "episode_id": episode_id,
            "patient_id": patient_id, "thread_id": thread_id,
            "question": question,
        }, event_key=key)
        self.publish("demo:patients_selected", {
            "demo_patient_ids": [patient_id],
            "patients": [{"patient_id": patient_id}],
        }, event_key=key + ":demo")

    def report_ready(self, *, dispatch_id: str, episode_id: str,
                     patient_id: str, status: str, risk_result: dict,
                     report: dict) -> None:
        key = f"report:{episode_id}"
        self.publish("episode:report_ready", {
            "dispatch_id": dispatch_id, "episode_id": episode_id,
            "patient_id": patient_id, "status": status,
            "risk_result": risk_result, "report": report,
        }, event_key=key)

    def review_ready(self, *, dispatch_id: str, episode_id: str,
                     patient_id: str, review_key: str, ai_review: dict) -> None:
        key = review_key
        self.publish("review:ready", {
            "dispatch_id": dispatch_id, "episode_id": episode_id,
            "patient_id": patient_id, "review_key": review_key,
            "ai_review": ai_review,
        }, event_key=key)

    def dispatch_state_changed(self, *, dispatch_id: str, status: str,
                               counts: dict | None = None) -> None:
        """调度状态迁移事件（说明书 10.3 / 15.3 dispatch:state_changed）。

        状态序列：loading_inputs → calculating_base_roster → applying_callback_policy
        → dispatching → waiting_patients / completed / partial / failed。
        """
        self.publish("dispatch:state_changed", {
            "dispatch_id": dispatch_id, "status": status,
            "counts": counts or {},
        }, event_key=f"dispatch_state:{dispatch_id}:{status}")

    def dispatch_started(self, *, dispatch_id: str, business_date: str,
                         timezone: str, patient_snapshot_version: str) -> None:
        self.publish("dispatch:started", {
            "dispatch_id": dispatch_id, "business_date": business_date,
            "timezone": timezone, "patient_snapshot_version": patient_snapshot_version,
        }, event_key=f"dispatch:started:{dispatch_id}")

    def roster_ready(self, *, dispatch_id: str, send_count: int, skip_count: int,
                     phone_callback_count: int, send_roster, skip_roster,
                     callback_policy_version, details) -> None:
        self.publish("dispatch:roster_ready", {
            "dispatch_id": dispatch_id, "send_count": send_count,
            "skip_count": skip_count, "phone_callback_count": phone_callback_count,
            "send_roster": send_roster, "skip_roster": skip_roster,
            "callback_policy_version": callback_policy_version, "details": details,
        }, event_key=f"roster:{dispatch_id}")
        # 过渡期兼容旧前端 agent:filter_result
        self.publish("agent:filter_result", {
            "total": send_count + skip_count, "send_count": send_count,
            "skip_count": skip_count, "send_list": send_roster,
            "skip_list": skip_roster, "details": details,
        }, event_key=f"filter:{dispatch_id}")

    def dispatch_report_ready(self, *, dispatch_id: str, status: str,
                              counts: dict, reports_by_episode: dict) -> None:
        self.publish("dispatch:report_ready", {
            "dispatch_id": dispatch_id, "status": status,
            "counts": counts, "reports_by_episode": reports_by_episode,
        }, event_key=f"dispatch_report:{dispatch_id}")
        # 过渡期兼容旧前端 agent:final_stats
        self.publish("agent:final_stats", {
            "status": status, **counts, "reports_by_episode": reports_by_episode,
        }, event_key=f"final_stats:{dispatch_id}")

    def risk_scored(self, *, dispatch_id: str, episode_id: str,
                    patient_id: str, patient_name: str, risk_result: dict) -> None:
        key = f"risk:{episode_id}:{risk_result.get('level')}"
        self.publish("agent:risk_scored", {
            "dispatch_id": dispatch_id, "episode_id": episode_id,
            "patient_id": patient_id, "patient_name": patient_name,
            "level": risk_result.get("level"),
            "total_score": risk_result.get("total_score", risk_result.get("score")),
            "reason": risk_result.get("reason", ""),
            "risk_result": risk_result,
        }, event_key=key)

    def turn_decision(self, *, dispatch_id: str, episode_id: str,
                      patient_id: str, patient_name: str, turn_no: int,
                      input_source: str, decision: dict,
                      coverage: dict | None = None,
                      risk_result: dict | None = None) -> None:
        """发布 TurnRouter 决策，供前端决策日志和审计使用。

        TurnRouter 的结果原本只保存在 LangGraph State 中，前端无法实时看到
        "继续追问 / 完成 / 转人工" 的原因。这里同时发布新事件
        ``episode:decision`` 和兼容现有前端的 ``agent:log``。
        """
        decision = decision or {}
        coverage = coverage or {}
        risk_result = risk_result or {}
        action = decision.get("action", "unknown")
        action_labels = {
            "clarify": "继续追问",
            "continue_wait": "继续等待回复",
            "complete": "完成随访",
            "incomplete_handoff": "信息不完整，转人工",
            "emergency_handoff": "紧急转人工",
        }
        action_label = action_labels.get(action, action)
        missing = decision.get("missing_slots") or coverage.get("missing") or []
        missing_text = "、".join(str(item) for item in missing) or "无"
        reason = decision.get("reason") or "未提供原因"
        risk_level = risk_result.get("level")
        risk_text = risk_level or "待对话结束后评估"
        display_name = patient_name or f"患者{patient_id}"
        message = (
            f"第{turn_no}轮 · {display_name}({patient_id}) · "
            f"TurnRouter：{action_label} · 原因：{reason} · "
            f"缺失项：{missing_text} · 风险：{risk_text}"
        )
        payload = {
            "dispatch_id": dispatch_id,
            "episode_id": episode_id,
            "patient_id": patient_id,
            "patient_name": patient_name,
            "turn_no": turn_no,
            "input_source": input_source,
            "decision": decision,
            "coverage": coverage,
            "risk_result": risk_result,
            "message": message,
            "state": "turn_router",
            "timestamp": time.strftime("%H:%M:%S"),
        }
        self.publish(
            "episode:decision", payload,
            event_key=f"decision:{episode_id}:{turn_no}",
        )
        # 当前前端 Pinia Store 已通过 agent:log 接收日志；保留该事件名，
        # 同时让新前端可以使用 episode:decision 获取结构化决策数据。
        self.publish(
            "agent:log", payload,
            event_key=f"decision_log:{episode_id}:{turn_no}",
        )

    def doctor_alert(self, *, dispatch_id: str, episode_id: str,
                     patient_id: str, patient_name: str, risk_result: dict,
                     alert_key: str) -> None:
        self.publish("doctor:alert", {
            "patient_id": patient_id, "patient_name": patient_name,
            "risk_level": risk_result.get("level", "high"),
            "risk_result": risk_result,
            "message": f"{patient_name} 触发高风险预警",
        }, event_key=alert_key)

    def callback_alert(self, *, dispatch_id: str, episode_id: str,
                       patient_id: str, patient_name: str, no_reply_days: int,
                       reason: str, alert_key: str) -> None:
        display_name = patient_name or f"患者{patient_id}"
        self.publish("doctor:callback_alert", {
            "dispatch_id": dispatch_id,
            "episode_id": episode_id,
            "patient_id": patient_id,
            "patient_name": display_name,
            "type": "no_reply",
            "risk_level": "callback",
            "risk_score": None,
            "no_reply_days": no_reply_days,
            "reason": reason,
            "message": f"{display_name}连续{no_reply_days}天未回复，需要电话回访",
            "timestamp": time.strftime("%H:%M:%S"),
        }, event_key=alert_key)

    def immediate_intervention_alert(self, *, dispatch_id: str,
                                     episode_id: str, patient_id: str,
                                     patient_name: str, turn_no: int,
                                     reason: str, alert_key: str) -> None:
        """患者表达需要立即关注的情绪/痛苦时，实时通知医生端。"""
        display_name = patient_name or f"患者{patient_id}"
        self.publish("doctor:alert", {
            "dispatch_id": dispatch_id,
            "episode_id": episode_id,
            "patient_id": patient_id,
            "patient_name": display_name,
            "turn_no": turn_no,
            "alert_type": "emotional_instability",
            "risk_level": "urgent",
            "requires_immediate_action": True,
            "reason": reason,
            "message": f"{display_name}情绪不稳定，需要人工介入",
        }, event_key=alert_key)
