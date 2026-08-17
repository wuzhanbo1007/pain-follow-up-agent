# backend/knowledge/ingest.py
"""
RAG 批量入库流程

用法（在 backend 目录下）：
    python -m knowledge.ingest            # 解析 raw/ → 切分 → embedding → 写入 ES
    python -m knowledge.ingest --status  # 仅打印当前向量库状态

依赖：
    pip install elasticsearch pypdf sentence-transformers torch
（bge-m3 首次运行会自动从 HuggingFace 下载权重）
"""
import argparse
import os
import sys
from pathlib import Path

# 确保 backend 在 sys.path，支持直接 python knowledge/ingest.py 运行
_BACKEND = str(Path(__file__).resolve().parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from . import config
from .retriever import rebuild_knowledge


def main():
    parser = argparse.ArgumentParser(description="PainSmart RAG 知识库入库")
    parser.add_argument("--status", action="store_true", help="仅打印向量库状态")
    parser.add_argument("--raw", default=None, help="语料目录（默认 knowledge_base/raw）")
    parser.add_argument("--target", type=int, default=None, help="分块目标字符数")
    parser.add_argument("--overlap", type=int, default=None, help="分块重叠字符数")
    args = parser.parse_args()

    if args.status:
        try:
            from .retriever import _get_store
            store = _get_store()
            print(f"[RAG] 索引={config.ES_INDEX} 路径={config.ES_HOST} 文档数={store.count}")
        except Exception as e:
            print(f"[RAG] 状态获取失败：{e}")
        return

    print(f"[RAG] 开始入库：raw={args.raw or config.RAW_DIR}")
    try:
        n = rebuild_knowledge(args.raw, args.target, args.overlap)
        print(f"[RAG] OK: 入库完成，共 {n} 个 chunk")
        print(f"      索引={config.ES_INDEX} 写入 {config.ES_HOST}")
    except Exception as e:
        print(f"[RAG] FAIL: 入库失败：{e}")
        print("      请确认已安装 elasticsearch / pypdf，且 bge-m3 可下载（或改用 OpenAI 兼容 embedding）。")
        sys.exit(1)


if __name__ == "__main__":
    main()
