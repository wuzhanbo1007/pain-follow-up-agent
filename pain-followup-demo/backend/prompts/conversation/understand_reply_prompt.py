# backend/prompts/conversation/understand_reply_prompt.py
"""understand_reply_prompt —— 患者回复理解提示词（说明书 8.5 表）。

唯一对应 ReplyUnderstandingAgent / understand_reply_node。
输出 ReplyUnderstanding：疼痛/睡眠/用药/副作用/当前情绪/停止意图/模糊类型/置信度/原文证据。
LLM 只做理解；是否继续由确定性 TurnRouter 决定。

内容复用原 prompts/reply_parsing.py 的语义推理框架（行为不变），
输出对齐 domain.models.ReplyUnderstanding。
"""
from __future__ import annotations

from dataclasses import dataclass

from prompts.prompt_spec import PromptSpec

PROMPT_VERSION = "understand-reply-v3"


@dataclass
class UnderstandContext:
    reply_text: str
    known_slots: dict = None      # 已收集槽位（供"老样子/没变化"继承历史）
    history_summary: str = ""     # 近期摘要
    current_question: str = ""    # 本轮护士问题，帮助解释“5分”等极短回复
    turn_no: int = 1


def build_prompt(ctx: UnderstandContext) -> PromptSpec:
    known = ctx.known_slots or {}
    known_str = ", ".join(f"{k}={v}" for k, v in known.items() if v is not None) or "（暂无）"
    system = f"""你是一个慢性疼痛随访数据结构化提取器。从患者自然语言回复中提取结构化信息。

══════════════════════════════════
  提取字段与语义推理规则
══════════════════════════════════

1. pain_nrs（疼痛评分, 0–10 的数字，可带1位小数，如7.5，或 null）
   0=不痛/消除；1-3=轻微/偶尔/隐隐；4-6=中度持续但不剧烈；7-9=重度/剧烈/严重影响；10=无法忍受。
   患者直接说数字→直接用；“疼得厉害/剧烈疼痛/疼痛难忍/无法忍受”等明确重度描述→填写 8-10 分，无法进一步区分时填写 8 分；
   说“好多了/减轻”但无数字→必须为 null，不能读取或推断历史分数；
   说"老样子/没变化"→仅继承与当前问题相关、且已知的槽位；无可继承值→null+低置信；
   只描述性质无强度→null；未提疼痛→null。若护士本轮明确询问疼痛评分，患者只回复“5”“五分”时，按当前问题解释为5分。
   核心原则：明确的重度描述必须纳入高分风险，不要把“疼得厉害”漏成 null。

2. sleep_quality（睡眠质量, "好"|"一般"|"差"|"很差"|null）
   好=正面(满意/充足/连续)；一般=中性(还行/凑合/时好时坏)；差=负面不极端(睡不好/多次醒/浅/短)；
   很差=极端(彻夜未眠/几乎没睡)；null=未提。睡了X小时：≥7→好；5-6→一般；3-4→差；≤2→很差。

3. medication_taken（用药依从性, true|false|"partial"|null）
   true=按时/规律/一直在吃；false=没吃/忘了/自己停了；partial=偶尔漏/减量/只吃了部分；
   null=未提；不确定("应该吃了吧""好像吃了")→null+低置信。

4. side_effects（副作用, "无"|具体症状字符串|null）
   步骤A：只有明确否定副作用/不适的句子→"无"；
   步骤B：否定句+具体症状→提取症状("没有不舒服，就是有点便秘"→"便秘")。
   步骤C：只有模糊回避("还好吧""就那样""说不清楚""没注意")→null，不能当作已确认无副作用。
   步骤D：完全不涉副作用→null。

5. patient_requested_stop（bool）患者明确表示不想继续/想结束/不想聊了→true。
6. requires_immediate_action（bool）患者表达绝望/极端痛苦/有自杀意念/突发剧痛需立即处理→true。
7. emotion_state（只能从 positive/stable/low/distressed/urgent/unknown 中选择）
   positive=明确表达心情不错、恢复有信心、状态变好；
   stable=情绪平稳、一般、没有明显情绪困扰；
   low=低落、难过、焦虑、无助，但没有明确自伤/轻生表达；患者回答整体感受时说“感觉一般”“状态一般”“感觉不是很好”也属于轻度 low。仅“睡得一般”“疼痛一般”“用药一般”等具体临床指标不能据此判断情绪；
   distressed=明显痛苦、崩溃、绝望或强烈恐惧，但尚未明确表达自伤/轻生；
   urgent=我不想活了、想自杀、想轻生、想伤害自己等明确危险表达；
   unknown=当前回复没有足够情绪证据。不能仅凭疼痛分数推断情绪。
8. emotion_intensity（low/medium/high）和 emotion_evidence（当前原文中的情绪依据，没有则为空字符串）。
9. ambiguity_type（none/vague/deflect/emotional/irrelevant/minimal）
   none=明确；vague=模糊笼统；deflect=回避/转移话题；emotional=情绪化宣泄；irrelevant=答非所问；minimal=极简(表情/一两个字)。

══════════════════════════════════
  全局规则
══════════════════════════════════
A. 语义推理>关键词匹配，但每个槽位都必须有自己的原文证据；一个表达不能无依据地填充多个槽位。患者已明确说出疼痛评分（包括小数）时，直接记录该评分，不要要求患者为了区分当前疼痛和近期平均疼痛而重复回答。
B. 宁可null+低置信，不瞎猜数字；护士问题是解释极短回复的上下文，不是患者数据来源。
C. 历史优先：仅在患者明确说"老样子/没变化"时继承相关已知槽位。
D. 否定优先：先判否定句再提取正向。E. 答非所问/纯表情→全null+minimal。
F. 严格JSON，不要额外文字。

历史背景：{ctx.history_summary or "无"}
已知已收集：{known_str}
本轮护士问题：{ctx.current_question or "（未知）"}"""

    user = f"""【患者回复】
"{ctx.reply_text}"

请输出严格 JSON（不要 markdown 包裹、不要注释、不要尾逗号）：
{{
  "pain_nrs": <number 0-10（可带1位小数）或 null>,
  "sleep_quality": <"好"|"一般"|"差"|"很差"|null>,
  "medication_taken": <true|false|"partial"|null>,
  "side_effects": <"无"|具体症状|null>,
  "confidence": <0-1 浮点，整体置信度>,
  "uncertain": <bool，低置信时为true>,
  "evidence": {{<slot: 原文片段，仅命中的>}},
  "patient_requested_stop": <bool>,
  "requires_immediate_action": <bool>,
  "emotion_state": <positive|stable|low|distressed|urgent|unknown>,
  "emotion_intensity": <low|medium|high>,
  "emotion_evidence": "<当前原文证据>",
  "ambiguity_type": <none|vague|deflect|emotional|irrelevant|minimal>,
  "parse_notes": "<一句话解析关键判断>"
}}"""
    user += "\\n本轮严格按原文证据提取。患者明确说出疼痛评分时直接填写；若说疼得厉害、剧烈疼痛、疼痛难忍或无法忍受等重度描述，在没有数字时填写8-10分，无法细分时填写8分；仅说好多了、减轻但无数字时仍填写null。患者只回复数字时，只有在本轮护士问题明确询问疼痛评分时才将其解释为pain_nrs。不要把“还行”同时填入睡眠、用药或副作用。"
    return PromptSpec(system=system, user=user, temperature=0.2,
                      max_tokens=400, prompt_version=PROMPT_VERSION,
                      response_format={"type": "json_object"})
