"""
RAG 语料加载器 — 基于 Unstructured 的多格式文档解析

使用 unstructured 库统一解析 PDF / Markdown / TXT / DOCX 等格式，
自动处理文字提取、OCR（扫描件）、表格检测、元素分类等。

Unstructured 元素类型（部分）：
  Title, NarrativeText, ListItem, Table, Image, FigureCaption, Header, Footer, PageBreak
"""
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
from collections import Counter

from unstructured.partition.auto import partition


@dataclass
class DocPage:
    """单页文档单元（保持与原 loader 兼容）"""
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


# ======================================================================
# 解析引擎（Unstructured）
# ======================================================================

def parse_document(path: str) -> List[DocPage]:
    """用 Unstructured 解析单个文档，返回 DocPage 列表。

    支持格式：
      - PDF（文本型 + 扫描型 OCR）
      - Markdown / TXT
      - DOCX / DOC
      - 图片（含文字）

    Unstructured 自动处理：
      - 文件类型检测
      - 文字提取（文本型PDF）
      - OCR 识别（扫描型PDF，需安装 Tesseract + 中文语言包）
      - 表格检测与提取
      - 元素分类（标题/正文/列表/表格/图片描述等）

    兜底策略：若 Unstructured 返回 0 元素（部分 PDF 编码不被 pdfminer 兼容），
    自动回退到 pypdf 逐页提取纯文本，保证所有文字型 PDF 不丢失数据。

    Args:
        path: 文档路径

    Returns:
        DocPage 列表（按页码组织，每页合并该页所有元素文本）
    """
    path_obj = Path(path)
    title = path_obj.stem
    year = _infer_year(path_obj.name)

    # ---- 主路径：Unstructured 解析 ----
    try:
        elements = partition(
            filename=path,
            # 解析策略：
            #   auto    — 自动选择：有文字层则提取文字，无文字则走 OCR（需 Tesseract）
            #   fast    — 纯文本提取（最快，不识别表格/公式）
            #   hi_res  — 高精度 OCR（需 Tesseract，适合扫描件）
            strategy="auto",
            # 指定中英文（用于 PDF 元素分类和可能的 OCR 回退）
            languages=["chi_sim+eng"],
        )
    except Exception as e:
        # Unstructured 解析失败（如文件格式错误）→ 直接走兜底
        print(f"  [loader] {path_obj.name}: Unstructured 解析失败 ({type(e).__name__})，走 pypdf 兜底")
        elements = []

    # ---- 兜底：Unstructured 返回 0 元素 → 用 pypdf 逐页提取纯文本 ----
    if not elements:
        return _fallback_pypdf(path, path_obj, title, year)

    # ---- 主路径：按页码合并元素文本 ----
    pages_dict: dict[int, str] = {}

    for el in elements:
        meta = el.metadata
        page_num = meta.page_number or 1

        text = (el.text or "").strip()
        if not text:
            continue

        # 给不同元素类型加前缀标记（便于溯源和 splitter 识别结构）
        prefix = {
            "Title":            "## ",
            "Header":           "## ",
            "ListItem":         "- ",
            "Table":            "[表格]\n",
            "Image":            "[图片]\n",
            "FigureCaption":    "",
            "Formula":          "[公式] ",
            "UncategorizedText": "",
        }.get(el.category, "")

        if page_num not in pages_dict:
            pages_dict[page_num] = ""

        pages_dict[page_num] += prefix + text + "\n"

    # 转为 DocPage 列表
    pages = []
    for page_no in sorted(pages_dict.keys()):
        text = pages_dict[page_no].strip()
        if not text:
            continue
        pages.append(DocPage(
            doc_id=path_obj.name,
            source=path_obj.name,
            title=title,
            year=year,
            category="",       # load_documents 中会填充
            page=page_no,
            text=text,
        ))

    # 调试统计
    types = Counter(el.category for el in elements)
    print(f"  [loader] {path_obj.name}: {len(elements)} 个元素, "
          f"{sum(len(str(e)) for e in elements)} 字符, "
          f"类型: {dict(types)}")

    return pages


def _fallback_pypdf(path: str, path_obj: Path, title: str, year: Optional[int]) -> List[DocPage]:
    """兜底方案：用 pypdf 逐页提取纯文本（处理 pdfminer 不兼容的 PDF）。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            txt = (page.extract_text() or "").strip()
            if not txt:
                continue
            pages.append(DocPage(
                doc_id=path_obj.name,
                source=path_obj.name,
                title=title,
                year=year,
                category="",
                page=i,
                text=txt,
            ))
        print(f"  [loader] {path_obj.name}: 兜底 pypdf → {len(pages)} 页, "
              f"{sum(len(p.text) for p in pages)} 字")
        return pages
    except Exception as e:
        print(f"  [loader] {path_obj.name}: pypdf 兜底也失败 ({type(e).__name__})，跳过")
        return []


# ======================================================================
# 批量加载入口（兼容原接口）
# ======================================================================

def load_documents(raw_dir=None) -> List[DocPage]:
    """扫描 raw_dir（含 guidelines/consensus/pathways/internal 子目录），
    加载所有 PDF / TXT / MD / DOCX 为 DocPage 列表。

    Args:
        raw_dir: 语料根目录，默认 knowledge/config.RAW_DIR

    Returns:
        DocPage 列表
    """
    from . import config
    raw_dir = Path(raw_dir or config.RAW_DIR)
    if not raw_dir.exists():
        print(f"[RAG] 语料目录不存在: {raw_dir}")
        return []

    docs: List[DocPage] = []
    # 支持的扩展名（Unstructured 实际支持更多，这里列出已知可处理的）
    extensions = {".pdf", ".md", ".markdown", ".txt", ".docx", ".doc", ".html", ".htm"}

    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        if path.name.startswith("README"):
            continue

        category = _category_of(raw_dir, path)

        try:
            pages = parse_document(str(path))
            # 回填 category（parse_document 中拿不到 raw_dir 信息）
            for p in pages:
                p.category = category
            docs.extend(pages)
        except Exception as e:
            print(f"[RAG] 跳过 {path.name}: {e}")

    print(f"[RAG] 已加载 {len(docs)} 页文档（来自 {raw_dir}）")
    return docs


# ======================================================================
# 兼容旧接口：仅解析 PDF（内部调用 Unstructured）
# ======================================================================

def parse_pdf(path: str) -> List[tuple]:
    """兼容旧版 parse_pdf 接口，返回 [(page_no, text), ...]

    内部仍使用 Unstructured，保证与旧 splitter 兼容。
    """
    pages = parse_document(path)
    return [(p.page, p.text) for p in pages]


# ======================================================================
# 独立测试入口
# ======================================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        pages = parse_document(sys.argv[1])
        for p in pages:
            print(f"\n--- 第{p.page}页 ({p.title}, {p.category}) ---")
            print(p.text[:500])
        print(f"\n共 {len(pages)} 页")
    else:
        print("用法: python loader.py <文件路径>")
