# backend/agents/patient_simulator_agent.py
"""PatientSimulatorAgent —— 自动患者模拟智能体。

这个文件同时包含：

* LangGraph 工作流：场景初始化、回复生成和兜底路由；
* 患者模拟决策：根据患者场景、护士问题、历史对话和人物特征生成回复。

LLM 可以自主选择自然回复策略，例如直接回答、先表达犹豫、补充程度描述
或说明轻微不适。Prompt 中的示例只用于提供说话风格，不是固定答案。

本 Agent 只生成患者回复，不计算风险、不决定结束、不触发告警、不写数据库。
"""
from __future__ import annotations

import hashlib
import random
import re
from typing import Any

from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime

from agents.states import SimState
from infrastructure.runtime_context import AppContext
from prompts.simulator.generate_patient_reply_prompt import (
    SimulatorContext,
    build_prompt,
)

_FALLBACK_REPLY = "今天还好。"

# 只保留结构化输出所需的规范化 ID。具体槽位含义和识别方式交给 Prompt。
_VALID_SLOTS = frozenset({
    "pain_nrs",
    "sleep_quality",
    "medication_taken",
    "side_effects",
})

_BAD_OUTPUT_PATTERNS = (
    "我是ai", "我是 ai", "作为ai", "作为 AI", "语言模型", "模拟患者",
    "无法回答", "我不能回答",
)


