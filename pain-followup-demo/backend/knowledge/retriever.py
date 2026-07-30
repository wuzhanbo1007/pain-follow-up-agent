"""
RAG 检索器（B 号 Agent 的知识检索封装）— 检索 + 引用溯源

对外暴露 retrieve_guidelines(query, diagnosis, k)，
返回带可溯源引用的片段列表（citation：指南名 + 年份 + 页码 + 章节 + 条款号）。
"""
import threading
from dataclasses import dataclass, field
from typing import List, Optional

from . import config
from .embeddings import get_embedding_provider
from .store import ChromaStore


@dataclass
class CitedChunk:
    text: str
    source: str
    title: str
    year: Optional[int]
    category: str
    page: Optional[int]
    section: str = ""
    clause_no: Optional[str] = None
    score: float = 0.0

    @property
    def citation(self) -> str:
        parts = [self.title]
        if self.year:
            parts.append(str(self.year))
        if self.page:
            parts.append(f"p.{self.page}")
        if self.clause_no:
            parts.append(f"第{self.clause_no}条")
        elif self.section:
            parts.append(self.section)
        return " · ".join(parts)


_STORE = None
_STORE_LOCK = threading.Lock()


def _get_store() -> ChromaStore:
    """获取（懒加载）全局 Chroma 检索单例。

    线程安全：用锁保证「冷启动」时只有一个线程去创建 PersistentClient，
    避免多个并发请求同时建库触发 Chroma 底层 SQLite 竞态（count 被静默吞成 0
    → 检索为空 → 计划占位）。见 store.count 的 except 吞异常逻辑。
    """
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                provider = get_embedding_provider()
                _STORE = ChromaStore(provider)
    return _STORE


def warm_store() -> None:
    """应用启动时主动预热检索单例并加载集合，确保首个请求不再冷启动竞态。"""
    try:
        store = _get_store()
        n = store.warm()
        print(f"[retriever] 向量库预热完成，集合「{store.collection_name}」共 {n} 条")
    except Exception as e:
        # 预热失败不应阻塞服务启动；真正出错会在首个检索请求时暴露
        print(f"[retriever] 预热向量库失败（首个检索请求时才会真正报错）: {e}")


def retrieve_guidelines(
    query: str,
    diagnosis: str = None,
    k: int = None,
    category: str = None,
) -> List[CitedChunk]:
    """
    检索与 query（患者诊断/病情）相关的指南与共识条款。

    :param query: 检索问句（通常含诊断+病情）
    :param diagnosis: 患者诊断，用于日志/后续精排（当前不硬过滤）
    :param k: top-k，默认 config.RETRIEVE_TOP_K
    :param category: 仅检索某类别（guidelines/consensus/pathways/internal）
    :return: CitedChunk 列表，按相似度降序
    """
    k = k or config.RETRIEVE_TOP_K
    store = _get_store()
    if store.count == 0:
        # 向量库为空：提示但尚未报错，由调用方降级处理
        return []
    hits = store.query(query, k=k, category=category)
    out: List[CitedChunk] = []
    for h in hits:
        m = h.get("metadata", {})
        out.append(CitedChunk(
            text=h["text"],
            source=m.get("source", ""),
            title=m.get("title", ""),
            year=_to_int(m.get("year")),
            category=m.get("category", ""),
            page=_to_int(m.get("page")),
            section=m.get("section", ""),
            clause_no=m.get("clause_no"),
            score=h.get("score", 0.0),
        ))
    return out


def _to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def rebuild_knowledge(raw_dir=None, target=None, overlap=None) -> int:
    """重跑 ingestion（解析→切分→embedding→入库），返回入库 chunk 数"""
    from .splitter import load_and_split
    from .embeddings import get_embedding_provider
    from .store import ChromaStore

    chunks = load_and_split(raw_dir, target, overlap)
    provider = get_embedding_provider()
    store = ChromaStore(provider)
    n = store.build(chunks)
    global _STORE
    _STORE = store
    return n
