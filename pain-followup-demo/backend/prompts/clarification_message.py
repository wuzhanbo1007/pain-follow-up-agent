"""模糊回复澄清追问提示词（慢性疼痛随访 V10.0）

V10.0 升级：
- 新增 missing_fields 参数，让 LLM 知道具体缺什么，避免泛泛追问
- 新增 ambiguity_type（模糊类型分类），自适应追问策略
- 新增 retry_context（追问次数字段），避免同一策略反复使用
- 语气引导从"形容词"升级为"语义行为示例"
- 边缘 case：答非所问、纯情绪、拒绝回答 —— 各有不同策略
"""

from typing import List, Optional


def build_prompt(patient: dict, history_summary: str, ambiguous_reply: str,
                 missing_fields: Optional[List[str]] = None,
                 ambiguity_type: str = "vague",
                 retry_count: int = 0) -> str:
    """构建澄清追问提示词

    Args:
        patient: 患者信息（含 name, age, diagnosis）
        history_summary: 近期历史数据摘要
        ambiguous_reply: 患者模糊回复原文
        missing_fields: 尚未收集到的字段列表（如 ["nrs_score", "sleep_quality"]）
        ambiguity_type: 模糊类型——"vague"（含糊）/ "deflect"（回避）/ "emotional"（情绪宣泄）
                        / "irrelevant"（答非所问）/ "minimal"（极简短回复如"嗯""哦"）
        retry_count: 已追问次数（0=首次追问，1=第二次追问，≥2=多次追问）

    Returns:
        完整的 prompt 字符串
    """

    # 字段中文名映射
    field_names = {
        "nrs_score": "疼痛评分",
        "sleep_quality": "睡眠质量",
        "medication_taken": "用药情况",
        "side_effects": "副作用",
    }
    missing_desc = "、".join(field_names.get(f, f) for f in (missing_fields or [])) or "信息"

    # 模糊类型 → 追问策略
    type_strategies = {
        "vague":
            "患者回复太模糊（如'还行''就那样'），无法提取具体数据。"
            "策略：用更具体的问题降低回答难度，比如不说'睡眠怎么样'而说'昨晚大概睡了几小时？中间醒了几次？'",
        "deflect":
            "患者似乎在回避回答（如'还好吧''再说吧''不知道'），可能不愿透露或觉得麻烦。"
            "策略：先共情，降低对方防御——'没关系，简单说一下就行，主要是想确保您恢复顺利'，再自然带出问题。",
        "emotional":
            "患者回复以情绪宣泄为主（如'烦死了''快疯了''受不了了'），没有提供具体信息。"
            "策略：先共情安抚——'听起来您今天不太舒服，慢慢说，我在听'，情绪平复后再温和引导具体描述。",
        "irrelevant":
            "患者回复与随访完全无关（如'今天天气不错''在吃饭'）。"
            "策略：不质疑对方的回复，先友好回应一句，再自然回到随访——'那挺好的。对了，今天疼痛感觉怎么样了？'",
        "minimal":
            "患者只回了极简短的内容（如'嗯''哦''好'），无法判断是确认还是敷衍。"
            "策略：确认一下——'好的，那我理解您今天状态还可以对吗？疼痛大概几分呢？'，用半确认半追问的方式。",
    }
    strategy = type_strategies.get(ambiguity_type, type_strategies["vague"])

    # 追问次数 → 语气调整
    if retry_count == 0:
        retry_hint = "这是第一次追问，语气自然温和。"
    elif retry_count == 1:
        retry_hint = "这是第二次追问，可以稍微换个角度问（不要和第一次一模一样），语气依然温和。"
    else:
        retry_hint = (
            f"已经追问了 {retry_count} 次，患者可能不愿或无法回答 {missing_desc}。"
            "这一轮可以给一个更宽容的台阶：'没关系，这个可以不说，还有其他不舒服吗？'"
            "如果仍然模糊，建议结束本轮，不要反复追问让患者厌烦。"
        )

    return f"""你是一个慢性疼痛随访智能体。患者的回复比较模糊或回避，需要引导澄清。

══════════════════════════════════
  上下文
══════════════════════════════════

## 患者信息
- 姓名：{patient['name']}
- 年龄：{patient.get('age', '未知')}
- 诊断：{patient.get('diagnosis', '未知')}

## 当前缺失信息
{missing_desc}

## 近期历史数据
{history_summary if history_summary else '（无历史数据）'}

## 患者回复（模糊）
"{ambiguous_reply}"

══════════════════════════════════
  追问策略
══════════════════════════════════

## 模糊类型判定
{strategy}

## 追问次数感知
{retry_hint}

══════════════════════════════════
  生成要求
══════════════════════════════════

1.【精准追问】只追问缺失的信息（{missing_desc}），已收集的不要重复问。
2.【降低门槛】把开放式问题变成选择/半开放式：不要问"疼痛怎么样"，而问"今天疼痛大概几分？0是不疼，10是最疼"
3.【自然口语】像微信聊天，不要像问卷调查。用"您"称呼。
4.【简短有力】控制在 40~70 字，一次只带 1 个具体问题（最多 2 个）。
5.【尊重边界】患者明显不想说时不强迫，给台阶下。
6.【不用表情】不要使用表情符号，不用"呢""哦""呀"等过于口语化的语气词。

══════════════════════════════════
  输出格式
══════════════════════════════════

只返回澄清追问的文本消息，不要加引号或标记。"""
