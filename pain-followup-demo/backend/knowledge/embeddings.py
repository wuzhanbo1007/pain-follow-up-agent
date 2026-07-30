"""
Embedding 提供方 — SiliconFlow / 本地模型向量化

默认 SiliconFlow 云端 Embedding API（OpenAI 兼容，模型 BAAI/bge-m3，无需本地 GPU）；
可选 bge-m3 本地推理（sentence-transformers）或任意 OpenAI 兼容接口（自托管 vLLM）。
懒加载重依赖，缺 openai / sentence-transformers 时本模块仍可导入（调用时才报错）。
"""
from abc import ABC, abstractmethod
from typing import List

from . import config


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        ...

    @property
    def dim(self) -> int:
        ...


class BgeM3LocalProvider(EmbeddingProvider):
    """本地 bge-m3（sentence-transformers / HuggingFace 权重）"""
    def __init__(self, model_name: str = None, dim: int = None):
        self.model_name = model_name or config.EMBEDDING_MODEL
        self._dim = dim or config.EMBEDDING_DIM
        self._model = None

    def _ensure(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise RuntimeError(
                    "未安装 sentence-transformers，无法使用 bge-m3 本地推理。"
                    "请 `pip install sentence-transformers torch`，或在 .env 设置 "
                    "EMBEDDING_PROVIDER=openai_compatible 走兼容接口。"
                ) from e
            import torch
            self._model = SentenceTransformer(self.model_name)
            # bge-m3 中文建议归一化
            self._model.max_seq_length = 512

    @property
    def dim(self):
        return self._dim

    def embed(self, texts: List[str]) -> List[List[float]]:
        self._ensure()
        # bge-m3 推荐在查询前加 "为这个句子生成表示以用于检索：" 前缀
        emb = self._model.encode(
            texts, normalize_embeddings=True,
            show_progress_bar=False, convert_to_numpy=True,
        )
        return [list(map(float, v)) for v in emb]


class OpenAICompatibleProvider(EmbeddingProvider):
    """OpenAI 兼容 embedding 接口（可指向本地 vLLM）"""
    def __init__(self, base_url: str = None, api_key: str = None,
                 model: str = None, dim: int = None):
        self.base_url = base_url or config.EMBEDDING_BASE_URL
        self.api_key = api_key or config.EMBEDDING_API_KEY
        self.model = model or config.EMBEDDING_MODEL
        self._dim = dim or config.EMBEDDING_DIM
        self._client = None

    def _ensure(self):
        if self._client is None:
            if not self.base_url:
                raise RuntimeError(
                    "EMBEDDING_BASE_URL 未配置，无法使用 OpenAI 兼容 embedding。"
                )
            # 复用统一 LLM 封装中的 embedding 单例（读 knowledge.config 的 EMBEDDING_*）
            from llm.client import get_embedding_client
            self._client = get_embedding_client(base_url=self.base_url, api_key=self.api_key)

    @property
    def dim(self):
        return self._dim

    def embed(self, texts: List[str]) -> List[List[float]]:
        self._ensure()
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [list(map(float, d.embedding)) for d in resp.data]


class SiliconFlowProvider(OpenAICompatibleProvider):
    """SiliconFlow 云端 Embedding API（OpenAI 兼容接口）

    默认指向 https://api.siliconflow.cn/v1，模型 BAAI/bge-m3（1024 维）。
    无需本地 GPU / 下载权重，适合内网 Demo 与快速验证。
    """
    def __init__(self, base_url: str = None, api_key: str = None,
                 model: str = None, dim: int = None):
        super().__init__(
            base_url=base_url or "https://api.siliconflow.cn/v1",
            api_key=api_key,
            model=model or "BAAI/bge-m3",
            dim=dim or 1024,
        )

    def _ensure(self):
        if self._client is None:
            if not self.api_key or self.api_key == "not-needed":
                raise RuntimeError(
                    "SiliconFlow 需要 EMBEDDING_API_KEY。请在 .env 配置 "
                    "EMBEDDING_API_KEY=sk-...（SiliconFlow 控制台获取）。"
                )
            # 复用统一 LLM 封装中的 embedding 单例（读 knowledge.config 的 EMBEDDING_*）
            from llm.client import get_embedding_client
            self._client = get_embedding_client(base_url=self.base_url, api_key=self.api_key)


class BailianProvider(OpenAICompatibleProvider):
    """阿里云百炼 Embedding API（OpenAI 兼容接口）

    默认指向 https://dashscope.aliyuncs.com/compatible-mode/v1，
    模型 text-embedding-v4（1024 维）。
    """
    def __init__(self, base_url: str = None, api_key: str = None,
                 model: str = None, dim: int = None):
        super().__init__(
            base_url=base_url or config.EMBEDDING_BASE_URL,
            api_key=api_key or config.EMBEDDING_API_KEY,
            model=model or config.EMBEDDING_MODEL,
            dim=dim or config.EMBEDDING_DIM,
        )


_PROVIDERS = {
    "bge_m3": BgeM3LocalProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "siliconflow": SiliconFlowProvider,
    "bailian": BailianProvider,
}


def get_embedding_provider(name: str = None) -> EmbeddingProvider:
    name = (name or config.EMBEDDING_PROVIDER).lower()
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"未知 Embedding 提供方: {name}，可选 {list(_PROVIDERS)}")
    return cls()
