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

# 加载 backend/.env（确保 RAG 相关配置从 .env 读取）
try:
    from dotenv import load_dotenv
    _BACKEND_DIR = Path(__file__).resolve().parent.parent   # backend/
    load_dotenv(_BACKEND_DIR / ".env")
except ImportError:
    pass  # python-dotenv 未安装时直接读系统环境变量

_HERE = Path(__file__).resolve()
# config.py 位于 backend/knowledge/config.py
#   .parent            = backend/knowledge
#   .parent.parent     = backend
#   .parent.parent.parent = pain-followup-demo
#   .parent.parent.parent.parent = PainSmart6.0
_PROJECT_ROOT = _HERE.parent.parent.parent.parent   # PainSmart6.0
_KB_ROOT = _PROJECT_ROOT / "knowledge_base"

# ===== 语料路径 =====
# 原始语料目录（PDF / Markdown / SQLite 导出），可用环境变量覆盖
RAW_DIR = Path(os.getenv("KNOWLEDGE_RAW_DIR", str(_KB_ROOT / "raw")))

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
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))   # bge-m3 默认 1024 维

# ===== ES 向量库配置 =====
ES_HOST = os.getenv("ES_HOST", "http://192.168.2.210:9200")
ES_USERNAME = os.getenv("ES_USERNAME", "elastic")
ES_PASSWORD = os.getenv("ES_PASSWORD", "")
ES_INDEX = os.getenv("ES_INDEX", "pain_guidelines_kb")

# ===== 分块参数（中文） =====
# 文档要求 chunk ≈ 512–1024 token；中文约 1 字≈0.6 token，
# 故按字符数控制：目标 500–1000 字，重叠 80–120 字（≈ 10–15%）。
CHUNK_TARGET_CHARS = int(os.getenv("CHUNK_TARGET_CHARS", "800"))
CHUNK_OVERLAP_CHARS = int(os.getenv("CHUNK_OVERLAP_CHARS", "100"))
# 每条证据给 LLM 看的摘要长度（字符）：默认取半个 chunk 目标（800/2=400），
# 让模型看到 chunk 主体（约 60%），而非只看开头 30%。可经环境变量覆盖。
PROMPT_EXCERPT_CHARS = int(os.getenv("PROMPT_EXCERPT_CHARS", str(CHUNK_TARGET_CHARS // 2)))

# ===== Reranker 配置 =====
# 提供方：local（本地 transformers 推理，bge-reranker-large） / gitee（Gitee AI 云端 bce-reranker API）
RERANKER_PROVIDER = os.getenv("RERANKER_PROVIDER", "local")
RERANKER_API_URL = os.getenv("RERANKER_API_URL", "https://ai.gitee.com/v1/sentence-similarity")
RERANKER_API_KEY = os.getenv("RERANKER_API_KEY", "")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "bce-reranker-base_v1")
# 注：Gitee 的 reranker 与 embedding 是不同资源包，EMBEDDING_API_KEY 无 reranker 权限，
#     需单独配 RERANKER_API_KEY。

# ===== 检索参数 =====
RETRIEVE_TOP_K = int(os.getenv("RETRIEVE_TOP_K", "5"))
# RRF 融合中关键词(BM25)通道的相对权重：>1 表示更信任关键词命中（过滤「语义像但无关」的陷阱文档），
# 默认 1.0 为等权（BM25 与 kNN 地位相同）。实验时经环境变量调高（如 1.5/2.0）。
RRF_KEYWORD_WEIGHT = float(os.getenv("RRF_KEYWORD_WEIGHT", "1.0"))