class PatientSimulatorAgent:
    """自动患者模拟智能体：场景一致 + 自主回复 + 结果校验。"""

    def __init__(self, llm_gateway=None):
        self._gw = llm_gateway

    async def create_scenario(self, snapshot: dict, *, seed: str) -> dict:
        """生成 episode 级场景事实；同一 seed 始终得到同一组事实。"""
        rng = random.Random(seed)
        history = snapshot.get("history") or []
        nrs_list = [h.get("nrs_score") for h in history
                    if h.get("nrs_score") is not None]
        avg = round(sum(nrs_list) / len(nrs_list), 1) if nrs_list else 5
        nrs = max(0, min(10, int(avg + rng.uniform(-1, 1))))
        return {
            "scenario_version": "patient-simulator-v3",
            "facts": {
                "pain_nrs": nrs,
                "sleep_quality": rng.choice(["好", "一般", "差", "很差"]),
                "medication_taken": rng.choice([True, True, False]),
                "side_effects": rng.choice(["无", "无", "恶心", "便秘", "头晕"]),
            },
            "disclosure": {
                "pain_nrs": "when_asked",
                "sleep_quality": "when_asked",
                "medication_taken": "when_asked",
                "side_effects": "when_asked",
            },
            "persona": {
                "verbosity": "short",
                "cooperation": 0.7,
                "style": "口语、简短、像微信聊天",
            },
            "seed": seed,
        }

    async def reply(
        self,
        scenario: dict,
        *,
        transcript: list[dict],
        question: str,
        snapshot: dict | None = None,
        target_slot: str | None = None,
        known_slots: dict | None = None,
        round_num: int = 1,
    ) -> str:
        """兼容旧调用方：只返回患者文本。"""
        result = await self.generate_reply(
            scenario,
            transcript=transcript,
            question=question,
            snapshot=snapshot,
            target_slot=target_slot,
            known_slots=known_slots,
            round_num=round_num,
        )
        return result["reply"]

    async def generate_reply(
        self,
        scenario: dict,
        *,
        transcript: list[dict],
        question: str,
        snapshot: dict | None = None,
        target_slot: str | None = None,
        known_slots: dict | None = None,
        round_num: int = 1,
    ) -> dict:
        """让 LLM 自主决定回复策略，并对最终文本做事实一致性校验。"""
        from infrastructure.llm.llm_gateway import LLMGateway

        gw = self._gw or LLMGateway()
        facts = (scenario or {}).get("facts", {})
        persona = (scenario or {}).get("persona", {})
        last_nurse = question or self._last_nurse_message(transcript)

        # target_slot 只用于 LLM 失败后的模板兜底，不参与正常 LLM 回复决策。
        # 正常路径完全由模型根据医护最后一句话判断意图和回答槽位。
        suggested_slot = self._normalize_slot(target_slot)
        is_general_status = self._is_general_status_question(last_nurse)
        fallback_slot = None if is_general_status else (suggested_slot or "pain_nrs")
        fallback_intent = "general_status" if is_general_status else fallback_slot
        target_value = facts.get(suggested_slot) if suggested_slot else None
        known_slots = known_slots or {}
        fallback = self._template_reply(
            question=last_nurse,
            facts=facts,
            target_slot=fallback_intent,
            seed=(scenario or {}).get("seed", ""),
            round_num=round_num,
        )

        if not gw.is_llm_available():
            return self._result(fallback, fallback_slot, "llm_unavailable")

        brief = {
            "name": (snapshot or {}).get("name", "患者"),
            "diagnosis": (snapshot or {}).get("diagnosis", ""),
            "pain_type": (snapshot or {}).get("pain_type", "慢性疼痛"),
            "age": (snapshot or {}).get("age"),
        }
        spec = build_prompt(SimulatorContext(
            patient_brief=brief,
            target_slot=suggested_slot,
            target_value=target_value,
            facts=facts,
            known_slots=known_slots,
            persona=persona,
            transcript=self._trim_transcript(transcript),
            last_nurse_question=last_nurse,
            round_num=round_num,
        ))
        try:
            data = await gw.chat_json(
                [
                    {"role": "system", "content": spec.system},
                    {"role": "user", "content": spec.user},
                ],
                profile="patient_simulator",
            )
            text = self._extract_reply(data)
            if isinstance(data, dict) and "slot" in data:
                # 模型明确返回 null 时表示开放式/非采集问题，必须尊重模型
                # 的判断，不能再被工作流提供的 suggested_slot 覆盖。
                decided_slot = self._normalize_slot(data.get("slot"))
            else:
                # 模型没有返回 slot 时视为“本轮未识别到采集项目”，不能
                # 擅自把工作流参考槽位当成模型判断结果。
                decided_slot = None
            decided_value = facts.get(decided_slot) if decided_slot else None
            if self._is_valid_reply(text, decided_slot, decided_value):
                return self._result(text, decided_slot, "llm", data)
        except Exception:
            # 模拟器不能阻断随访；失败时走同一套确定性兜底。
            pass

        return self._result(fallback, fallback_slot, "deterministic_fallback")

    @staticmethod
    def _result(reply: str, target_slot: str | None, source: str,
                raw: Any | None = None) -> dict:
        metadata = {"reply_source": source}
        if isinstance(raw, dict):
            # 只保留模型给出的可审计元数据，不把内部推理内容写入状态。
            strategy = raw.get("strategy") or raw.get("response_strategy")
            if strategy:
                metadata["strategy"] = str(strategy)[:40]
        return {
            "reply": reply,
            "valid": bool(reply),
            "target_slot": target_slot,
            "mentioned_slots": [target_slot] if target_slot else [],
            "reply_source": source,
            "metadata": metadata,
        }

    @staticmethod
    def _last_nurse_message(transcript: list[dict]) -> str:
        return next(
            (m.get("content", "") for m in reversed(transcript or [])
             if m.get("role") in ("nurse", "assistant")),
            "",
        )

    @staticmethod
    def _normalize_slot(slot: Any) -> str | None:
        """只校验 LLM 返回的规范化槽位 ID，不在代码中解析自然语言关键词。"""
        if not isinstance(slot, str):
            return None
        normalized = slot.strip().lower()
        return normalized if normalized in _VALID_SLOTS else None

    @staticmethod
    def _is_general_status_question(question: str) -> bool:
        """识别开场/开放式状态问题，避免被首个 missing slot 强行带入评分。"""
        text = re.sub(r"[\s\u3000，。！？：；、,.!?;:'\"“”‘’]", "", question or "")
        patterns = (
            "今天感觉怎么样", "今天感觉如何", "今天怎么样", "今天还好吗",
            "现在感觉怎么样", "现在感觉如何", "现在还好吗",
            "最近感觉怎么样", "最近感觉如何", "最近还好吗",
            "这两天感觉怎么样", "这两天感觉如何",
            "整体感觉怎么样", "整体情况怎么样", "总体情况怎么样",
        )
        return any(pattern in text for pattern in patterns)

    @staticmethod
    def _extract_reply(data: Any) -> str:
        if isinstance(data, dict):
            text = data.get("reply") or data.get("text") or data.get("answer") or ""
        else:
            text = data or ""
        return PatientSimulatorAgent._strip_role_prefix(str(text).strip())

    @staticmethod
    def _strip_role_prefix(text: str) -> str:
        text = text.strip().strip("`").strip()
        for prefix in ("患者：", "患者:", "病人：", "病人:", "我：", "我:"):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        if len(text) >= 2 and text[0] in ('"', "'", "“", "‘") \
                and text[-1] in ('"', "'", "”", "’"):
            text = text[1:-1].strip()
        return text

    @staticmethod
    def _is_valid_reply(text: str, target_slot: str | None,
                        target_value: Any) -> bool:
        if not text or len(text) > 100 or "\n" in text:
            return False
        lowered = text.lower()
        if any(pattern.lower() in lowered for pattern in _BAD_OUTPUT_PATTERNS):
            return False
        if target_slot == "pain_nrs" and target_value is not None:
            scores = [int(value) for value in re.findall(
                r"(?<!\d)(10|[0-9])(?:\s*分)?", text)]
            if scores and scores[-1] != int(target_value):
                return False
        return True

    @staticmethod
    def _trim_transcript(transcript: list[dict], limit: int = 8) -> list[dict]:
        return [
            {"role": m.get("role", ""), "content": str(m.get("content", ""))[:120]}
            for m in (transcript or [])[-limit:]
        ]

    @staticmethod
    def _template_reply(
        *, question: str, facts: dict, target_slot: str,
        seed: str, round_num: int,
    ) -> str:
        """LLM 不可用时的稳定兜底，不作为正常回复生成路径。"""
        value = facts.get(target_slot)
        variants = {
            "general_status": [
                "今天感觉还可以，整体比较稳定。",
                "今天还行，没有特别不舒服。",
                "今天状态一般，不过还能应付。",
            ],
            "pain_nrs": [
                f"大概{value if value is not None else 5}分。",
                f"现在差不多{value if value is not None else 5}分，活动时明显一点。",
            ],
            "sleep_quality": {
                "好": ["睡得还行，基本一觉到天亮。", "昨晚睡得不错。"],
                "一般": ["睡了几个小时，整体一般。", "睡眠还可以，中间醒了一次。"],
                "差": ["睡得不太好，晚上醒了好几次。", "疼得有点影响睡觉。"],
                "很差": ["几乎没怎么睡着。", "昨晚睡得很差，基本一直醒着。"],
            }.get(value, ["睡眠一般。"]),
            "medication_taken": (
                ["按时吃了。", "今天的药吃过了。"]
                if value else ["今天忘记吃药了。", "药还没来得及吃。"]
            ),
            "side_effects": (
                ["目前没发现什么不舒服。", "没有明显副作用。"]
                if value in (None, "无")
                else [f"有点{value}。", f"吃完药后有些{value}。"]
            ),
        }
        choices = variants.get(target_slot, [_FALLBACK_REPLY])
        digest = hashlib.sha256(
            f"{seed}|{target_slot}|{round_num}|{question}".encode()
        ).hexdigest()
        return choices[int(digest[:8], 16) % len(choices)]


