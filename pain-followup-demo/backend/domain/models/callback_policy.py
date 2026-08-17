# backend/domain/models/callback_policy.py
"""电话回访策略模型（说明书 5）。

自然语言只编译为电话回访补充动作策略，不得表达应访/免访名单或患者 ID 列表。
字段白名单：consecutive_no_reply_days / last_reply_at / channel。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# PolicyCompiler 允许生成的条件字段白名单
CONDITION_FIELD_WHITELIST: frozenset[str] = frozenset({
    "consecutive_no_reply_days", "last_reply_at",
})

# 允许的比较运算符
ALLOWED_OPS: frozenset[str] = frozenset({"gte", "gt", "lte", "lt", "eq"})


class CallbackCondition(BaseModel):
    """单个电话回访命中条件。"""
    field: Literal["consecutive_no_reply_days", "last_reply_at"]
    op: Literal["gte", "gt", "lte", "lt", "eq"]
    value: int | str

    model_config = {"extra": "ignore"}

    @field_validator("field")
    @classmethod
    def _check_field(cls, v: str) -> str:
        if v not in CONDITION_FIELD_WHITELIST:
            raise ValueError(f"非白名单字段: {v}")
        return v


class CallbackAction(BaseModel):
    """命中后执行的补充动作。仅允许电话回访标记。"""
    channel: Literal["phone", "wechat", "app"] = "phone"
    phone_callback: bool = True

    model_config = {"extra": "ignore"}


class CallbackPolicyDraft(BaseModel):
    """PolicyCompiler Agent 产出的候选策略（未经医护确认）。"""
    policy_type: Literal["phone_callback"] = "phone_callback"
    source_text: str = ""
    eligible_scope: Literal["base_roster.send_only"] = "base_roster.send_only"
    conditions: list[CallbackCondition] = Field(default_factory=list)
    action: CallbackAction = Field(default_factory=CallbackAction)
    unrecognized_lines: list[str] = Field(default_factory=list)
    version: str = ""

    model_config = {"extra": "ignore"}


class CallbackPolicy(CallbackPolicyDraft):
    """已校验、待确认的电话回访策略（含命中预览信息）。"""
    policy_date: str = ""
    hit_count: int = 0
    hit_patients: list[dict[str, Any]] = Field(default_factory=list)
    hash: str = ""

    model_config = {"extra": "ignore"}


class CallbackPolicySnapshot(BaseModel):
    """医护确认后冻结的不可变策略快照。"""
    policy_version: str
    raw_text: str
    compiled_json: dict[str, Any]
    hash: str
    approved_by: str = ""
    approved_at: str = ""
    status: Literal["draft", "approved", "rejected"] = "approved"

    model_config = {"extra": "ignore"}


def evaluate_callback_policy(policy: CallbackPolicy,
                             send_roster: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    """在基础应访名单上执行电话回访条件，返回命中人数与明细。

    不得把免访患者重新纳入。命中即标记 phone_callback=true。
    """
    hits: list[dict[str, Any]] = []
    for p in send_roster:
        for cond in policy.conditions:
            if _match_condition(cond, p):
                hits.append({
                    "patient_id": p.get("patient_id"),
                    "name": p.get("name", ""),
                    "matched_field": cond.field,
                    "op": cond.op,
                    "value": cond.value,
                    "actual": _extract_field(cond.field, p),
                    "channel": policy.action.channel,
                })
                break
    return len(hits), hits


def _extract_field(field: str, patient: dict[str, Any]):
    return patient.get(field)


def _match_condition(cond: CallbackCondition, patient: dict[str, Any]) -> bool:
    actual = _extract_field(cond.field, patient)
    if actual is None:
        return False
    try:
        if cond.op == "gte":
            return float(actual) >= float(cond.value)
        if cond.op == "gt":
            return float(actual) > float(cond.value)
        if cond.op == "lte":
            return float(actual) <= float(cond.value)
        if cond.op == "lt":
            return float(actual) < float(cond.value)
        if cond.op == "eq":
            return str(actual) == str(cond.value)
    except (TypeError, ValueError):
        return False
    return False
