# backend/infrastructure/llm/llm_gateway.py
"""LLMGateway —— 统一模型调用网关（说明书 14.2：client.py + model.py 合并）。

一个 LLMGateway：模型、温度、超时、结构化输出、重试按 profile 配置。
图节点通过 Runtime Context 获取 gateway，不直接持有 client 实例。

实现上复用现有 llm.client / llm.model，对外提供统一接口，保持降级语义不变：
  - chat()           异步文本（已 strip）
  - chat_json()      json_object 模式 + 安全解析
  - achat_completion  透传（兼容旧调用方）
  - is_llm_available()
"""
from __future__ import annotations

import json
from typing import Any, Optional, TypeVar

from core.logging_config import get_logger
from prompts.prompt_spec import PromptSpec

log = get_logger("painsmart.llm_gateway")

T = TypeVar("T")


class LLMProfile:
    """按用途的模型调用 profile。"""
    def __init__(self, *, temperature: float = 0.5, max_tokens: int | None = None,
                 response_format: dict | None = None, timeout: int | None = None):
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.response_format = response_format
        self.timeout = timeout


# 预置 profile（说明书：每个 Agent 有唯一输入/输出 Schema 与一个提示词文件）
PROFILES: dict[str, LLMProfile] = {
    "understand_reply": LLMProfile(temperature=0.2),
    "compose_question": LLMProfile(temperature=0.6),
    "compose_greeting": LLMProfile(temperature=0.7),
    "compose_farewell": LLMProfile(temperature=0.5),
    "summarize_history": LLMProfile(temperature=0.3, max_tokens=400),
    # 患者模拟需要事实一致和槽位命中，使用较低温度并限制输出长度。
    # Qwen3.6-35B 会把较多 completion token 用于推理；100 会在输出 JSON 前
    # 以 finish_reason=length 结束，导致 message.content 为空。
    "patient_simulator": LLMProfile(temperature=0.45, max_tokens=12000,
                                     response_format={"type": "json_object"}),
    "ai_review": LLMProfile(temperature=0.2, response_format={"type": "json_object"}),
    "policy_compiler": LLMProfile(temperature=0.0, response_format={"type": "json_object"}),
    "plan_generation": LLMProfile(temperature=0.4),
    "default": LLMProfile(temperature=0.5),
}


class LLMGateway:
    """统一模型调用网关。"""

    @staticmethod
    def is_llm_available() -> bool:
        try:
            from llm.client import is_llm_available
            return is_llm_available()
        except Exception:
            return False

    @staticmethod
    def get_profile(name: str) -> LLMProfile:
        return PROFILES.get(name, PROFILES["default"])

    async def chat(self, messages: list[dict], *, profile: str = "default",
                   temperature: float | None = None, max_tokens: int | None = None,
                   response_format: dict | None = None) -> str:
        """异步文本调用。profile 决定默认参数，显式参数覆盖。"""
        from llm.client import chat, is_llm_available
        if not is_llm_available():
            raise RuntimeError("LLM 不可用（未配置 LLM_API_KEY）")
        p = self.get_profile(profile)
        return await chat(
            messages,
            temperature=temperature if temperature is not None else p.temperature,
            response_format=response_format if response_format is not None else p.response_format,
            max_tokens=max_tokens if max_tokens is not None else p.max_tokens,
        )

    async def chat_json(self, messages: list[dict], *, profile: str = "default",
                        temperature: float | None = None) -> dict:
        """json_object 模式调用 + 安全解析。失败返回 {}。"""
        from llm.client import chat, parse_json_safe, is_llm_available
        if not is_llm_available():
            return {}
        p = self.get_profile(profile)
        fmt = {"type": "json_object"}
        try:
            raw = await chat(
                messages,
                temperature=temperature if temperature is not None else p.temperature,
                response_format=fmt,
                max_tokens=p.max_tokens,
            )
            return parse_json_safe(raw)
        except Exception as exc:
            log.warning("chat_json 失败（profile=%s）: %s", profile, exc)
            return {}

    async def invoke_structured(self, prompt: PromptSpec,
                                variables: dict | None = None,
                                schema: type[T] | None = None) -> T | dict:
        """§12.1：按 PromptSpec 统一结构化调用。

        ``prompt.render(variables)`` → LLM JSON →（可选）Pydantic schema 校验。
        使用 PromptSpec 自带 temperature / max_tokens / response_format；
        LLM 输出不可解析或校验失败时抛 ValueError（不静默降级）。未给 schema 时
        返回解析后的 dict。回复理解 / AI 审阅必须经此通道返回结构化模型，不得把
        对象转字符串（§12.1）。
        """
        from llm.client import chat, parse_json_safe, is_llm_available
        if not is_llm_available():
            raise RuntimeError("LLM 不可用（未配置 LLM_API_KEY）")
        messages = prompt.render(variables)
        raw = await chat(
            messages,
            temperature=prompt.temperature,
            response_format=prompt.response_format,
            max_tokens=prompt.max_tokens,
        )
        data = parse_json_safe(raw)
        if not data:
            raise ValueError("invoke_structured: LLM 输出无法解析为 JSON")
        if schema is None:
            return data
        try:
            return schema.model_validate(data)
        except Exception as exc:
            raise ValueError(
                f"invoke_structured: 结构化校验失败 ({getattr(schema, '__name__', schema)}): {exc}"
            ) from exc

    async def achat_completion(self, messages: list[dict], **kw) -> str:
        """透传到 llm.model.achat_completion（兼容旧调用方）。"""
        from llm.model import achat_completion
        return await achat_completion(messages, **kw)
