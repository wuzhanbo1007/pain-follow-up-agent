"""
PainSmart RAG 知识库包
对外暴露：
  - retrieve_guidelines(query, diagnosis, k)  → 带溯源引用的指南/共识检索（B 号 Agent 用）
  - rebuild_knowledge(raw_dir)               → 重跑 ingestion
  - ChromaStore / EmbeddingProvider         → 底层组件
"""
from .retriever import CitedChunk, retrieve_guidelines, rebuild_knowledge
from .store import ChromaStore
from .embeddings import EmbeddingProvider, get_embedding_provider
from .splitter import Chunk, split_pages, load_and_split
from .loader import DocPage, load_documents, parse_pdf

__all__ = [
    "retrieve_guidelines",
    "rebuild_knowledge",
    "CitedChunk",
    "ChromaStore",
    "EmbeddingProvider",
    "get_embedding_provider",
    "Chunk",
    "split_pages",
    "load_and_split",
    "DocPage",
    "load_documents",
    "parse_pdf",
]
