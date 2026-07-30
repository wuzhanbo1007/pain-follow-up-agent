"""
随访会话内容总结（原 D 号 followup session reviewer，非 Agent）

职责：对已完成的随访会话做 LLM 智能总结，输出结构化结果：
    {"summary", "risk_flags": [str], "completion_score": int(0-100),
     "suggestions": [str], "source": "llm"|"rule"}

设计（与既有"无 LLM 时规则/模板兜底"风格一致）：
  - 主路径：构造 prompt 调 LLM（response_format=json_object）输出上述 JSON；
    LLM 不可用或解析失败时，回退到基于 transcripts 条数 / 风险等级的简单规则版。
  - 不修改既有表结构：结构化结果经调用方序列化进 review 表的 audit_snapshot 字段。
"""
import json

from llm.client import chat, is_llm_available
from prompts.review_analysis import build_prompt as build_review_prompt


async def summarize_session(session: dict, transcripts: list, plan: dict | None = None) -> dict:
    """对已完成的随访会话做结构化总结。

    Args:
        session: 会话字典（含 risk_result / agent_summary 等）
        transcripts: 对话记录 [{"role": "nurse"/"patient", "content": ...}]
        plan:   随访计划（可选，暂未用于规则兜底）
    Returns:
        dict: {summary, risk_flags, completion_score, suggestions, source}
    """
    rule_result = _rule_summary(session, transcripts)

    if is_llm_available():
        try:
            raw = await chat(
                messages=[{"role": "user", "content": build_review_prompt(session, transcripts, plan)}],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            out = _safe_json(raw)
            if isinstance(out, dict) and out.get("summary"):
                return {
                    "summary": out.get("summary", rule_result["summary"]),
                    "risk_flags": out.get("risk_flags", rule_result["risk_flags"]) or [],
                    "completion_score": _clamp_score(out.get("completion_score", rule_result["completion_score"])),
                    "suggestions": out.get("suggestions", rule_result["suggestions"]) or [],
                    "source": "llm",
                }
        except Exception:
            pass

    return {**rule_result, "source": "rule"}


def _rule_summary(session: dict, transcripts: list) -> dict:
    """规则兜底版总结：基于对话条数与风险等级给出简单结论。"""
    risk_result = session.get("risk_result", {}) or {}
    risk_level = (risk_result.get("level_label") or risk_result.get("level") or "未知")
    high_risk = risk_level in ("高风险", "high_risk", "high")

    count = len(transcripts) if isinstance(transcripts, list) else 0

    # 完成度评分：有对话且覆盖多轮则较高；高风险扣减
    score = 50
    if count >= 2:
        score += 20
    if count >= 4:
        score += 10
    if high_risk:
        score = max(0, score - 20)
    score = max(0, min(100, score))

    risk_flags = []
    if high_risk:
        risk_flags.append("高风险：需医生重点关注")
    # 高疼痛评分关键词检测
    for m in (transcripts or []):
        if m.get("role") == "patient" and any(k in m.get("content", "") for k in ("疼得厉害", "受不了", "8分", "9分", "10分", "爆")):
            risk_flags.append("患者自述疼痛剧烈")
            break

    suggestions = ["按常规周期持续随访"]
    if high_risk:
        suggestions = ["建议医生尽快介入评估", "确认镇痛方案是否需要调整"]

    summary = f"共 {count} 条对话；风险等级：{risk_level}（规则兜底总结，未调用 LLM）"
    return {
        "summary": summary,
        "risk_flags": risk_flags,
        "completion_score": score,
        "suggestions": suggestions,
    }


def _clamp_score(v) -> int:
    try:
        s = int(v)
    except (TypeError, ValueError):
        return 50
    return max(0, min(100, s))


def _safe_json(raw: str) -> dict:
    try:
        text = raw.strip()
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1]
                if text.startswith("json"):
                    text = text[4:]
        return json.loads(text.strip())
    except Exception:
        return {}
