"""
Chroma 向量库封装 — 存储 / 检索

直接使用 chromadb（本地文件型，零运维，适合隐私内网 Demo），
embedding 由 embeddings.EmbeddingProvider 提供，与 LangChain 风格对齐。
"""
import re
import threading
import uuid
from typing import List

from . import config
from .embeddings import EmbeddingProvider


def _meta_filter(category: str = None, diagnosis: str = None):
    """将 category / diagnosis 转换为 Chroma where 过滤条件"""
    where = {}
    if category:
        where["category"] = category
    # diagnosis 暂不作为硬过滤（避免漏检），检索后按相关性排序即可
    return where or None


class ChromaStore:
    """指南/共识向量库"""

    def __init__(self, provider: EmbeddingProvider, persist_dir=None, collection=None):
        self.provider = provider
        self.persist_dir = str(persist_dir or config.CHROMA_DIR)
        self.collection_name = collection or config.CHROMA_COLLECTION
        self._client = None
        self._collection = None
        self._lock = threading.Lock()  # 保护「首次加载集合」的并发竞态

    def _embed_fn(self):
        # chromadb 1.5 embedding_function 接口：__call__ + embed_query + name()
        class _ChromaEmbedFn:
            def __init__(self, provider):
                self._provider = provider
            def name(self):
                return f"custom_{type(self._provider).__name__}"
            def __call__(self, input):
                return self._provider.embed(list(input))
            def embed_query(self, input):
                return self._provider.embed(list(input) if isinstance(input, list) else [input])
        return _ChromaEmbedFn(self.provider)

    def _get_client(self):
        if self._client is None:
            try:
                import chromadb
            except ImportError as e:
                raise RuntimeError(
                    "未安装 chromadb，请 `pip install chromadb`"
                ) from e
            self._client = chromadb.PersistentClient(path=self.persist_dir)
        return self._client

    def build(self, chunks) -> int:
        """将 Chunk 列表写入（覆盖重建）Chroma 集合"""
        client = self._get_client()
        # 重建集合，保证重跑 ingestion 幂等
        try:
            client.delete_collection(self.collection_name)
        except Exception:
            pass
        collection = client.create_collection(
            name=self.collection_name,
            embedding_function=self._embed_fn(),
            metadata={"hnsw:space": "cosine"},
        )
        docs, metas, ids = [], [], []
        for c in chunks:
            docs.append(c.text)
            metas.append({k: ("" if v is None else str(v)) for k, v in c.metadata.items()})
            ids.append(str(uuid.uuid4()))
        if docs:
            # 分批写入，避免单次过大
            B = 10
            for i in range(0, len(docs), B):
                collection.add(
                    documents=docs[i:i + B],
                    metadatas=metas[i:i + B],
                    ids=ids[i:i + B],
                )
        self._collection = collection
        return len(docs)

    def _collection_obj(self):
        if self._collection is None:
            # 双重检查锁：避免并发请求同时首次 get_collection 触发 Chroma
            # 内部租户/segment 索引竞态（表现：tenant 连不上 / bindings 缺失 /
            # KeyError 路径等），该异常被 count 吞掉后会误判为空库 → 计划占位。
            with self._lock:
                if self._collection is None:
                    client = self._get_client()
                    self._collection = client.get_collection(
                        name=self.collection_name,
                        embedding_function=self._embed_fn(),
                    )
        return self._collection

    def warm(self) -> int:
        """同步预热：单线程加载集合，避免首请求并发竞态。返回集合条数。"""
        return self._collection_obj().count()

    def query(self, text: str, k: int = 5, category: str = None) -> List[dict]:
        """向量相似检索，返回带元数据的片段"""
        col = self._collection_obj()
        where = _meta_filter(category)
        n = col.count()
        k = min(k, n) if n else 0
        if k == 0:
            return []
        res = col.query(
            query_texts=[text],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        out = []
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            out.append({
                "text": doc,
                "metadata": meta,
                "distance": float(dist),
                "score": round(1.0 - float(dist), 4),  # cosine 距离→相似度
            })
        return out

    @property
    def count(self) -> int:
        try:
            return self._collection_obj().count()
        except Exception:
            return 0
