# backend/knowledge/retriever.py
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


def _get_store():
    """获取（懒加载）全局检索单例（Elasticsearch 后端）。

    线程安全：用锁保证「冷启动」时只有一个线程去创建 store，
    避免多个并发请求同时初始化触发底层竞态（count 被静默吞成 0
    → 检索为空 → 计划占位）。
    """
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                provider = get_embedding_provider()
                from .es_store import EsStore
                _STORE = EsStore(provider)
    return _STORE


def warm_store() -> None:
    """应用启动时主动预热检索单例并加载集合，确保首个请求不再冷启动竞态。"""
    try:
        store = _get_store()
        n = store.warm()
        # 兼容 ChromaStore（collection_name）与 EsStore（index）
        name = getattr(store, "collection_name", None) or getattr(store, "index", "?")
        print(f"[retriever] 向量库预热完成，集合「{name}」共 {n} 条")
    except Exception as e:
        # 预热失败不应阻塞服务启动；真正出错会在首个检索请求时暴露
        print(f"[retriever] 预热向量库失败（首个检索请求时才会真正报错）: {e}")


def retrieve_guidelines(
    query: str = None,
    diagnosis: str = None,
    k: int = None,
    category: str = None,
    discharge_summary: str = None,
    symptoms: str = None,
) -> List[CitedChunk]:
    """
    检索与患者相关的指南与共识条款。

    :param query: 兼容参数。若不传 discharge_summary/symptoms，则 query 同时用于语义+关键词。
    :param diagnosis: 患者诊断，用于日志/后续精排（当前不硬过滤）
    :param k: top-k，默认 config.RETRIEVE_TOP_K
    :param category: 仅检索某类别（guidelines/consensus/pathways/internal）
    :param discharge_summary: 出院小结（做语义匹配 kNN，推荐传完整内容）。
        与 symptoms 同时提供时，两者合并为同一语义查询（kNN + reranker 共用），
        BM25 关键词通道仍只用 symptoms。
    :param symptoms: 症状/诊断关键词（做关键词匹配 BM25）
    :return: CitedChunk 列表，按相似度降序
    """
    k = k or config.RETRIEVE_TOP_K
    store = _get_store()
    if store.count == 0:
        # 向量库为空：提示但尚未报错，由调用方降级处理
        return []
    # 合并语义+关键词：kNN 与 reranker 查询同时带患者上下文 + 具体要点
    # （RAGAS A/B 验证：Recall 42.5→57.5%, Precision 50.9→65.6%）
    sem = discharge_summary or query
    kw = symptoms or query
    if discharge_summary and symptoms and symptoms not in sem:
        sem = f"{sem} {kw}"
    hits = store.query_hybrid(query, k=k, category=category,
                              semantic_text=sem,
                              keyword_text=kw)
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
    """重跑 ingestion（解析→切分→embedding→写入 ES），返回入库 chunk 数"""
    from .splitter import load_and_split
    from .embeddings import get_embedding_provider
    from .es_store import EsStore

    chunks = load_and_split(raw_dir, target, overlap)
    provider = get_embedding_provider()
    store = EsStore(provider)
    n = store.build(chunks)
    global _STORE
    _STORE = store
    return n
