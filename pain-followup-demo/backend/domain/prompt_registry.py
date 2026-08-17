# backend/domain/prompt_registry.py
"""PromptUsageRegistry —— PromptKey → 唯一 Agent / 文件（说明书 8.5 调用与引用规范 5）。

启动时校验：无孤儿、无多 Agent 复用、无节点绕过 Agent 直接调用 LLM。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PromptEntry:
    key: str
    prompt_file: str
    agent: str


# 每个提示词文件只由一个 Agent 引用
REGISTRY: dict[str, PromptEntry] = {
    "conversation.understand_reply": PromptEntry(
        "conversation.understand_reply",
        "prompts/conversation/understand_reply_prompt.py",
        "ReplyUnderstandingAgent"),
    "conversation.compose_question": PromptEntry(
        "conversation.compose_question",
        "prompts/conversation/compose_question_prompt.py",
        "QuestionComposerAgent"),
    "conversation.compose_greeting": PromptEntry(
        "conversation.compose_greeting",
        "prompts/conversation/compose_greeting_prompt.py",
        "GreetingComposerAgent"),
    "conversation.compose_farewell": PromptEntry(
        "conversation.compose_farewell",
        "prompts/conversation/compose_farewell_prompt.py",
        "FarewellComposerAgent"),
    "conversation.summarize_history": PromptEntry(
        "conversation.summarize_history",
        "prompts/conversation/summarize_history_prompt.py",
        "HistorySummaryAgent"),
    "simulator.generate_patient_reply": PromptEntry(
        "simulator.generate_patient_reply",
        "prompts/simulator/generate_patient_reply_prompt.py",
        "PatientSimulatorAgent"),
    "review.review_followup": PromptEntry(
        "review.review_followup",
        "prompts/review/review_followup_prompt.py",
        "AIReviewAgent"),
    "policy.compile_callback_policy": PromptEntry(
        "policy.compile_callback_policy",
        "prompts/policy/compile_callback_policy_prompt.py",
        "CallbackPolicyCompilerAgent"),
    "planning.generate_followup_plan": PromptEntry(
        "planning.generate_followup_plan",
        "prompts/planning/generate_followup_plan_prompt.py",
        "PlanGenerationAgent"),
    "planning.plan_system_guardrails": PromptEntry(
        "planning.plan_system_guardrails",
        "prompts/planning/plan_system_guardrails_prompt.py",
        "PlanGenerationAgent"),
}


def prompt_file_for(key: str) -> str:
    entry = REGISTRY.get(key)
    if entry is None:
        raise KeyError(f"未注册的 PromptKey: {key}")
    return entry.prompt_file


def agent_for(key: str) -> str:
    entry = REGISTRY.get(key)
    if entry is None:
        raise KeyError(f"未注册的 PromptKey: {key}")
    return entry.agent


def validate_registry() -> list[str]:
    """§12.1：启动时校验 PromptUsageRegistry 映射。

    校验项：
      1. 每个注册项的 prompt_file 在磁盘存在；
      2. 同一 prompt_file 只被一个 Agent 引用（系统消息允许与主提示词同 Agent）；
      3. prompts/ 下导出 build_prompt / build_system_prompt 的模块都已注册（无孤儿）。

    返回问题列表；无问题返回 []。由 bootstrap 在启动时调用并记录。
    """
    from pathlib import Path

    problems: list[str] = []
    prompts_root = Path(__file__).resolve().parent.parent / "prompts"

    # 1) 注册项 prompt_file 必须存在
    for key, entry in REGISTRY.items():
        if not (prompts_root.parent / entry.prompt_file).is_file():
            problems.append(f"[{key}] prompt_file 不存在: {entry.prompt_file}")

    # 2) prompt_file → Agent 唯一性（同一 Agent 复用其系统消息允许）
    owner: dict[str, str] = {}
    for key, entry in REGISTRY.items():
        prev = owner.get(entry.prompt_file)
        if prev and prev != entry.agent:
            problems.append(
                f"[{key}] prompt_file 被多个 Agent 引用: {entry.prompt_file} "
                f"({prev} / {entry.agent})"
            )
        owner[entry.prompt_file] = entry.agent

    # 3) 无孤儿：prompts/ 下导出 build_prompt / build_system_prompt 的模块必须已注册
    registered = {e.prompt_file for e in REGISTRY.values()}
    for py in sorted(prompts_root.rglob("*.py")):
        if py.name == "__init__.py":
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        if "def build_prompt" not in text and "def build_system_prompt" not in text:
            continue
        rel = py.relative_to(prompts_root.parent).as_posix()
        if rel not in registered:
            problems.append(f"[orphan] 未注册的提示词模块: {rel}")
    return problems
