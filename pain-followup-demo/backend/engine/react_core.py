"""
B/C/D 共享 ReACT 内核（非 Agent，纯处理引擎）

把 execution.py 与 followup_service.py 中重复的「LLM解析+追问生成」
纯逻辑抽取到此处，供两条路径共用。

V9.1: ReACT 循环改为 function-calling 驱动——LLM 持工具自行解析回复、
评估风险、生成追问，一次调用替代原来的 reflect_decision + generate_nurse_question。

设计约束：
  - 不在此处做事件推送副作用。
  - 降级函数 fallback_fn 由调用方注入。
  - chat() 异常向上抛。
"""
import json
import traceback
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

from llm.client import is_llm_available, build_tool_model
from engine.tool_definitions import TOOL_SCHEMAS
from engine.tool_executor import execute_tool
from prompts.react_nurse import TOOL_NURSE_SYSTEM
from core.logging_config import get_logger

log = get_logger("painsmart.react_engine")


def _conv_to_lc(conversation: list[dict]) -> list:
    """将对话历史 dict 列表转为 LangChain message 列表。"""
    out = []
    for m in conversation:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role in ("nurse", "assistant"):
            out.append(AIMessage(content=content))
        elif role == "system":
            out.append(SystemMessage(content=content))
        else:
            out.append(HumanMessage(content=content))
    return out


async def run_tool_reflect(
    patient: dict,
    conversation: list[dict],
    info_inventory: dict,
    fallback_fn,
    *,
    previous_tool_results: dict | None = None,
) -> dict:
    """ReACT 单轮决策：LLM 持工具自行解析回复、评估风险、决定是否结束。

    一次 LLM 调：解析 → 算风险（可选） → 预警（可选） → 结束/追问。

    Args:
        patient: 患者字典
        conversation: 完整对话历史
        info_inventory: 已知信息清单
        fallback_fn: 无 LLM 时的降级函数
        previous_tool_results: 上一轮已缓存的工具结果

    Returns:
        dict: {should_end, next_question, reasoning, info_inventory, missing,
               next_action, finalize_summary, parsed, risk_result, tool_calls}
    """
    if not is_llm_available():
        return fallback_fn()

    model = build_tool_model(TOOL_SCHEMAS, temperature=0.3)
    if model is None:
        return fallback_fn()

    name = patient.get("name", "患者")
    risk_hint = patient.get("risk_level", "medium_risk")

    try:
        lc_msgs = [SystemMessage(content=TOOL_NURSE_SYSTEM)]

        context_lines = [f"患者：{name}", f"预估风险等级：{risk_hint}"]
        diag = patient.get("diagnosis", "")
        if diag:
            context_lines.append(f"诊断：{diag}")
        known = []
        for k, v in (info_inventory or {}).items():
            if v is not None:
                known.append(f"{k}={v}")
        if known:
            context_lines.append(f"已收集信息：{', '.join(known)}")
        lc_msgs.append(HumanMessage(content="\n".join(context_lines)))

        conv_lc = _conv_to_lc(conversation)
        lc_msgs.extend(conv_lc[-20:])

        response = await model.ainvoke(lc_msgs)

        parsed_result = None
        risk_result = None
        finalize_summary = None
        tool_calls = []
        tool_results_cache = dict(previous_tool_results or {})

        if hasattr(response, "tool_calls") and response.tool_calls:
            tool_calls = list(response.tool_calls)
            lc_msgs.append(response)

            for tc in response.tool_calls:
                t_name = tc.get("name", "")
                t_args = tc.get("args", {})

                cache_key = f"{t_name}:{json.dumps(t_args, sort_keys=True, ensure_ascii=False)}"
                if cache_key in tool_results_cache:
                    result_str = tool_results_cache[cache_key]
                else:
                    result_str = await execute_tool(t_name, t_args)
                    tool_results_cache[cache_key] = result_str

                lc_msgs.append(ToolMessage(
                    content=result_str,
                    tool_call_id=tc.get("id", ""),
                ))

                try:
                    r = json.loads(result_str)
                except json.JSONDecodeError:
                    r = {}

                if t_name == "parse_patient_reply":
                    parsed_result = r
                elif t_name == "calculate_risk_score":
                    risk_result = r
                elif t_name == "finalize_followup":
                    finalize_summary = r.get("summary", "")

            response = await model.ainvoke(lc_msgs)

        next_question = ""
        if hasattr(response, "content") and response.content:
            next_question = response.content.strip()

        should_end = bool(finalize_summary) or not next_question

        return {
            "should_end": should_end,
            "next_question": next_question or "您方便再具体说说吗？",
            "reasoning": f"ReACT turn: tools={[t.get('name') for t in tool_calls]}",
            "info_inventory": info_inventory,
            "missing": [],
            "next_action": "end" if should_end else "continue",
            "finalize_summary": finalize_summary,
            "parsed": parsed_result,
            "risk_result": risk_result,
            "tool_calls": tool_calls,
        }

    except Exception:
        log.warning("run_tool_reflect 失败，降级: %s", traceback.format_exc())
        return fallback_fn()


async def target_question(missing_items: list, patient: dict = None,
                          conversation: list = None) -> str:
    """护栏触发时，让 LLM 为缺失项生成自然的追问（保持语气连贯）。

    硬编码仅作 LLM 不可用时的降级兜底，不是主路径。
    """
    if not is_llm_available():
        return _hardcoded_question(missing_items, patient)

    name = (patient or {}).get("name", "患者")
    missing_labels = {
        "pain_nrs": "疼痛评分",
        "sleep_quality": "睡眠质量",
        "medication_taken": "用药依从性",
        "side_effects": "副作用",
    }
    items_str = "、".join(missing_labels.get(k, k) for k in missing_items)

    ctx = ""
    if conversation:
        last = conversation[-4:]
        ctx = "\n".join(f"{'护士' if m['role']=='nurse' else '患者'}：{m['content']}"
                        for m in last)

    from llm.client import chat
    try:
        raw = await chat(
            messages=[{
                "role": "system",
                "content": f"你是疼痛随访护士。对话中以下信息还没收集：{items_str}。"
                           f"请生成一句自然的追问，只问其中1个最优先的缺失项。"
                           f"语气亲切口语化，像微信聊天。只输出追问文本，不要任何前缀。"
            }, {
                "role": "user",
                "content": f"患者：{name}\n{ctx}\n\n请针对缺失项{items_str}生成追问："
            }],
            temperature=0.5,
        )
        text = (raw or "").strip()
        if text:
            return text
    except Exception:
        pass

    return _hardcoded_question(missing_items, patient)


def _hardcoded_question(missing_items: list, patient: dict = None) -> str:
    """LLM 不可用时的纯规则降级。"""
    name = (patient or {}).get("name", "")
    name_prefix = f"{name}，" if name else ""
    targeted = {
        "pain_nrs":        f"{name_prefix}方便给今天的疼痛打个分吗（0-10分）？",
        "sleep_quality":   f"{name_prefix}昨晚睡得好吗？有没有因为疼醒或者睡不好？",
        "medication_taken": f"{name_prefix}今天的药按时吃了吗？",
        "side_effects":    f"{name_prefix}最近有没有恶心、头晕或者哪里不舒服吗？",
    }
    for k in missing_items:
        if k in targeted:
            return targeted[k]
    return f"{name_prefix}您再说说其他情况呀～"
