"""统一 Chat Model 工厂（参照 Comet-main chat_model.py）

设计要点（对齐 Comet 的“不用 AsyncOpenAI 直连”思路，但全异步）：
- 所有 Agent / Engine 统一通过 LangChain ChatOpenAI 调用 LLM。
- ChatOpenAI 同时提供同步(.invoke)与异步(.ainvoke / .astream)两套 API；
  本项目统一走异步路径（await model.ainvoke / async for model.astream），
  底层由 ChatOpenAI 内部的 AsyncOpenAI 驱动，因此不需要手动 new AsyncOpenAI(...)，
  也不需要在业务代码里用 asyncio.to_thread 包裹。
- 业务代码运行在 async 上下文（async def 路由 / async 任务 / langgraph ainvoke），
  直接 await achat_completion(...) 即可；事件循环在 LLM 等待期间可被让出，
  其他请求与 WebSocket 推送照常进行。
- 连接级重试（指数退避）覆盖临时网络抖动 / 5xx / 限流；
  response_format 不被模型支持时自动去掉重试一次（对齐旧 planner 兜底）。
- 若 langchain_openai 未安装，退化为原生 AsyncOpenAI 客户端（同样异步）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncGenerator, Optional

from core.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT

# ---- 连接级重试（参照 Comet client._post_with_retry）----
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.5  # 秒，第 n 次重试等待 _RETRY_BACKOFF * n
_RETRY_STATUS = {429, 500, 502, 503, 504}

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
    HAS_LANGCHAIN = True
except Exception:  # pragma: no cover - langchain 未安装时退化
    ChatOpenAI = None  # type: ignore[assignment]
    SystemMessage = HumanMessage = AIMessage = None  # type: ignore[assignment]
    HAS_LANGCHAIN = False

try:
    from openai import OpenAI, AsyncOpenAI
    HAS_OPENAI = True
except Exception:  # pragma: no cover
    HAS_OPENAI = False


def is_llm_available() -> bool:
    """配置了 key 且可用 ChatOpenAI 或原生 OpenAI 即为 True。"""
    return bool((HAS_LANGCHAIN or HAS_OPENAI) and LLM_API_KEY)


# 进程级客户端单例（仅兜底 / embedding 复用）
_raw_client = None
_async_client = None


def _get_raw_client():
    global _raw_client
    if _raw_client is None:
        _raw_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT)
    return _raw_client


def _get_async_client():
    global _async_client
    if _async_client is None:
        _async_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT)
    return _async_client


def _to_lc_messages(messages: list[dict]) -> list:
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            out.append(SystemMessage(content=content))  # type: ignore[arg-type]
        elif role == "assistant":
            out.append(AIMessage(content=content))  # type: ignore[arg-type]
        else:
            out.append(HumanMessage(content=content))  # type: ignore[arg-type]
    return out


def build_tool_model(tools: Optional[list[dict]] = None,
                     *, temperature: float = 0.3) -> Any:
    """构建带 function-calling 工具的 ChatOpenAI 模型。

    返回绑定了工具的 ChatOpenAI 实例，可直接 .ainvoke() 获取
    AIMessage（含 tool_calls）。LLM 不可用时返回 None。
    """
    if not HAS_LANGCHAIN or not LLM_API_KEY:
        return None
    model = build_chat_model(temperature=temperature, streaming=False)
    if tools:
        model = model.bind_tools(tools)
    return model


def build_chat_model(*, temperature: float = 0.7, json_mode: bool = False,
                     max_tokens: Optional[int] = None,
                     extra_body: Optional[dict] = None,
                     streaming: bool = True) -> Any:
    """构建 ChatOpenAI（异步底层由 AsyncOpenAI 驱动）。Comet chat_model.build_chat_model 等价物。

    - streaming=True（默认）使 .astream 可用；.ainvoke 不论 streaming 参数均可使用。
    - json_mode=True 时绑定 response_format={"type":"json_object"}。
    - extra_body 透传额外参数（如 DeepSeek 的 {"thinking":{"type":"disabled"}}）。
    - 无 langchain 时退化为原生 AsyncOpenAI 客户端。
    """
    if HAS_LANGCHAIN:
        kwargs: dict[str, Any] = dict(
            model=LLM_MODEL,
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            temperature=temperature,
            timeout=LLM_TIMEOUT,
            streaming=streaming,
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if extra_body:
            kwargs["extra_body"] = extra_body
        model = ChatOpenAI(**kwargs)
        if json_mode:
            model = model.bind(response_format={"type": "json_object"})
        return model
    # 兜底：无 langchain → 原生 AsyncOpenAI
    return _get_async_client()


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否属于可重试的瞬时错误（网络抖动 / 超时 / 5xx / 限流）。"""
    msg = str(exc).lower()
    if any(k in msg for k in (
        "timeout", "timed out", "connection", "reset by peer",
        "rate limit", "429", "500", "502", "503", "504",
        "temporary", "try again", "server error",
    )):
        return True
    status = getattr(exc, "status_code", None)
    if status in _RETRY_STATUS:
        return True
    return False


