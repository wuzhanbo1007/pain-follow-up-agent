"""
RAG 中文分块器 — 文档解析与分块

设计原则：
  - 先按页（DocPage）保持 page 元数据；
  - 页内按"中文标题层级"切分（一、 / 1. / 1.1 / 第X条 / 推荐意见X / 【摘要】等）；
  - 单段过长时按字符递归切分到目标长度（带重叠）；
  - 每块携带 {source, title, year, category, page, section, clause_no} 溯源元数据。
"""
import re
from dataclasses import dataclass, field
from typing import List

from .loader import DocPage

# 中文标题识别（行级）
_HEADING_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"(?:[一二三四五六七八九十]+、)"                              # 一、二、
    r"|(?:[1-9]\d?(?:\.\d+)*[.、．])"                       # 1. 1.1 2、
    r"|(?:第[一二三四五六七八九十百\d]+条)"                          # 第1条
    r"|(?:推荐意见\s*[1-9]\d?)"                                     # 推荐意见1
    r"|(?:【[^】]{1,12}】)"                                          # 【摘要】
    r")\s*(.+?)\s*$"
)

# 条款编号（用于 citation）：第X条 / 推荐意见X
_CLAUSE_RE = re.compile(r"第\s*([一二三四五六七八九十百\d]+)\s*条|推荐意见\s*([1-9]\d?)")

# 末页/参考文献等无需入库的段落
_SKIP_HEADINGS = {"参考文献", "利益冲突", "声明", "附录", "缩略语", "作者单位"}


@dataclass
class Chunk:
    text: str
    metadata: dict


def _is_heading(line: str):
    m = _HEADING_RE.match(line)
    if m:
        return m.group(1).strip()
    return None


def _clause_no(text: str):
    m = _CLAUSE_RE.search(text)
    if not m:
        return None
    return (m.group(1) or m.group(2)).strip()


def _recursive_split(text: str, target: int, overlap: int) -> List[str]:
    """超长文本递归切分（按字符，中文约 1 字≈0.6 token）"""
    if len(text) <= target:
        return [text] if text.strip() else []
    # 优先在换行处切
    step = max(1, target - overlap)
    pieces = []
    start = 0
    while start < len(text):
        end = min(start + target, len(text))
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end == len(text):
            break
        # 回退到上一个换行，避免生硬断句
        nl = text.rfind("\n", start, end)
        start = nl + 1 if nl > start else end
    return pieces


def split_pages(pages: List[DocPage], target: int = 800, overlap: int = 100) -> List[Chunk]:
    """
    将 DocPage 列表切分为带元数据的 Chunk。
    """
    chunks: List[Chunk] = []
    for doc in pages:
        if not doc.text.strip():
            continue
        base_meta = {
            "source": doc.source,
            "title": doc.title,
            "year": doc.year,
            "category": doc.category,
            "page": doc.page,
        }

        # 按换行切行，聚合成"标题 + 其下内容"
        lines = doc.text.split("\n")
        sections = []          # [(heading_text, content_lines)]
        cur_heading = "正文"
        cur_lines: List[str] = []
        for ln in lines:
            h = _is_heading(ln)
            if h:
                # 切换标题：先把上一节压入
                if cur_lines:
                    sections.append((cur_heading, cur_lines))
                cur_heading = h
                cur_lines = []
            else:
                cur_lines.append(ln)
        if cur_lines:
            sections.append((cur_heading, cur_lines))

        for heading, content in sections:
            if heading in _SKIP_HEADINGS:
                continue
            body = "\n".join(content).strip()
            if not body:
                continue
            clause = _clause_no(heading + " " + body[:200])
            meta = dict(base_meta)
            meta["section"] = heading
            if clause:
                meta["clause_no"] = clause
            for piece in _recursive_split(body, target, overlap):
                c = dict(meta)
                if clause is None:
                    # 段内再找一次条款
                    c2 = _clause_no(piece)
                    if c2:
                        c["clause_no"] = c2
                chunks.append(Chunk(text=piece, metadata=c))
    return chunks


def load_and_split(raw_dir=None, target: int = None, overlap: int = None) -> List[Chunk]:
    """加载语料并切分的一站式入口"""
    from . import config
    from .loader import load_documents
    pages = load_documents(raw_dir)
    t = target or config.CHUNK_TARGET_CHARS
    o = overlap or config.CHUNK_OVERLAP_CHARS
    return split_pages(pages, t, o)
