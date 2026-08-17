# backend/knowledge/reranker.py
"""
检索精排器 — bge-reranker (cross-encoder) 对召回候选逐对打分精排

- 懒加载：首次 rerank 时才加载模型（缓存单例），避免拖慢进程启动。
- 用 transformers 直接加载 BAAI/bge-reranker-large（中文医学适用）。
- 模型下载走 HF_ENDPOINT=hf-mirror.com（内网镜像）。

用法：
    from knowledge.reranker import get_reranker
    ranked = get_reranker().rerank(query, items, top_k=5)
    # items: [{"text": "...", "metadata": {...}, "score": 0.0}, ...]
"""
import os
import threading
from typing import List

from . import config

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

DEFAULT_MODEL = "BAAI/bge-reranker-large"
_MAX_TEXT_CHARS = 1500   # 候选文本截断，控制输入长度
_BATCH = 24              # 推理批大小


class Reranker:
    def __init__(self, model_name: str = None):
        self.model_name = model_name or DEFAULT_MODEL
        self._tok = None
        self._model = None

    def _ensure(self):
        if self._model is None:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            self._tok = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            self._model.eval()

    def rerank(self, query: str, items: List[dict], top_k: int = 5) -> List[dict]:
        """对候选 items 逐对打分，返回按相关度降序的前 top_k 个 item（保留原字段）。"""
        if not items:
            return []
        self._ensure()
        import torch

        pairs = [[query, (it.get("text") or "")[:_MAX_TEXT_CHARS]] for it in items]
        scores: List[float] = []
        for i in range(0, len(pairs), _BATCH):
            enc = self._tok(pairs[i:i + _BATCH], padding=True, truncation=True,
                            max_length=512, return_tensors="pt")
            with torch.no_grad():
                logits = self._model(**enc, return_dict=True).logits.view(-1).float()
            scores.extend(logits.tolist())

        ranked = sorted(zip(items, scores), key=lambda x: x[1], reverse=True)
        out = []
        for it, s in ranked[:top_k]:
            it["score"] = float(s)
            out.append(it)
        return out


class GiteeReranker:
    """Gitee AI 云端 bce-reranker（sentence-similarity API），免本地模型/GPU。

    POST {api_url}
      {"model": "bce-reranker-base_v1",
       "inputs": {"source_sentence": query, "sentences": [候选文本...]},
       "normalize": true}
    返回：与 sentences 一一对应的相似度分数数组。
    注：Gitee reranker 与 embedding 是不同资源包，需单独 RERANKER_API_KEY。
    """
    _API_BATCH = 16   # 单次请求的候选数上限（Gitee 对 sentences 数量/长度有限制）

    def __init__(self, api_url: str = None, api_key: str = None, model: str = None):
        self.api_url = api_url or config.RERANKER_API_URL
        self.api_key = api_key or config.RERANKER_API_KEY
        self.model = model or config.RERANKER_MODEL

    def rerank(self, query: str, items: List[dict], top_k: int = 5) -> List[dict]:
        """调用 Gitee 相似度 API 打分，返回按相关度降序的前 top_k 个 item。"""
        if not items:
            return []
        if not self.api_key:
            raise RuntimeError("RERANKER_API_KEY 未配置，无法使用 Gitee reranker。")
        import requests

        texts = [(it.get("text") or "")[:_MAX_TEXT_CHARS] for it in items]
        scores: List[float] = []
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for i in range(0, len(texts), self._API_BATCH):
            batch = texts[i:i + self._API_BATCH]
            payload = {
                "model": self.model,
                "inputs": {"source_sentence": query, "sentences": batch},
                "normalize": True,
            }
            resp = requests.post(self.api_url, json=payload, headers=headers, timeout=90)
            if resp.status_code != 200:
                raise RuntimeError(f"Gitee reranker HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(f"Gitee reranker 报错: {data['error']}")
            scores.extend(float(s) for s in data)

        ranked = sorted(zip(items, scores), key=lambda x: x[1], reverse=True)
        out = []
        for it, s in ranked[:top_k]:
            it["score"] = float(s)
            out.append(it)
        return out


_RERANKER = None
_RERANKER_LOCK = threading.Lock()


def get_reranker():
    """按 config.RERANKER_PROVIDER 返回精排器单例（local=本地 bge-reranker / gitee=云端 API）"""
    global _RERANKER
    if _RERANKER is None:
        with _RERANKER_LOCK:
            if _RERANKER is None:
                if config.RERANKER_PROVIDER == "gitee":
                    _RERANKER = GiteeReranker()
                else:
                    _RERANKER = Reranker()
    return _RERANKER
