# backend/knowledge/api_routes.py
"""
RAG 知识库 REST 路由（FastAPI APIRouter）
注册到 app.py：app.include_router(knowledge_router)

ES 查询/写入及 embedding 计算均为同步阻塞 I/O；路由统一为同步 def，
由 FastAPI 线程池托管执行，无需 asyncio.to_thread。
"""
from fastapi import APIRouter, Query, HTTPException

from knowledge.retriever import retrieve_guidelines, rebuild_knowledge
from knowledge import config

knowledge_router = APIRouter(tags=["knowledge"])


@knowledge_router.get("/api/knowledge/status")
def api_kb_status():
    from knowledge.es_store import EsStore
    from knowledge.embeddings import get_embedding_provider

    def _get_count():
        store = EsStore(get_embedding_provider())
        return store.count
    try:
        count = _get_count()
    except Exception as e:
        return {"ok": False, "error": str(e), "count": 0}
    return {"ok": True, "count": count, "collection": config.ES_INDEX}


@knowledge_router.post("/api/knowledge/ingest")
def api_kb_ingest():
    """重建知识库索引（耗时的文档切分+embedding 计算）"""
    try:
        n = rebuild_knowledge()
        return {"ok": True, "ingested": n}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"ok": False, "error": str(e)})


@knowledge_router.get("/api/knowledge/search")
def api_kb_search(
    q: str = Query(""),
    diagnosis: str = Query(""),
    k: int = Query(config.RETRIEVE_TOP_K),
):
    """语义检索知识库（embedding 向量 + ES 查询）"""
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "缺少查询参数 q"})
    try:
        hits = retrieve_guidelines(q, diagnosis=diagnosis, k=k)
    except Exception as e:
        return {"ok": False, "error": str(e), "results": []}
    # 脱敏序列化（保留 citation 元数据）
    results = [{
        "text": h.text,
        "citation": h.citation,
        "source": h.source,
        "title": h.title,
        "year": h.year,
        "page": h.page,
        "section": h.section,
        "clause_no": h.clause_no,
        "score": h.score,
    } for h in hits]
    return {"ok": True, "count": len(results), "results": results}
