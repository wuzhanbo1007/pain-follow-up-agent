"""
统一 LLM 客户端（多 Agent 架构改造 - 任务 1）

把散落在各处的 `from openai import OpenAI; OpenAI(...)` 收敛为单一模块级单例，
所有 Agent / Engine 共用同一 client，配置从 core.config 读取。

设计要点（保持与既有代码一致）：
  - 不在构造函数里设置全局超时之外，也在每次调用 chat() 时显式传入 timeout，
    与 message_generator / parser / planner / execution / auto_reply 原有调用方式一致。
  - chat() 内部只捕获异常并向上抛（不再此处做降级），“无 LLM 时规则/模板兜底”
    的责任归调用方（既有代码风格：调用方自行 try/except 降级）。
"""
import json

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

from core.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT

# 统一 Chat Model 工厂（ChatOpenAI / 原生 AsyncOpenAI 退化），异步调用对齐 Comet
from llm.model import (  # noqa: E402
    HAS_LANGCHAIN,
    build_chat_model,
    build_tool_model,
    achat_completion,
    astream_completion,
    is_llm_available,
)


# 模块级单例
_client = None


# ===== Embedding 客户端（与 chat 相互独立：可能用不同的 key/base_url）=====
# 按 (base_url, api_key) 缓存，同一组凭据复用同一 client。
_embedding_clients = {}


def get_embedding_client(base_url=None, api_key=None):
    """返回 Embedding 用的 OpenAI 兼容 client。

    默认读取 knowledge.config 的 EMBEDDING_BASE_URL / EMBEDDING_API_KEY
    （与 chat 用的 LLM_* 配置相互独立，可指向 SiliconFlow / 本地 vLLM 等）。
    传入 base_url / api_key 时按“同一组凭据”复用单例，保持
    OpenAICompatibleProvider 可覆盖 base_url/api_key 的原有行为。

    未安装 openai 或缺少 base_url 时抛 RuntimeError（与原有兜底一致）。
    """
    global _embedding_clients
    # 懒加载 embedding 配置，避免 llm.client 与 knowledge 包产生循环依赖
    if base_url is None or api_key is None:
        try:
            from knowledge import config as kb_config
        except Exception:
            kb_config = None
        if base_url is None:
            base_url = getattr(kb_config, "EMBEDDING_BASE_URL", None)
        if api_key is None:
            api_key = getattr(kb_config, "EMBEDDING_API_KEY", None)

    key = (base_url, api_key)
    client = _embedding_clients.get(key)
    if client is None:
        if not HAS_OPENAI:
            raise RuntimeError("未安装 openai 库，无法使用 OpenAI 兼容 embedding。")
        if not base_url:
            raise RuntimeError(
                "EMBEDDING_BASE_URL 未配置，无法使用 OpenAI 兼容 embedding。"
            )
        client = OpenAI(api_key=api_key, base_url=base_url)
        _embedding_clients[key] = client
    return client


def get_client():
    """返回模块级单例 OpenAI client；未配置 key / 未安装 openai 时返回 None。"""
    global _client
    if _client is None:
        if not (HAS_OPENAI and LLM_API_KEY):
            return None
        # 构造函数传入 timeout 仅作默认；具体调用仍以 chat() 的 timeout 为准
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=LLM_TIMEOUT)
    return _client


def is_llm_available() -> bool:
    """配置了 key 且 openai 可用即为 True。"""
    return bool(HAS_OPENAI and LLM_API_KEY)


async def chat(messages, *, temperature=0.7, response_format=None, max_tokens=None) -> str:
    """便捷调用：异步返回模型文本（已 strip）。

    底层统一走 llm.model.achat_completion（ChatOpenAI → 内部 AsyncOpenAI），
    带连接级重试与兜底降级。任何超时 / 网络错误 / 返回空内容都向上抛，由调用方兜底降级。

    Args:
        messages: [{"role": "system"/"user"/"assistant", "content": "..."}]
        temperature / response_format / max_tokens: 透传给 Chat Model
    Returns:
        str: 模型回复文本（strip 后）
    """
    if not is_llm_available():
        raise RuntimeError("LLM 客户端不可用（未配置 LLM_API_KEY 或未安装 openai/langchain）")
    return await achat_completion(
        messages,
        temperature=temperature,
        response_format=response_format,
        max_tokens=max_tokens,
    )


def parse_json_safe(raw: str) -> dict:
    """尽力从 LLM 文本中解析 JSON（兼容 ```json 代码块包裹）。失败返回 {}。"""
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 包裹
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
