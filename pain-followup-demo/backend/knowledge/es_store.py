"""
Elasticsearch 向量库封装 — 混合检索（BM25 + 向量 kNN + RRF 融合）

提供 build / query / count / delete 接口，
retriever.py 切换 store 即可，上游 Agent 无感。

索引设计（单索引）：
  pain_guidelines_kb
    - text          : text          chunk 正文
    - embedding     : dense_vector  1024维, cosine, int8_hnsw
    - source/title/year/category/page/section/clause_no/content_type/language : 元数据

混合检索（ES 8.8+ retriever RRF）：
  BM25 全文（text） + 向量 kNN（embedding） → Reciprocal Rank Fusion 融合排序

依赖：elasticsearch 客户端（pip install elasticsearch）
"""
import threading
import uuid
from typing import List, Optional

from . import config
from .embeddings import get_embedding_provider

# ===== 中文分词（jieba 预分词，解决 BM25 中文拆单字问题）=====
# ES 服务器未装 IK/smartcn 插件，standard 把中文拆单字。
# 方案：入库时用 jieba 切词，存 keywords 字段（空格分隔），
#       standard 按空格切分正好保留完整中文词 → BM25 精确匹配。
import jieba


def _cn_tokenize(text: str) -> str:
    """jieba 精确模式分词，返回空格分隔的词串（供 keywords 字段）。"""
    if not text:
        return ""
    words = [w.strip() for w in jieba.cut(text) if w.strip()]
    # 过滤纯标点/单字噪声（保留医学关键词）
    import re
    keep = []
    for w in words:
        if re.fullmatch(r"[\s\W_]+", w):   # 纯符号跳过
            continue
        keep.append(w)
    return " ".join(keep)


# ===== 混合检索辅助（Python 手动 RRF 融合 / hit 转 item）=====
def _rrr_fuse(hits_lists: list, top_n: int, rank_constant: int = 60,
              weights: list = None) -> list:
    """Reciprocal Rank Fusion：对多个通道的 ES hits 按排名融合，返回前 top_n 个 hit。

    weights: 各通道权重（与 hits_lists 顺序对应）；None 时所有通道等权。
      某通道权重 >1 表示更信任该通道（如关键词权重调高，过滤「语义像但无关」的陷阱文档）。
    """
    from collections import defaultdict
    weights = weights or [1.0] * len(hits_lists)
    scores = defaultdict(float)
    for w, hits in zip(weights, hits_lists):
        for rank, h in enumerate(hits):
            scores[h["_id"]] += w / (rank_constant + rank + 1)
    top_ids = {i for i, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]}
    merged = [h for h in hits_lists[0] + hits_lists[1] if h["_id"] in top_ids]
    return sorted(merged, key=lambda h: scores[h["_id"]], reverse=True)[:top_n]


def _hit_to_item(h: dict) -> dict:
    """把 ES hit 转成 query()/query_hybrid() 统一返回的 item 结构。"""
    src = h["_source"]
    return {
        "text": src.get("text", ""),
        "metadata": {
            "source": src.get("source", ""),
            "title": src.get("title", ""),
            "year": src.get("year"),
            "category": src.get("category", ""),
            "page": src.get("page"),
            "section": src.get("section", ""),
            "clause_no": src.get("clause_no", ""),
            "content_type": src.get("content_type", "text"),
            "language": src.get("language", "zh"),
        },
        "score": 0.0,
    }


# ===== ES 连接配置（从 config 读取，config 已加载 .env）=====
_ES_HOST = config.ES_HOST
_ES_USER = config.ES_USERNAME
_ES_PASS = config.ES_PASSWORD
_ES_INDEX = config.ES_INDEX


# ===== 索引 mapping =====
INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
    "mappings": {
        "dynamic": "strict",
        "properties": {
            # text 仅用于 embedding 输入（语义检索），不建倒排索引 → BM25 不搜它
            "text":          {"type": "text", "analyzer": "standard", "index": False},
            # jieba 预分词后的关键词（空格分隔，standard 按空格切分保留完整词），BM25 专用
            "keywords":      {"type": "text", "analyzer": "standard"},
            "embedding": {
                "type": "dense_vector",
                "dims": 1024,
                "index": True,
                "similarity": "cosine",
                "index_options": {"type": "int8_hnsw", "m": 16, "ef_construction": 100},
            },
            "source":       {"type": "keyword"},
            "title":        {"type": "keyword"},
            "year":         {"type": "integer"},
            "category":     {"type": "keyword"},
            "page":         {"type": "integer"},
            "section":      {"type": "keyword"},
            "clause_no":    {"type": "keyword"},
            "content_type": {"type": "keyword"},
            "language":     {"type": "keyword"},
        },
    },
}