async def _ainvoke(model: Any, messages: list[dict], *, use_format: bool,
                  temperature: float, max_tokens: Optional[int],
                  extra_body: Optional[dict]) -> str:
    """执行一次异步调用，返回模型文本。"""
    if HAS_LANGCHAIN and isinstance(model, ChatOpenAI):
        resp = await model.ainvoke(_to_lc_messages(messages))
        content = getattr(resp, "content", None)
        return content if isinstance(content, str) else str(resp)
    # 兜底：原生 AsyncOpenAI
    kwargs: dict[str, Any] = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "timeout": LLM_TIMEOUT,
    }
    if use_format:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if extra_body:
        kwargs["extra_body"] = extra_body
    resp = await model.chat.completions.create(**kwargs)
    content = resp.choices[0].message.content
    return content if isinstance(content, str) else ""


async def achat_completion(messages: list[dict], *, temperature: float = 0.7,
                           response_format: Optional[dict] = None,
                           max_tokens: Optional[int] = None,
                           extra_body: Optional[dict] = None) -> str:
    """异步调用 Chat Model，返回文本（已 strip）。事件循环在等待期间可被让出。

    连接级重试（指数退避）覆盖临时网络抖动 / 5xx / 限流；response_format 不被模型
    支持（参数类 4xx）时自动去掉 format 重试一次。其余异常直接抛出，由调用方兜底降级。
    """
    use_format = response_format is not None
    model = build_chat_model(
        temperature=temperature, json_mode=use_format,
        max_tokens=max_tokens, extra_body=extra_body,
    )

    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            content = await _ainvoke(
                model, messages, use_format=use_format,
                temperature=temperature, max_tokens=max_tokens, extra_body=extra_body,
            )
            if not content or not content.strip():
                raise ValueError("LLM 返回空内容")
            return content.strip()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            # 参数类错误（如 response_format 不被支持）→ 去掉 format 重试一次
            if use_format and not _is_retryable(exc):
                use_format = False
                model = build_chat_model(
                    temperature=temperature, json_mode=False,
                    max_tokens=max_tokens, extra_body=extra_body,
                )
                continue
            if not _is_retryable(exc):
                raise
            if attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_BACKOFF * (attempt + 1))
    assert last_exc is not None
    raise last_exc


async def astream_completion(messages: list[dict], *, temperature: float = 0.7,
                             max_tokens: Optional[int] = None,
                             extra_body: Optional[dict] = None) -> AsyncGenerator[str, None]:
    """异步流式调用，逐 chunk 产出文本片段（token 级推送用）。"""
    model = build_chat_model(
        temperature=temperature, json_mode=False,
        max_tokens=max_tokens, extra_body=extra_body, streaming=True,
    )
    if HAS_LANGCHAIN and isinstance(model, ChatOpenAI):
        async for chunk in model.astream(_to_lc_messages(messages)):
            text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
            if text:
                yield text
    else:
        stream = await model.chat.completions.create(
            model=LLM_MODEL, messages=messages, temperature=temperature,
            max_tokens=max_tokens, extra_body=extra_body, stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            text = chunk.choices[0].delta.content or ""
            if text:
                yield text
