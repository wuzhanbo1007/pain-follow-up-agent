"""
RAG 知识库配置 — 从 .env / 环境变量读取
RAG 知识库配置（向量库 / 嵌入 / 分块参数）

路径说明（相对 backend/knowledge/config.py）：
  backend/knowledge/config.py
    .parent            = backend/knowledge
    .parent.parent     = backend
    .parent.parent.parent = pain-followup-demo
    .parent.parent.parent.parent = PainSmart6.0
因此知识库根目录 = PainSmart6.0/knowledge_base
"""
import os
from pathlib import Path

_HERE = Path(__file__).resolve()
# config.py 位于 backend/knowledge/config.py
#   .parent            = backend/knowledge
#   .parent.parent     = backend
#   .parent.parent.parent = pain-followup-demo
#   .parent.parent.parent.parent = PainSmart6.0
_PROJECT_ROOT = _HERE.parent.parent.parent.parent   # PainSmart6.0
_KB_ROOT = _PROJECT_ROOT / "knowledge_base"

# ===== 语料与向量库路径 =====
# 原始语料目录（PDF / Markdown / SQLite 导出），可用环境变量覆盖
RAW_DIR = Path(os.getenv("KNOWLEDGE_RAW_DIR", str(_KB_ROOT / "raw")))
# Chroma 持久化目录
CHROMA_DIR = Path(os.getenv("KNOWLEDGE_CHROMA_DIR", str(_KB_ROOT / "vector_store")))
# Chroma 集合名
CHROMA_COLLECTION = os.getenv("KNOWLEDGE_COLLECTION", "pain_guidelines")

# ===== Embedding 配置 =====
# 提供方：
#   siliconflow    —— SiliconFlow 云端 Embedding API（OpenAI 兼容，默认，免本地 GPU）
#   bge_m3         —— 本地 sentence-transformers 推理（需 torch + 下载权重）
#   openai_compatible —— 任意 OpenAI 兼容 embedding 接口（自托管 vLLM 等）
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "siliconflow")
# bge-m3 模型名（SiliconFlow / sentence-transformers / HuggingFace 通用）
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
# SiliconFlow OpenAI 兼容 embedding 接口
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "sk-ekndvkvsmkxcqlobywnbwqwuigaoaupuwgszlrgvxbxqpmal")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))   # bge-m3 默认 1024 维

# ===== 分块参数（中文） =====
# 文档要求 chunk ≈ 512–1024 token；中文约 1 字≈0.6 token，
# 故按字符数控制：目标 500–1000 字，重叠 80–120 字（≈ 10–15%）。
CHUNK_TARGET_CHARS = int(os.getenv("CHUNK_TARGET_CHARS", "800"))
CHUNK_OVERLAP_CHARS = int(os.getenv("CHUNK_OVERLAP_CHARS", "100"))

# ===== 检索参数 =====
RETRIEVE_TOP_K = int(os.getenv("RETRIEVE_TOP_K", "5"))
