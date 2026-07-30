"""
Function-Calling 工具定义（替代旧 ToolDispatcher）。

每个工具包含 OpenAI 工具 schema + 对应的异步 handler。
handler 签名 = async def handler(**kwargs) -> dict，返回结构化结果。
"""

from __future__ import annotations

from typing import Any

# =============================================
# OpenAI Tool Schemas（传入 ChatOpenAI.bind_tools）
# =============================================

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "parse_patient_reply",
            "description": "从患者回复提取结构化数据（NRS评分、睡眠质量、用药依从性、副作用）。"
                           "每次患者回复后都应优先调用此工具提取结构化信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reply_text": {
                        "type": "string",
                        "description": "患者本次回复的完整原始文本"
                    }
                },
                "required": ["reply_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_patient_history",
            "description": "查询患者近期的历史随访记录，用于了解疼痛趋势和既往状况。"
                           "当需要评估患者变化趋势或补充背景信息时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {
                        "type": "string",
                        "description": "患者ID"
                    },
                    "days": {
                        "type": "integer",
                        "description": "查询最近N天的记录，默认7天"
                    }
                },
                "required": ["patient_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_risk_score",
            "description": "根据今日采集的疼痛数据计算患者的风险评分（0-20分），并返回风险等级。"
                           "当已收集到足够的今日数据（至少NRS评分）时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {
                        "type": "string",
                        "description": "患者ID"
                    },
                    "nrs_score": {
                        "type": "integer",
                        "description": "今日NRS疼痛评分(0-10)"
                    },
                    "sleep_quality": {
                        "type": "string",
                        "description": "睡眠质量描述（好/一般/差/很差）"
                    },
                    "medication_taken": {
                        "type": "boolean",
                        "description": "是否按时服药"
                    }
                },
                "required": ["patient_id", "nrs_score"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_alert",
            "description": "高风险患者触发医生预警。当风险评分>=8分或有紧急情况时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_id": {
                        "type": "string",
                        "description": "患者ID"
                    },
                    "risk_level": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "风险等级"
                    },
                    "reason": {
                        "type": "string",
                        "description": "预警原因摘要"
                    }
                },
                "required": ["patient_id", "risk_level", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_followup",
            "description": "结束本次随访。当疼痛(NRS)、睡眠、用药、副作用四项信息全部收集完毕，"
                           "或患者明确表示要结束对话时调用。调用后不再追问。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "本次随访的简要总结（包括患者状态、风险等级、后续建议）"
                    },
                    "nrs_collected": {"type": "boolean"},
                    "sleep_collected": {"type": "boolean"},
                    "medication_collected": {"type": "boolean"},
                    "side_effects_collected": {"type": "boolean"},
                },
                "required": ["summary"]
            }
        }
    },
]


# =============================================
# Tool Handler 依赖（由调用方在注册时注入）
# =============================================

_registry: dict[str, Any] = {
    "reply_parser": None,
    "risk_engine": None,
    "db": None,
    "patients": None,
    "event_bus": None,
}


def register_handlers(
    reply_parser: Any = None,
    risk_engine: Any = None,
    db: Any = None,
    patients: list[dict[str, Any]] | None = None,
    event_bus: Any = None,
) -> None:
    """注册工具 handler 所需的依赖。在 bootstrap 阶段调用一次。"""
    _registry["reply_parser"] = reply_parser
    _registry["risk_engine"] = risk_engine
    _registry["db"] = db
    _registry["patients"] = patients
    _registry["event_bus"] = event_bus


# =============================================
# Tool Handlers（纯函数，无副作用除 escalate_alert）
# =============================================

async def _parse_patient_reply(reply_text: str) -> dict:
    """工具: parse_patient_reply — 提取结构化数据"""
    parser = _registry["reply_parser"]
    if parser is None:
        return {"nrs_score": None, "sleep_quality": None, "medication_taken": None,
                "side_effects": None, "raw_text": reply_text}
    try:
        parsed = await parser.parse(reply_text, "")
    except Exception:
        try:
            parsed = parser._keyword_parse(reply_text)
        except Exception:
            parsed = {"nrs_score": None, "sleep_quality": None, "medication_taken": None}
    return {
        "nrs_score": parsed.get("nrs_score"),
        "sleep_quality": parsed.get("sleep_quality"),
        "medication_taken": parsed.get("medication_taken"),
        "side_effects": parsed.get("side_effects"),
        "raw_text": reply_text,
    }


async def _query_patient_history(patient_id: str, days: int = 7) -> dict:
    """工具: query_patient_history — 查询历史记录"""
    db = _registry["db"]
    if db is None:
        return {"patient_id": patient_id, "history": [], "record_count": 0}
    try:
        history = db.get_history(patient_id, days)
    except Exception:
        history = []
    return {
        "patient_id": patient_id,
        "days": days,
        "history": history,
        "record_count": len(history),
    }


async def _calculate_risk_score(
    patient_id: str,
    nrs_score: int,
    sleep_quality: str | None = None,
    medication_taken: bool | None = None,
) -> dict:
    """工具: calculate_risk_score — 风险评分"""
    engine = _registry["risk_engine"]
    patients = _registry["patients"] or []
    patient = next((p for p in patients if p.get("patient_id") == patient_id), {})

    if engine is None:
        return {"total_score": None, "level": "unknown", "level_label": "未知",
                "error": "风险引擎未初始化"}

    today_data = {
        "nrs": nrs_score,
        "sleep_quality": sleep_quality,
        "medication_taken": medication_taken,
    }
    try:
        history = patient.get("history", [])[-7:]
        result = engine.calculate(patient_id, patient, today_data, history)
    except Exception as e:
        result = {"total_score": None, "level": "unknown", "level_label": "未知",
                  "error": str(e)}
    return {
        "patient_id": patient_id,
        "total_score": result.get("total_score"),
        "level": result.get("level"),
        "level_label": result.get("level_label", ""),
        "base_score": result.get("base_score"),
        "dynamic_score": result.get("dynamic_score"),
    }


async def _escalate_alert(patient_id: str, risk_level: str, reason: str) -> dict:
    """工具: escalate_alert — 推送预警（带事件推送副作用）"""
    bus = _registry["event_bus"]
    if bus:
        try:
            bus.publish("doctor:alert", {
                "patient_id": patient_id,
                "risk_level": risk_level,
                "reason": reason,
            })
        except Exception:
            pass
    return {"success": True, "patient_id": patient_id,
            "risk_level": risk_level, "reason": reason}


async def _finalize_followup(summary: str, nrs_collected: bool = False,
                              sleep_collected: bool = False,
                              medication_collected: bool = False,
                              side_effects_collected: bool = False) -> dict:
    """工具: finalize_followup — 结束随访"""
    return {
        "finalized": True,
        "summary": summary,
        "items_collected": {
            "nrs": nrs_collected,
            "sleep": sleep_collected,
            "medication": medication_collected,
            "side_effects": side_effects_collected,
        }
    }


# =============================================
# Handler Map：tool_name → async handler
# =============================================

HANDLER_MAP: dict[str, Any] = {
    "parse_patient_reply": _parse_patient_reply,
    "query_patient_history": _query_patient_history,
    "calculate_risk_score": _calculate_risk_score,
    "escalate_alert": _escalate_alert,
    "finalize_followup": _finalize_followup,
}
