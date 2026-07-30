"""
RAG 语料加载器 — PDF / Markdown 文档解析

语料均为文本型 PDF（无扫描件），全程不需要 OCR。
pypdf 直接按页提取结构化文本，输出统一的 DocPage 列表供 splitter 切分。
"""
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DocPage:
    """单页文档单元"""
    doc_id: str
    source: str          # 文件名（含扩展名）
    title: str           # 文档标题（去扩展名）
    year: Optional[int]  # 发布年份（从文件名推断）
    category: str        # 类别：guidelines / consensus / pathways / internal
    page: int           # 页码（从 1 开始）
    text: str           # 该页纯文本


# 文件名年份识别：匹配 4 位 19xx/20xx
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _infer_year(filename: str) -> Optional[int]:
    m = _YEAR_RE.search(filename)
    return int(m.group(0)) if m else None


def _category_of(raw_dir: Path, path: Path) -> str:
    """根据相对 raw_dir 的子目录推断类别"""
    try:
        rel = path.relative_to(raw_dir)
        parts = rel.parts
        if len(parts) >= 2:
            return parts[0]            # 第一级子目录名
    except ValueError:
        pass
    return "internal"


def parse_pdf(path: str) -> List[tuple]:
    """
    解析单个文本型 PDF，返回 [(page_no, text), ...]。
    直接提取文字；无文本层（疑似扫描件）抛错。
    """
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        txt = txt.strip()
        pages.append((i, txt))

    # 全文档无任何文本层 → 疑似扫描件
    if not any(t.strip() for _, t in pages):
        raise ValueError(
            f"未检测到文本层：{os.path.basename(path)}（疑似扫描件）。"
            f"当前不支持 OCR，请替换为文本版 PDF 后再入库。"
        )
    return pages


def parse_markdown(path: str) -> List[tuple]:
    """Markdown / 文本型规范：整篇作为单页（page=1）"""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read().strip()
    return [(1, text)] if text else []


def load_documents(raw_dir=None) -> List[DocPage]:
    """
    扫描 raw_dir（含 guidelines/consensus/pathways/internal 子目录），
    加载所有 PDF / Markdown 为 DocPage 列表。
    """
    from . import config
    raw_dir = Path(raw_dir or config.RAW_DIR)
    if not raw_dir.exists():
        print(f"[RAG] 语料目录不存在: {raw_dir}")
        return []

    docs: List[DocPage] = []
    extensions = {".pdf", ".md", ".markdown", ".txt"}
    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        if path.name.startswith("README"):
            continue
        category = _category_of(raw_dir, path)
        title = path.stem
        year = _infer_year(path.name)
        doc_id = f"{category}/{path.name}"

        try:
            if path.suffix.lower() == ".pdf":
                pages = parse_pdf(str(path))
            else:
                pages = parse_markdown(str(path))
        except Exception as e:
            print(f"[RAG] 跳过 {path.name}: {e}")
            continue

        for page_no, text in pages:
            if not text.strip():
                continue
            docs.append(DocPage(
                doc_id=doc_id,
                source=path.name,
                title=title,
                year=year,
                category=category,
                page=page_no,
                text=text,
            ))
    print(f"[RAG] 已加载 {len(docs)} 页文档（来自 {raw_dir}）")
    return docs
