# backend/prompts/policy/compile_callback_policy_prompt.py
"""compile_callback_policy_prompt —— 电话回访策略编译提示词（说明书 5）。

唯一对应 CallbackPolicyCompilerAgent。仅编译电话回访条件，
不决定基础应访/免随访名单。字段必须用白名单。
"""
from __future__ import annotations

from dataclasses import dataclass

from prompts.prompt_spec import PromptSpec

PROMPT_VERSION = "compile-callback-policy-v1"


@dataclass
class PolicyCompileContext:
    natural_language: str
    field_whitelist: tuple = ("consecutive_no_reply_days", "last_reply_at")


def build_prompt(ctx: PolicyCompileContext) -> PromptSpec:
    whitelist = ", ".join(ctx.field_whitelist)
    system = f"""你是住院随访排程助手。把医护写的「今日随访配置」中的电话回访自然语言规则编译为 JSON。

只输出 JSON，不要多余文字。结构：
{{
  "policy_type": "phone_callback",
  "source_text": "原始自然语言",
  "eligible_scope": "base_roster.send_only",
  "conditions": [{{"field":"consecutive_no_reply_days","op":"gte","value":3}}],
  "action": {{"channel":"phone","phone_callback":true}},
  "unrecognized_lines": ["你没看懂的原始行，逐行列出；都看懂了就 []"]
}}

注意：
1. 配置只声明电话回访规则，不声明谁应随访/谁免随访（名单由数据库判定）。
2. 允许的条件字段白名单：{whitelist}。禁止生成患者 ID 列表、应访/免随访动作或任意数据库查询表达式。
3. 医护可能用各种口语（没回应/不回消息/超过两天没动静/三天不理我）表达"N天没回复"，
   请语义理解后归一到 consecutive_no_reply_days 的 gte 条件。无法确定的天数用 null（不生成 conditions）。
4. op 只能是 gte/gt/lte/lt/eq。value 为整数。
5. 没看懂的行放进 unrecognized_lines，不要硬编。"""
    user = f"配置内容：\n{ctx.natural_language}"
    return PromptSpec(system=system, user=user, temperature=0.0,
                      max_tokens=400, prompt_version=PROMPT_VERSION,
                      response_format={"type": "json_object"})