# ---- 节点 ----

async def ensure_scenario_node(state: SimState, runtime: Runtime[AppContext]) -> dict:
    """会话开始时只生成一次 scenario；后续轮次复用（§9，场景与轮次无关）。"""
    if state.get("scenario"):
        return {}
    seed = state.get("scenario_seed") or state.get("episode_id") or ""
    snapshot = state.get("snapshot") or {}
    agent = PatientSimulatorAgent(runtime.context.llm_gateway)
    scenario = await agent.create_scenario(snapshot, seed=seed)
    return {"scenario_seed": seed, "scenario": scenario}


async def simulate_reply_node(state: SimState, runtime: Runtime[AppContext]) -> dict:
    """基于固定场景、transcript 与护士问题生成患者回复。"""
    agent = PatientSimulatorAgent(runtime.context.llm_gateway)
    transcript = state.get("transcript") or []
    last_nurse = next(
        (m.get("content", "") for m in reversed(transcript)
         if m.get("role") in ("nurse", "assistant")),
        state.get("last_question") or "",
    )
    result = await agent.generate_reply(
        state.get("scenario") or {},
        transcript=transcript,
        question=last_nurse,
        snapshot=state.get("snapshot") or {},
        target_slot=state.get("target_slot"),
        known_slots=state.get("known_slots") or {},
        round_num=state.get("round_num", 1),
    )
    return {
        "reply": result.get("reply", _FALLBACK_REPLY),
        "valid": bool(result.get("valid")),
        "target_slot": result.get("target_slot"),
        "mentioned_slots": result.get("mentioned_slots", []),
        "reply_metadata": {k: v for k, v in result.items()
                           if k not in {"reply", "valid"}},
    }