class EsStore:
    """Elasticsearch 向量库（知识库唯一后端）"""

    def __init__(self, provider=None, es_host=None, es_user=None, es_pass=None,
                 index=None, collection=None):
        self.provider = provider or get_embedding_provider()
        self.es_host = es_host or _ES_HOST
        self.es_user = es_user or _ES_USER
        self.es_pass = es_pass or _ES_PASS
        self.index = index or collection or _ES_INDEX
        self._client = None
        self._lock = threading.Lock()

    # ===== 客户端 =====
    def _get_client(self):
        if self._client is None:
            try:
                from elasticsearch import Elasticsearch
            except ImportError as e:
                raise RuntimeError("未安装 elasticsearch，请 `pip install elasticsearch`") from e
            # 8.x 默认 https，这里用 http 明文
            self._client = Elasticsearch(
                self.es_host,
                basic_auth=(self.es_user, self.es_pass),
                request_timeout=60,
                verify_certs=False,
            )
        return self._client

    def _ensure_index(self):
        """索引不存在则创建"""
        client = self._get_client()
        if not client.indices.exists(index=self.index):
            client.indices.create(index=self.index, body=INDEX_MAPPING)

    # ===== 入库 =====
    def _chunks_to_docs(self, chunks) -> list:
        """把 Chunk 列表转成 bulk 写入的 actions"""
        actions = []
        for c in chunks:
            m = c.metadata or {}
            doc = {
                "text": c.text,
                "keywords": _cn_tokenize(c.text),   # jieba 预分词
                "embedding": self.provider.embed([c.text])[0],
                "source": m.get("source", ""),
                "title": m.get("title", ""),
                "year": m.get("year"),
                "category": m.get("category", ""),
                "page": m.get("page"),
                "section": m.get("section", ""),
                "clause_no": m.get("clause_no", ""),
                "content_type": m.get("content_type", "text"),
                "language": m.get("language", "zh"),
            }
            actions.append({"index": {"_index": self.index, "_id": str(uuid.uuid4())}})
            actions.append(doc)
        return actions

    def _bulk_write(self, actions: list) -> None:
        """分批写入 ES"""
        if not actions:
            return
        client = self._get_client()
        B = 100
        for i in range(0, len(actions), B * 2):
            batch = actions[i:i + B * 2]
            client.bulk(operations=batch, refresh=True)

    def build(self, chunks) -> int:
        """将 Chunk 列表写入（覆盖重建）ES 索引"""
        client = self._get_client()
        # 重建索引（幂等）
        if client.indices.exists(index=self.index):
            client.indices.delete(index=self.index)
        client.indices.create(index=self.index, body=INDEX_MAPPING)

        actions = self._chunks_to_docs(chunks)
        self._bulk_write(actions)
        return len(chunks)

    def append(self, chunks) -> int:
        """追加 Chunk 到现有索引（不清空，只加新文档）"""
        self._ensure_index()
        actions = self._chunks_to_docs(chunks)
        self._bulk_write(actions)
        return len(chunks)

        if actions:
            # 分批写入，避免单次过大
            B = 100
            for i in range(0, len(actions), B * 2):
                batch = actions[i:i + B * 2]
                client.bulk(operations=batch, refresh=True)
        return len(chunks)

    # ===== 检索（混合：BM25 + kNN + RRF）=====
    def query(self, text: str = None, k: int = 5, category: str = None,
              semantic_text: str = None, keyword_text: str = None) -> List[dict]:
        """混合检索，返回 [{text, metadata, score}]。

        Args:
            text: 兼容参数。若不传 semantic_text/keyword_text，则 text 同时用于两者。
            semantic_text: kNN 语义检索用的文本（推荐传完整出院小结/病情描述）。
            keyword_text:  BM25 关键词检索用的文本（推荐传症状/诊断关键词）。
            k: top-k
            category: 元数据过滤
        """
        client = self._get_client()
        if not client.indices.exists(index=self.index):
            return []

        # 兼容：只传 text 时，语义和关键词都用它
        sem = semantic_text or text or ""
        kw = keyword_text or text or ""
        if not sem and not kw:
            return []

        # 语义查询转向量（用 semantic_text，如完整出院小结）
        query_vec = self.provider.embed([sem])[0]

        # 混合检索：BM25(keyword_text) + kNN(semantic_text)，RRF 融合
        filters = []
        if category:
            filters.append({"term": {"category": category}})

        body = {
            "size": k,
            "retriever": {
                "rrf": {
                    "window_size": 50,
                    "rank_constant": 60,
                    "retrievers": [
                        {
                            "standard": {
                                "query": {
                                    "match": {
                                        # 只搜 keywords（jieba 切好的中文词），text 仅做 embedding
                                        "keywords": _cn_tokenize(kw or sem),
                                    }
                                }
                            }
                        },
                        {
                            "knn": {
                                "field": "embedding",
                                "query_vector": query_vec,
                                "k": k * 4,
                                "num_candidates": 500,
                            }
                        },
                    ],
                }
            },
        }
        if filters:
            body["retriever"]["rrf"]["retrievers"][1]["knn"]["filter"] = filters

        try:
            resp = client.search(index=self.index, body=body)
        except Exception:
            # RRF 不支持时降级为纯 kNN
            resp = client.search(index=self.index, body={
                "size": k,
                "knn": {
                    "field": "embedding",
                    "query_vector": query_vec,
                    "k": k,
                    "num_candidates": 500,
                    **({"filter": filters} if filters else {}),
                },
            })

        out = []
        for hit in resp.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            out.append({
                "text": src.get("text", ""),
                "metadata": {
                    "source": src.get("source", ""),
                    "title": src.get("title", ""),
                    "year": src.get("year"),
                    "category": src.get("category", ""),
                    "page": src.get("page"),
                    "section": src.get("section", ""),
                    "clause_no": src.get("clause_no", ""),
                    "content_type": src.get("content_type", "text"),
                    "language": src.get("language", "zh"),
                },
                "score": float(hit.get("_score", 0.0)),
                "distance": 0.0,
            })
        return out

    # ===== 混合检索（BM25 + kNN → RRF → reranker）=====
    def query_hybrid(self, text: str = None, k: int = 5, category: str = None,
                     semantic_text: str = None, keyword_text: str = None) -> List[dict]:
        """BM25 + kNN 双通道召回 → Python RRF 融合 top-50 → bge-reranker 精排 top-k。

        ES 原生 RRF 需 license（当前降级纯 kNN），此处用 Python 手动实现融合，
        并叠加 cross-encoder 精排，提升 Precision。
        """
        client = self._get_client()
        if not client.indices.exists(index=self.index):
            return []

        sem = semantic_text or text or ""
        kw = keyword_text or text or ""
        if not sem and not kw:
            return []

        query_vec = self.provider.embed([sem])[0]
        kw_tokens = _cn_tokenize(kw or sem)
        CAND = 50

        filters = []
        if category:
            filters.append({"term": {"category": category}})

        # 通道1：BM25（jieba 分词后的 keywords 字段）
        bm25_body = {"size": CAND, "query": {"match": {"keywords": kw_tokens}}}
        if filters:
            bm25_body["query"] = {"bool": {"must": bm25_body["query"], "filter": filters}}
        # 通道2：kNN（语义向量）
        knn_body = {"size": CAND, "knn": {"field": "embedding", "query_vector": query_vec,
                                          "k": CAND, "num_candidates": 500}}
        if filters:
            knn_body["knn"]["filter"] = filters

        try:
            r1 = client.search(index=self.index, body=bm25_body)
            r2 = client.search(index=self.index, body=knn_body)
        except Exception:
            # 召回异常 → 降级为原有 query（纯 kNN / 兼容逻辑）
            return self.query(text, k=k, category=category,
                              semantic_text=semantic_text, keyword_text=keyword_text)

        fused = _rrr_fuse([r1["hits"]["hits"], r2["hits"]["hits"]], top_n=CAND,
                          weights=[config.RRF_KEYWORD_WEIGHT, 1.0])  # 关键词通道权重可配
        items = [_hit_to_item(h) for h in fused]

        # reranker 精排（模型懒加载；失败则用 RRF 融合排序结果）
        try:
            from .reranker import get_reranker
            return get_reranker().rerank(sem, items, top_k=k)
        except Exception:
            return items[:k]

    # ===== 工具 =====
    @property
    def count(self) -> int:
        try:
            client = self._get_client()
            if not client.indices.exists(index=self.index):
                return 0
            resp = client.count(index=self.index)
            return int(resp.get("count", 0))
        except Exception:
            return 0

    def warm(self) -> int:
        return self.count

    def delete(self) -> None:
        client = self._get_client()
        if client.indices.exists(index=self.index):
            client.indices.delete(index=self.index)
