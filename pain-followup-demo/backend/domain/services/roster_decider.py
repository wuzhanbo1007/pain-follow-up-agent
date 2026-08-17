# backend/domain/services/roster_decider.py
"""RosterDecider —— 当日名单决策器（说明书 4.2 decide_base_roster / 5.1）。

基础名单只由数据库计划到期、患者状态、授权与豁免字段计算；
电话回访（CallbackPolicy）只在应访名单上叠加 phone_callback 标记，不能把免访患者重新纳入。

实现复用 domain.services.followup_rules 的 C1~C4 判定（计划生效/窗口/频次/豁免，
自 engine.followup_scheduler 迁出，§12.2），行为与旧 build_today_send_list 一致；
C0（电话回访阈值）改由 CallbackPolicy 表达。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from domain.models.followup import DispatchRoster, RosterDecision
from domain.models.callback_policy import CallbackPolicy, evaluate_callback_policy


class RosterDecider:
    """依据业务日期 + 患者快照 + 计划规则，计算应访/免访基础名单。"""

    def __init__(self, rules_module=None):
        # rules_module: domain.services.followup_rules（可注入便于测试）
        self._rules = rules_module

    def _resolve_engine(self):
        if self._rules is not None:
            return self._rules
        from domain.services.followup_rules import (
            is_followup_due, build_today_send_list, get_today,
        )
        return self._engine_module(is_followup_due, build_today_send_list, get_today)

    class _engine_module:
        def __init__(self, is_followup_due, build_today_send_list, get_today):
            self.is_followup_due = is_followup_due
            self.build_today_send_list = build_today_send_list
            self.get_today = get_today

    def decide_base_roster(self, patients: list[dict[str, Any]],
                           *, business_date: date | None = None,
                           default_mode: str = "auto",
                           no_reply_threshold: int | None = 3) -> DispatchRoster:
        """计算基础应访/免访名单（不含电话回访）。

        复用现有规则引擎的 build_today_send_list，保证与旧版判定一致。
        每位患者落 decision_trace（mode 仅作诊断信息，不决定输入来源）。

        §6 / §11-11：RosterDecider 只决定 send_roster / skip_roster，不读取
        患者表中的 followup_mode 字段；自动/手动分流由 DispatcherAgent 按外部
        配置 manual_patient_ids 完成。
        """
        eng = self._resolve_engine()
        today = business_date or eng.get_today()
        rule_res = eng.build_today_send_list(
            patients, today=today, no_reply_threshold=no_reply_threshold)
        rule_details = rule_res.get("details", {})

        send_list: list[dict] = []
        skip_list: list[dict] = []
        details: dict[str, Any] = {}

        for p in patients:
            pid = p.get("patient_id")
            d = dict(rule_details.get(pid) or {})
            p = dict(p)
            need = bool(d.get("need_followup"))
            if need:
                p["skip_follow_up"] = False
                if d.get("matched_rule") == "电话回访":
                    p["phone_callback"] = True
                    p["channel"] = "phone"
                    d["phone_callback"] = True
                    d["channel"] = "phone"
                d.update(need_followup=True, source="rule", mode=default_mode,
                         patient_id=pid, name=p.get("name", ""),
                         reason=d.get("reason") or "规则判定应随访")
                send_list.append(p)
            else:
                p.update(skip_follow_up=True, skip_reason=d.get("reason") or "规则判定免随访")
                d.update(source="rule", patient_id=pid, name=p.get("name", ""))
                skip_list.append(p)
            details[pid] = d

        today_str = today.isoformat() if hasattr(today, "isoformat") else str(today)
        phone_callback_count = sum(
            1 for patient in send_list if patient.get("phone_callback"))
        return DispatchRoster(
            total=len(patients),
            send_list=send_list,
            skip_list=skip_list,
            details=details,
            phone_callback_count=phone_callback_count,
            today=today_str,
        )

    def apply_callback_policy(self, roster: DispatchRoster,
                              policy: CallbackPolicy | None) -> DispatchRoster:
        """在应访名单上叠加电话回访标记（不得把免访患者重新纳入）。"""
        if policy is None or not policy.conditions:
            return roster
        hit_count, hits = evaluate_callback_policy(policy, roster.send_list)
        hit_ids = {h["patient_id"] for h in hits}
        existing_ids = {
            p.get("patient_id") for p in roster.send_list
            if p.get("phone_callback")
        }
        # 回写 send_list 与 details
        new_send: list[dict] = []
        for p in roster.send_list:
            pid = p.get("patient_id")
            if pid in hit_ids:
                p = dict(p)
                p["phone_callback"] = True
                p["channel"] = policy.action.channel
            new_send.append(p)
        for pid in hit_ids:
            d = roster.details.get(pid)
            if d is not None:
                d["phone_callback"] = True
                d["channel"] = policy.action.channel
                d["matched_rule"] = "电话回访"
        roster.send_list = new_send
        roster.phone_callback_count = len(existing_ids | hit_ids)
        return roster