async def _deterministic_fallback_node(state: SimState) -> dict:
    return {"reply": _FALLBACK_REPLY}


def _valid_router(state: SimState) -> str:
    return "fallback" if not state.get("valid") else "END_route"


# ---- 图 ----

def build_simulator_graph():
    g = StateGraph(SimState, context_schema=AppContext)
    g.add_node("ensure_scenario", ensure_scenario_node)
    g.add_node("simulate_reply", simulate_reply_node)
    g.add_node("fallback", _deterministic_fallback_node)
    g.add_edge(START, "ensure_scenario")
    g.add_edge("ensure_scenario", "simulate_reply")
    g.add_conditional_edges("simulate_reply", _valid_router,
                             {"END_route": END, "fallback": "fallback"})
    g.add_edge("fallback", END)
    return g.compile()


simulator_workflow = build_simulator_graph()


async def run_simulation(*, episode_id: str = "", patient_id: str = "",
                         scenario_seed: str | None = None, snapshot: dict,
                         transcript: list[dict], round_num: int,
                         scenario: dict | None = None,
                         target_slot: str | None = None,
                         known_slots: dict | None = None,
                         context=None) -> dict:
    """SimState 工作流入口：以患者身份回答护士最后一个问题。

    返回 {reply, valid, scenario, scenario_seed, target_slot, reply_metadata}；
    调用方（PatientFollowupAgent）
    将 scenario 写回 PatientState 固化，后续轮次复用同一场景。
    context：显式注入 AppContext（图节点经 runtime.context 传入）；缺省取全局单例。
    """
    if context is None:
        from infrastructure.runtime_context import get_context
        context = get_context()
    result = await simulator_workflow.ainvoke({
        "episode_id": episode_id, "patient_id": patient_id,
        "scenario_seed": scenario_seed, "snapshot": snapshot,
        "scenario": scenario, "transcript": transcript, "round_num": round_num,
        "target_slot": target_slot, "known_slots": known_slots or {},
        "last_question": transcript[-1].get("content", "")
        if transcript and transcript[-1].get("role") in ("nurse", "assistant") else "",
    }, context=context)
    return {
        "reply": result.get("reply") or _FALLBACK_REPLY,
        "valid": bool(result.get("valid", result.get("reply"))),
        "scenario": result.get("scenario"),
        "scenario_seed": result.get("scenario_seed"),
        "target_slot": result.get("target_slot"),
        "mentioned_slots": result.get("mentioned_slots", []),
        "reply_metadata": result.get("reply_metadata", {}),
    }
