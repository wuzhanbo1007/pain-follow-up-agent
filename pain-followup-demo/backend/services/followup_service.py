"""随访业务服务层。

目标：将 WebSocket 入口与随访业务编排解耦，统一下发事件协议，复用现有 Agent/Engine 能力。

并发模型（与 Comet 对齐）：全链路 async——每位患者的随访流水线是事件循环上���一个
asyncio.Task（asyncio.create_task），LLM 调用统一 await（ChatOpenAI.ainvoke），
不使用 threading.Thread / threading.Lock / time.sleep 阻塞事件循环。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.event_bus import EventBus
from core.logging_config import get_logger
from engine.followup_scheduler import apply_today_followup_flags, build_today_send_list
from db.followup_db import (
    create_review,
    create_session,
    get_today_session,
    update_session,
    get_review_by_session,
    attach_ai_review,
)
from engine.react_core import run_tool_reflect, target_question
from agents.summarizer import summarize_session

log = get_logger("painsmart.followup_service")


@dataclass
class FollowupService:
    patients: list[dict[str, Any]]
    replies: dict[str, dict[str, Any]]
    db: Any
    message_generator: Any
    reply_parser: Any
    risk_engine: Any
    event_bus: EventBus
    stats: dict[str, int] = field(default_factory=dict)
    state: str = "IDLE"
    conversations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _running: bool = False

    # 演示协作状态：区分「自动患者」与「手动演示患者」
    demo_patient_ids: set = field(default_factory=set)   # 手动演示患者（人工逐步推进）
    pending_auto: int = 0                                 # 仍在后台自动随访的患者数
    demo_done_ids: set = field(default_factory=set)       # 已走完的手动演示患者（按患者分别记录）
    final_emitted: bool = False                           # 最终统计是否已下发（防重复）

    # 患者风险明细（供最终统计补全 high_risk_focus / low_risk_exempt / no_reply_patients）
    _patient_risk_records: list[dict[str, Any]] = field(default_factory=list)
    _sent_patient_ids: set = field(default_factory=set)  # 本轮已发出开场白的患者
    _pending_auto_lock: Any = field(default_factory=asyncio.Lock)

    # ReACT 范式：每位患者独立的思考状态
    _react_states: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.reset_stats()

    @property
    def logs(self) -> list[dict[str, Any]]:
        return self.event_bus.logs

    def reset_stats(self) -> None:
        self.stats = {
            "total_patients": len(self.patients),
            "send_count": 0,
            "skip_count": 0,
            "sent_count": 0,
            "replied_count": 0,
            "no_reply_count": 0,
            "high_risk_count": 0,
            "medium_risk_count": 0,
            "low_risk_count": 0,
            "alert_count": 0,
            "callback_count": 0,
            "total_assessed": 0,
        }
        self._patient_risk_records.clear()

    def transition_to(self, state: str) -> None:
        previous_state = self.state
        self.state = state
        self.event_bus.state_change(state, previous_state=previous_state)

    def run_daily_followup(self) -> dict[str, Any]:
        """触发今日随访：在事件循环上创建后台 async 任务（不阻塞调用方）。"""
        if self._running:
            return {"status": "running"}
        self._running = True
        asyncio.create_task(self._run_daily_followup())
        return {"status": "started"}

    async def _run_daily_followup(self) -> None:
        try:
            self.reset_stats()
            # 重置演示协作状态
            self.demo_patient_ids = set()
            self.pending_auto = 0
            self.demo_done_ids = set()
            self.final_emitted = False
            self._patient_risk_records.clear()
            self._sent_patient_ids = set()
            self.transition_to("FETCHING")
            self.event_bus.log(f"触发今日随访，共 {len(self.patients)} 位患者")

            # 重新按"今天"计算每位患者的应随访标记（保证反映最新计划/状态）
            try:
                apply_today_followup_flags(self.patients)
            except Exception as _fe:
                self.event_bus.log(f"判定引擎计算失败，回退硬编码标记：{_fe}")

            today_result = build_today_send_list(self.patients)
            send_list = today_result.get("send_list", [])
            skip_list = today_result.get("skip_list", [])
            self.stats.update({
                "total_patients": today_result.get("total", len(self.patients)),
                "send_count": len(send_list),
                "skip_count": len(skip_list),
            })
            self.event_bus.stats(self.stats)

            self.transition_to("FILTERING")
            self.event_bus.log(f"过滤免随访：共{len(self.patients)}人，免随访{len(skip_list)}人，实际发送{len(send_list)}人")
            self.event_bus.publish("agent:filter_result", {
                "total": len(self.patients),
                "send_count": len(send_list),
                "skip_count": len(skip_list),
                "send_list": send_list,
                "skip_list": skip_list,
                "details": today_result.get("details", []),
                "skip_details": [
                    {
                        "patient_id": p.get("patient_id"),
                        "name": p.get("name"),
                        "reason": (p.get("followup_detail") or {}).get("reason") or p.get("skip_reason", ""),
                        "matched_rule": (p.get("followup_detail") or {}).get("matched_rule", ""),
                        "day_index": (p.get("followup_detail") or {}).get("day_index"),
                    }
                    for p in skip_list
                ],
            })

            demo_patients = [p for p in send_list if p.get("patient_id") in self.replies][:2]
            self.demo_patient_ids = {p.get("patient_id") for p in demo_patients}
            if demo_patients:
                self.event_bus.publish("demo:patients_selected", {
                    "demo_patient_ids": [p.get("patient_id") for p in demo_patients],
                    "patients": [self._patient_brief(p) for p in demo_patients]
                })

            self.transition_to("GENERATING")
            self.event_bus.log(f"开始为 {len(send_list)} 位患者并行生成个性化随访消息...")
            for patient in send_list:
                await self._send_initial_message(patient)
                self.event_bus.log(f"✓ 已生成 {patient.get('name', '')}({patient.get('patient_id', '')}) 的随访消息")

            # 连续 3 天未回复的演示患者：发完开场白即标记"已完成"（不等待回复），
            # 后续由 _maybe_emit_final_stats 按 DB 标记归入"三日未回复"名单。
            # 同时创建一条 review 记录（track_status=pending_track），让医生能在审阅页看到该患者。
            from db.followup_db import create_review
            for pid in list(self.demo_patient_ids):
                p = next((x for x in send_list if x.get("patient_id") == pid), None)
                if p and int(p.get("consecutive_no_reply_days") or 0) >= 3:
                    self.demo_done_ids.add(pid)
                    try:
                        create_review(
                            session_id=f"no-reply-{pid}",
                            patient_id=pid,
                            track_status="pending_track",
                            audit_snapshot={
                                "source": "no_reply_skip",
                                "consecutive_no_reply_days": p.get("consecutive_no_reply_days"),
                                "reason": "连续3天未回复，需电话回访",
                            },
                        )
                    except Exception as e:
                        log.warning("为三日未回复患者 %s 创建 review 失败: %s", pid, e)
                    self.event_bus.log(
                        f"📵 {p.get('name', pid)} 连续{p.get('consecutive_no_reply_days')}天未回复，"
                        f"演示模式跳过等待回复，已生成待审阅记录"
                    )

            self.transition_to("WAITING_REPLY")
            self.event_bus.log("所有消息已发送完毕，进入等待回复状态...")
            self.event_bus.log(f"今日实际发送 {len(send_list)} 条个性化随访消息")
            self.event_bus.publish("agent:progress", {
                "phase": "sending", "current": int(self.stats["sent_count"]),
                "total": int(self.stats["send_count"]),
            })

            # 自动患者：按原逻辑在后台自动完成「对话 + 风险评估 + 审阅」，
            # 不占用演示通道；手动演示患者（demo_patient_ids）留待人工逐步推进。
            auto_patients = [p for p in send_list if p.get("patient_id") not in self.demo_patient_ids]
            self.pending_auto = len(auto_patients)
            if auto_patients:
                self.event_bus.log(f"启动 {len(auto_patients)} 位自动患者的后台随访（对话+风险评估）...")
                # 每位患者一个 async 任务：事件循环上并发运行，互不阻塞（Comet 风格）
                for patient in auto_patients:
                    asyncio.create_task(self._auto_run_patient(patient))
            else:
                # 没有自动患者时，若也无手动演示则直接出最终统计
                self._maybe_emit_final_stats()
        except Exception as exc:
            log.exception("随访服务执行失败")
            self.transition_to("ERROR")
            self.event_bus.log(f"随访执行失败：{exc}", level="error")
        finally:
            self._running = False

    async def _send_initial_message(self, patient: dict[str, Any]) -> None:
        """发送开场白，初始化该患者的 ReACT 状态（LLM 自主驱动后续追问）。"""
        patient = dict(patient)
        patient_id = patient["patient_id"]
        patient["history"] = self.db.get_history(patient_id, 7)

        # 用新版纯开场白生成（不含提问）
        greeting = (await self.message_generator.generate_greeting(patient)) or \
                   f"{patient.get('name', '')}您好，我是疼痛随访护士，来关心一下您今天的恢复情况。"

        first_msg = f"{greeting}\n\n今天感觉怎么样呀？"

        # 初始化 ReACT 状态：空信息清单，LLM 自主决定第一轮怎么问
        self._react_states[patient_id] = {
            "round": 0,
            "info_inventory": {},
            "history": [],
        }

        msg = {
            "patient_id": patient_id,
            "patient_name": patient.get("name", ""),
            "text": first_msg,
            "message": first_msg,
            "role": "nurse",
            "source": "followup_service",
        }
        self.conversations.setdefault(patient_id, []).append({"role": "nurse", "content": first_msg})
        self.stats["sent_count"] += 1
        self._sent_patient_ids.add(patient_id)
        self.event_bus.wechat_sent(msg)
        self.event_bus.publish("agent:message", {**msg, "generation_basis": {"greeting": greeting, "mode": "react"}})
        self.event_bus.stats(self.stats)

    def _send_question_text(self, patient: dict[str, Any], text: str) -> None:
        """发送单条护士追问消息（内部公用）。"""
        patient_id = patient["patient_id"]
        msg = {
            "patient_id": patient_id,
            "patient_name": patient.get("name", ""),
            "text": text,
            "message": text,
            "role": "nurse",
            "source": "followup_service",
        }
        self.conversations.setdefault(patient_id, []).append({"role": "nurse", "content": text})
        self.event_bus.wechat_sent(msg)
        self.event_bus.publish("agent:message", {**msg})
        self.event_bus.stats(self.stats)

    def _trim_redundant_prefix(self, text: str, patient_id: str) -> str:
        """去除 LLM 在 next_question 中复述的开场白/历史护士发言，仅保留本轮新增内容。

        演示中 LLM 偶会把完整对话历史（含开场白）复述进 next_question，导致每条护士
        消息都重复之前所有内容。这里与已发出的护士历史做前缀比对，截掉重复部分，
        保证前端每条气泡只显示本轮最新内容。
        """
        original = (text or "").strip()
        t = original
        if not t:
            return t
        nurse_history = [
            m["content"].strip()
            for m in self.conversations.get(patient_id, [])
            if m.get("role") == "nurse" and m.get("content", "").strip()
        ]
        changed = True
        while changed:
            changed = False
            for hist in nurse_history:
                if t.startswith(hist):
                    t = t[len(hist):].strip(" \n　")
                    changed = True
                    break
        # 兜底：若去重后为空（整句都是历史复述），保留原句，避免护士消息变空直接跳到结束语
        if not t:
            return original
        return t

    async def on_patient_reply(self, patient_id: str, reply_text: str) -> dict[str, Any]:
        patient = self.db.get_patient(patient_id)
        if not patient:
            return {"status": "not_found", "patient_id": patient_id}

        # 该患者已完成随访（已发送结束语，ReACT 状态已清除）→ 忽略后续回复
        if patient_id not in self._react_states and self.conversations.get(patient_id):
            last_msgs = self.conversations[patient_id]
            if last_msgs and last_msgs[-1].get("is_final"):
                return {"status": "already_completed", "patient_id": patient_id}

        # 记录患者回复
        self.conversations.setdefault(patient_id, []).append({"role": "patient", "content": reply_text})

        # ---- ReACT 范式：LLM 观察→思考→决定下一步 ----
        react_state = self._react_states.get(patient_id)
        if react_state is not None:
            # 递增轮次
            react_state["round"] += 1
            rnd = react_state["round"]

            # 追加对话历史用于 LLM 决策
            react_state.setdefault("history", []).append({"role": "patient", "content": reply_text})

            # 调用 ReACT 决策
            decision = await self._react_reflect(patient, react_state)

            should_end = decision.get("should_end", False)
            next_question = decision.get("next_question", "")
            # 去除 LLM 在 next_question 中复述的开场白/历史护士发言，仅保留本轮新增
            next_question = self._trim_redundant_prefix(next_question, patient_id)
            next_action = decision.get("next_action", "end")

            # 信息清单（LLM 解析结果）；用于判断是否可提前结束
            inv = react_state["info_inventory"] or {}
            missing_items = [
                k for k in ("pain_nrs", "sleep_quality", "medication_taken", "side_effects")
                if inv.get(k) in (None, "")
            ]

            # —— 防"直接跳到结束语"的兜底 ——
            # 若 LLM 判结束但信息尚未收集齐，强制继续追问（硬上限 6 轮后放行）
            if should_end and missing_items and rnd < 20:
                should_end = False
                next_question = await self._missing_question(missing_items, patient)
                next_action = "clarify"
            # LLM 决定继续但未给出追问内容（或被去重裁空）→ 用缺失项兜底生成一句
            if not should_end and not next_question:
                next_question = self._fallback_question(rnd)
                next_action = "clarify"
            # LLM 判结束但没给结束语文本 → 交给最终路径用 generate_farewell 处理

            self.event_bus.log(
                f"ReACT r{rnd} {patient.get('name', patient_id)}: "
                f"action={next_action} end={should_end} missing={missing_items}"
            )

            if not should_end and next_question:
                # LLM 决定继续追问 → 发送其生成的消息（停顿模拟真人思考，不阻塞事件循环）
                await asyncio.sleep(1.8)
                self._send_question_text(patient, next_question)
                self.transition_to("WAITING_REPLY")
                return {"status": "react_continue", "patient_id": patient_id,
                        "round": rnd, "next_action": next_action}

            # should_end=True → 跳出 ReACT，进入最终解析-评分-结束
            react_state["should_end"] = True

        # ---- ReACT 结束 / 非 ReACT 路径：完整解析-评分-告别 ----
        self.stats["replied_count"] += 1
        self.transition_to("PARSING")
        rounds = react_state.get("round", 1) if react_state else 1
        self.event_bus.log(
            f"收到 {patient.get('name', patient_id)}({patient_id}) 的随访回复（共{rounds}轮）"
        )

        # V9.1: 优先使用 function-calling tool 已产出的 parse/risk 结果
        tool_parsed = (react_state or {}).get("_tool_parsed") or {}
        tool_risk = (react_state or {}).get("_tool_risk_result") or {}

        if tool_parsed.get("nrs_score") is not None:
            # 工具已解析出结构化数据，直接使用，避免重复 LLM 调用
            parsed = tool_parsed
            today_data = {
                "nrs": parsed.get("nrs_score"),
                "sleep_quality": parsed.get("sleep_quality"),
                "medication_taken": parsed.get("medication_taken"),
            }
            self.event_bus.log(f"使用 function-calling parse 结果: NRS={parsed.get('nrs_score')}")
        else:
            # 旧版路径：全量回复解析
            history_m = react_state.get("history", []) if react_state else []
            all_replies = "\n".join([m["content"] for m in history_m if m["role"] == "patient"])
            if not all_replies:
                all_replies = reply_text
            history = self.db.get_history(patient_id, 7)
            history_ctx = "\n".join([
                f"{h.get('date')}: NRS={h.get('nrs_score')}, 睡眠={h.get('sleep_quality')}"
                for h in history[-5:]
            ])
            parsed = await self.reply_parser.parse(all_replies, history_ctx)
            today_data = {
                "nrs": parsed.get("nrs_score"),
                "sleep_quality": parsed.get("sleep_quality"),
                "medication_taken": parsed.get("medication_taken"),
            }
        parsed = dict(parsed)

        # 合并对话中所有患者回复 for DB write
        history_m = react_state.get("history", []) if react_state else []
        all_replies_for_db = "\n".join([m["content"] for m in history_m if m["role"] == "patient"])
        if not all_replies_for_db:
            all_replies_for_db = reply_text

        self.db.add_history(
            patient_id,
            parsed.get("nrs_score"),
            parsed.get("sleep_quality"),
            parsed.get("medication_taken"),
            all_replies_for_db,
        )

        self.transition_to("SCORING")
        # V9.1: 优先使用 tool 已算的 risk；若工具已返回 level，跳过重复计算
        if tool_risk.get("level") and tool_risk.get("total_score") is not None:
            risk_result = dict(tool_risk)
            self.event_bus.log(
                f"使用 function-calling risk 结果: level={risk_result.get('level')}"
                f" score={risk_result.get('total_score')}"
            )
        else:
            history = self.db.get_history(patient_id, 7)
            risk_result = self.risk_engine.calculate(patient_id, patient, today_data, history)
        self.event_bus.log(f"{patient.get('name', patient_id)}({patient_id}) 风险评分完成，级别：{risk_result.get('level', 'unknown')}")
        self._accumulate_risk(risk_result)
        self._record_patient_result(patient, risk_result, parsed)
        self.event_bus.publish("agent:risk_result", {
            "patient_id": patient_id,
            "patient_name": patient.get("name", ""),
            "risk_result": risk_result,
            "parse_result": parsed,
        })

        # 下发与前端看板对齐的风险事件（原 agent:risk_result 保留供其他消费方）
        self._emit_risk_events(patient, risk_result)
        if self.risk_engine.is_emergency(risk_result):
            self.stats["alert_count"] += 1
            self.event_bus.publish("agent:alert", {
                "patient_id": patient_id,
                "patient_name": patient.get("name", ""),
                "risk_result": risk_result,
                "message": f"{patient.get('name', patient_id)} 触发高风险预警",
            })
            # 前端 doctor:alert 仅用于预警计数（与 agent:alert 职责分离）
            self.event_bus.publish("doctor:alert", {
                "patient_id": patient_id,
                "patient_name": patient.get("name", ""),
                "risk_level": risk_result.get("level", "high"),
                "risk_result": risk_result,
                "message": f"{patient.get('name', patient_id)} 触发高风险预警",
            })

        farewell = await self.message_generator.generate_farewell(
            patient=patient,
            risk_level=risk_result.get("level", "medium_risk"),
            nrs_score=parsed.get("nrs_score"),
            sleep_quality=parsed.get("sleep_quality"),
            medication_status=parsed.get("medication_taken"),
            risk_tendency=(
                "high_risk" if risk_result.get("level") == "high" else
                "improving" if risk_result.get("trend") == "improving" else
                "worsening" if risk_result.get("trend") == "worsening" else
                "medium_risk"
            ),
        )
        self.conversations[patient_id].append({"role": "nurse", "content": farewell, "is_final": True})
        self.event_bus.wechat_sent({
            "patient_id": patient_id,
            "patient_name": patient.get("name", ""),
            "text": farewell,
            "message": farewell,
            "role": "nurse",
            "is_final": True,
        })

        # D 号审阅：对已完成的随访会话做结构化审阅（无 LLM 时走规则兜底）
        ai_review = await summarize_session(
            session={"session_id": "<pending>", "risk_result": risk_result},
            transcripts=self.conversations.get(patient_id, []),
            plan=None,
        )
        # 同日去重：若该患者今天已有会话记录，则复用并更新，避免「记录两次」
        existing = get_today_session(patient_id)
        if existing:
            session_id = existing["session_id"]
            update_session(
                session_id,
                transcript_json=self.conversations[patient_id],
                risk_result=risk_result,
                agent_summary="患者已完成本次微信随访，系统已生成风险评分。",
            )
            exist_review = get_review_by_session(session_id)
            if exist_review:
                attach_ai_review(exist_review["review_id"], ai_review)
            else:
                create_review(
                    session_id=session_id,
                    patient_id=patient_id,
                    track_status="pending_track",
                    audit_snapshot={"source": "followup_service", "risk_result": risk_result,
                                    "ai_review": ai_review},
                )
        else:
            session_id = create_session(
                patient_id=patient_id,
                transcript_json=self.conversations[patient_id],
                risk_result=risk_result,
                agent_summary="患者已完成本次微信随访，系统已生成风险评分。",
            )
            create_review(
                session_id=session_id,
                patient_id=patient_id,
                track_status="pending_track",
                audit_snapshot={"source": "followup_service", "risk_result": risk_result,
                                "ai_review": ai_review},
            )
        self.event_bus.review_ready({
            "session_id": session_id,
            "patient_id": patient_id,
            "patient_name": patient.get("name", ""),
            "risk_result": risk_result,
        })
        # 手动演示患者走完 → 记录到已完成集合，便于最终统计在所有演示结束后下发
        if patient_id in self.demo_patient_ids:
            self.demo_done_ids.add(patient_id)
        # 清除 ReACT 状态，防止 farewell 之后再次 on_patient_reply 重新进入追问循环
        self._react_states.pop(patient_id, None)
        self.transition_to("IDLE")
        self.event_bus.stats(self.stats)
        self._maybe_emit_final_stats()
        return {
            "status": "completed",
            "patient_id": patient_id,
            "parse_result": parsed,
            "risk_result": risk_result,
            "session_id": session_id,
        }

    async def run_agent_session(self, patient_id: str, plan_id: str | None = None) -> dict[str, Any]:
        """显式调用新版 Execution Agent，用于 REST 或后续 WebSocket 切换。"""
        from agents.execution import run_session

        patient = self.db.get_patient(patient_id)
        if not patient:
            return {"status": "not_found", "patient_id": patient_id}
        result = await run_session(patient_id, plan_id=plan_id)
        return {"status": "completed", **result}

    def trigger_exception(self, error_type: str = "manual") -> None:
        self.transition_to("ERROR")
        self.event_bus.log(f"模拟异常：{error_type}", level="warning")

    async def _react_reflect(self, patient: dict[str, Any], react_state: dict) -> dict:
        """V9.1: 使用 function-calling 工具进行 ReACT 反思决策。

        LLM 持工具（parse/history/risk/escalate/finalize）自主决策，
        一次调用完成解析+决策+追问生成（替代旧版 JSON prompt）。

        Args:
            patient: 患者完整信息
            react_state: 当前 ReACT 状态，含 round、info_inventory、history

        Returns:
            LLM 决策字典（对齐旧版字段 + tool 新增 parsed/risk_result/finalize_summary）。
        """
        patient_id = patient["patient_id"]
        patient_name = patient.get("name", patient_id)

        # 构建对话历史
        conversation = []
        conv = self.conversations.get(patient_id, [])
        for m in conv:
            conversation.append({"role": m["role"], "content": m["content"]})

        inventory = react_state.get("info_inventory", {})

        # V9.1: 用 function-calling 工具替代旧 JSON prompt
        decision = await run_tool_reflect(
            patient, conversation, inventory,
            fallback_fn=lambda: self._react_fallback(patient_id, react_state),
        )

        # 更新对话历史
        if decision.get("next_question"):
            react_state.setdefault("history", []).append(
                {"role": "nurse", "content": decision["next_question"]}
            )

        # 从 tool 调用结果中合并解析/评分信息到 react_state
        parsed = decision.get("parsed") or {}
        if parsed.get("nrs_score") is not None:
            inventory["pain_nrs"] = parsed["nrs_score"]
        if parsed.get("sleep_quality") is not None:
            inventory["sleep_quality"] = parsed["sleep_quality"]
        if parsed.get("medication_taken") is not None:
            inventory["medication_taken"] = bool(parsed["medication_taken"])
        if parsed.get("side_effects") is not None:
            inventory["side_effects"] = parsed["side_effects"]
        react_state["info_inventory"] = inventory

        # 兜底：LLM 未调 parse 时的关键词解析
        if not parsed:
            last_reply = self._last_patient_reply(conversation)
            if last_reply:
                try:
                    kp = self.reply_parser._keyword_parse(last_reply) or {}
                except Exception:
                    kp = {}
                if kp.get("nrs_score") is not None:
                    inventory["pain_nrs"] = kp["nrs_score"]
                if kp.get("sleep_quality") is not None:
                    inventory["sleep_quality"] = kp["sleep_quality"]
                if kp.get("medication_taken") is not None:
                    inventory["medication_taken"] = bool(kp["medication_taken"])
                react_state["info_inventory"] = inventory



        # 缓存工具产出的风险评分，供最终结束路径复用（避免重复计算）
        if decision.get("risk_result"):
            react_state["_tool_risk_result"] = decision["risk_result"]
        if parsed:
            react_state["_tool_parsed"] = parsed

        # 强制轮次上限兜底
        if react_state["round"] >= 19:
            decision["should_end"] = True

        return decision

    def _react_fallback(self, patient_id: str, react_state: dict) -> dict:
        """ReACT 降级决策：基于上一轮对话简单判断。"""
        rnd = react_state.get("round", 0)
        if rnd >= 3:
            return {"should_end": True, "reasoning": "降级达到轮次上限",
                    "info_inventory": react_state.get("info_inventory", {}),
                    "missing": [], "next_action": "end", "next_question": ""}
        # 降级时给出简单追问
        return {
            "should_end": False,
            "reasoning": f"降级决策 r{rnd}",
            "info_inventory": react_state.get("info_inventory", {}),
            "missing": ["pain_nrs", "sleep_quality", "medication_taken", "side_effects"][rnd:],
            "next_action": f"ask_{['pain','sleep','medication','side_effects'][rnd-1]}",
            "next_question": [
                "疼痛大概几分呀？(0-10)",
                "昨晚睡得怎么样？",
                "药按时吃了吗？",
                "有没有恶心、头晕这些不舒服？"
            ][rnd - 1] if rnd >= 1 else "今天感觉怎么样？",
        }

    @staticmethod
    def _fallback_question(rnd: int) -> str:
        """ReACT 未给出追问内容时的兜底问题，按轮次轮换四项关键信息。"""
        questions = [
            "那今天疼痛具体是几分呀（0-10分）？",
            "昨晚睡得怎么样，有没有因为疼醒或者睡不好？",
            "最近药都按时吃了吗？",
            "有没有恶心、头晕、便秘这些不舒服的地方？",
        ]
        return questions[(max(rnd, 1) - 1) % len(questions)]

    @staticmethod
    async def _missing_question(missing: list, patient: dict) -> str:
        """护栏触发时，让 LLM 为缺失项生成追问。"""
        return await target_question(missing, patient)


    @staticmethod
    def _last_patient_reply(conversation: list) -> str:
        """从对话历史中取最新一条患者回复。"""
        for m in reversed(conversation):
            if m.get("role") == "patient":
                return m.get("content", "")
        return ""

    def reset(self) -> None:
        self.conversations.clear()
        self._react_states.clear()
        self.reset_stats()
        self.demo_patient_ids = set()
        self.pending_auto = 0
        self.demo_done_ids = set()
        self.final_emitted = False
        self._patient_risk_records.clear()
        self._sent_patient_ids = set()
        self.transition_to("IDLE")
        self.event_bus.stats(self.stats)
        self.event_bus.log("演示状态已重置")

    async def _auto_run_patient(self, patient: dict[str, Any]) -> None:
        """后台自动完成「非演示患者」的整条随访（对话 + 风险评估 + 审阅）。

        通过编排器 run_followup_pipeline 串起 B→C→D：C 号执行 Agent 用 AutoReplyEngine
        自动生成患者回复并完成 Goal-Driven 对话，再自动做 D 号总结并落库。此路径不占用
        演示通道，也不逐条广播对话（前端仅展示手动演示患者的会话）。

        本方法作为独立 asyncio.Task 运行，多位患者的流水线在事件循环上并发推进。
        """
        pid = patient.get("patient_id", "?")
        name = patient.get("name", pid)
        try:
            self.event_bus.log(f"[auto] 开始自动随访 {name}({pid})…")
            from agents.orchestrator import run_followup_pipeline
            ctx = await run_followup_pipeline(patient["patient_id"])
            if ctx.get("early_return"):
                self.event_bus.log(f"⏭ {patient.get('name', '')} 今日无需随访，跳过自动随访")
                return
            c_session = ctx.get("c_session") or {}
            risk_result = c_session.get("risk_result", {}) or {}
            self.stats["replied_count"] += 1
            self._accumulate_risk(risk_result)
            self._record_patient_result(patient, risk_result)
            self._emit_risk_events(patient, risk_result)
            # 推送「待审阅」提示到医生屏（与手动演示路径一致）
            self.event_bus.review_ready({
                "session_id": c_session.get("session_id"),
                "patient_id": patient.get("patient_id"),
                "patient_name": patient.get("name", ""),
                "risk_result": risk_result,
            })
            if self.risk_engine.is_emergency(risk_result):
                self.stats["alert_count"] += 1
                self.event_bus.publish("doctor:alert", {
                    "patient_id": patient.get("patient_id"),
                    "patient_name": patient.get("name", ""),
                    "risk_level": risk_result.get("level", "high"),
                    "risk_result": risk_result,
                    "message": f"{patient.get('name', '')} 触发高风险预警",
                })
            self.event_bus.log(
                f"✓ 自动完成 {patient.get('name', '')} 随访"
                f"（风险：{risk_result.get('level_label') or risk_result.get('level', 'unknown')}）"
            )
        except Exception as exc:
            log.exception("自动随访失败 %s: %s", patient.get("patient_id"), exc)
            # 兜底：即使流水线崩溃，也在风险评分列表中标记该患者
            self.event_bus.publish("doctor:callback_alert", {
                "patient_id": patient.get("patient_id"),
                "patient_name": patient.get("name", ""),
                "type": "no_reply",
                "risk_level": "callback",
                "total_score": None,
                "reason": f"自动随访异常：{exc}",
            })
            self.stats["no_reply_count"] += 1
        finally:
            async with self._pending_auto_lock:
                self.pending_auto = max(0, self.pending_auto - 1)
            self.event_bus.stats(self.stats)
            # 自动患者全部结束（且手动演示已完成/无手动演示）后再出最终统计
            self._maybe_emit_final_stats()

    def _emit_risk_events(self, patient: dict[str, Any], risk_result: dict[str, Any]) -> None:
        """下发前端看板需要的风险事件（agent:risk_scored）。"""
        details = risk_result.get("details") or {}
        self.event_bus.publish("agent:risk_scored", {
            "patient_id": patient.get("patient_id"),
            "patient_name": patient.get("name", ""),
            "doctor_name": patient.get("doctor_name", ""),
            "level": risk_result.get("level"),
            "total_score": risk_result.get("total_score"),
            "nrs": risk_result.get("nrs_score") or details.get("nrs_score"),
            "reason": risk_result.get("reason", ""),
            "risk_result": risk_result,
        })

    def _maybe_emit_final_stats(self) -> None:
        """最终统计仅在「自动患者全部结束 + 手动演示患者全部走完（或无手动演示）」后下发一次。

        自动演示与手动演示互不阻塞：任一方先结束都先等待另一方，直到两类患者都完成才统计。
        """
        if self.final_emitted:
            return
        if self.pending_auto > 0:
            self.event_bus.log(f"[统计等待] 自动患者未全部完成 (pending_auto={self.pending_auto})")
            return
        if self.demo_patient_ids and not self.demo_patient_ids.issubset(self.demo_done_ids):
            self.event_bus.log(
                f"[统计等待] 手动演示患者未全部完成 "
                f"(demo={self.demo_patient_ids} done={self.demo_done_ids})"
            )
            return
        self.final_emitted = True

        # ---- 构建前端 StatsPanel 所需的完整统计对象 ----
        records = self._patient_risk_records
        high_risk_focus = sorted(
            [r for r in records if (r.get("score") or 0) >= 8],
            key=lambda r: r.get("score", 0), reverse=True
        )
        low_risk_exempt = sorted(
            [r for r in records if r.get("score") is not None and (r.get("score") or 0) <= 1],
            key=lambda r: r.get("score", 0)
        )
        # 未回复患者：已发消息但不在风险记录中（本轮无回复）
        replied_ids = {r["patient_id"] for r in records}
        no_reply_patients = []
        seen_no_reply = set()
        for pid in self._sent_patient_ids:
            if pid not in replied_ids:
                p = self.db.get_patient(pid) or {}
                no_reply_patients.append({
                    "patient_id": pid,
                    "name": p.get("name", pid),
                    "diagnosis": p.get("diagnosis", ""),
                    "score": None,
                })
                seen_no_reply.add(pid)

        # 补充 DB 中"连续 3 天未回复"的患者（即使 AutoReplyEngine 本轮模拟了回复，
        # 也应出现在"三日未回复"名单中，反映真实风险状态）
        for p in self.patients:
            pid = p.get("patient_id", "")
            if pid in seen_no_reply:
                continue
            if int(p.get("consecutive_no_reply_days") or 0) >= 3:
                # 该患者本轮虽被模拟了回复，但历史数据表明已连续失联
                no_reply_patients.append({
                    "patient_id": pid,
                    "name": p.get("name", pid),
                    "diagnosis": p.get("diagnosis", ""),
                    "score": None,
                })
                seen_no_reply.add(pid)

        # 未回复数 = 本轮未回复 + 历史三日未回复（去重）
        self.stats["no_reply_count"] = len(no_reply_patients)
        # 需回访 = 高风险(≥8分) + 三日未回复
        self.stats["callback_count"] = (self.stats.get("callback_count", 0)
                                        + len(no_reply_patients))

        final_stats = dict(self.stats)
        final_stats.update({
            "total_followup": self.stats.get("send_count", self.stats.get("sent_count", 0)),
            "total_assessed": self.stats.get("total_assessed", len(records)),
            "no_reply_count": self.stats["no_reply_count"],
            "high_risk_focus": high_risk_focus,
            "low_risk_exempt": low_risk_exempt,
            "no_reply_patients": no_reply_patients,
        })

        self.event_bus.publish("agent:final_stats", final_stats)
        self.event_bus.log(
            f"全部随访已完成，已生成最终统计: "
            f"应访{final_stats['total_followup']}人 "
            f"已评{final_stats['total_assessed']}人 "
            f"高危{final_stats['high_risk_count']}人 "
            f"未回{final_stats['no_reply_count']}人"
        )

    def _accumulate_risk(self, risk_result: dict[str, Any]) -> None:
        level = risk_result.get("level")
        if level == "high":
            self.stats["high_risk_count"] += 1
        elif level == "medium":
            self.stats["medium_risk_count"] += 1
        else:
            self.stats["low_risk_count"] += 1
        self.stats["total_assessed"] += 1

    def _record_patient_result(self, patient: dict[str, Any],
                                risk_result: dict[str, Any],
                                parsed: dict[str, Any] | None = None) -> None:
        """记录每位患者的随访明细，供最终统计构建 high_risk_focus 等列表。"""
        pid = patient.get("patient_id", "")
        name = patient.get("name", pid)
        diag = patient.get("diagnosis", "")
        level = risk_result.get("level", "low")
        score = risk_result.get("total_score",
                              risk_result.get("nrs_score",
                                              parsed.get("nrs_score") if parsed else None))
        # 有高风险预警 → 回访计数
        is_alert = risk_result.get("alert", False) or level == "high"
        self._patient_risk_records.append({
            "patient_id": pid,
            "name": name,
            "diagnosis": diag,
            "level": level,
            "score": score if score is not None else 0,
            "phone_callback": is_alert,
        })
        if is_alert:
            self.stats["callback_count"] += 1

    @staticmethod
    def _patient_brief(patient: dict[str, Any]) -> dict[str, Any]:
        return {
            "patient_id": patient.get("patient_id"),
            "name": patient.get("name"),
            "age": patient.get("age"),
            "diagnosis": patient.get("diagnosis"),
            "doctor_name": patient.get("doctor_name", ""),
        }
