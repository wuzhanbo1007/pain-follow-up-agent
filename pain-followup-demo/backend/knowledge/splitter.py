"""
RAG 多策略分块器 — 支持固定大小 / 结构感知 / 语义分块，多语言（中文/英文）

设计原则：
  - 先按页（DocPage）保持 page 元数据；
  - 页内按"标题层级"切分：
      中文 → 一、 / 1. / 1.1 / 第X条 / 推荐意见X / 【摘要】
      英文 → "1. Introduction" / "3.2 Methods" / 全大写/首字母大写章节词
  - 单段过长时递归切分到目标长度（带重叠）；
    中英文目标长度不同（英文按 token 折算需更长字符数）；
  - 每个 Chunk 携带 {source, title, year, category, page, section, clause_no, language, content_type}。

分块策略（strategy 参数）：
  - "fixed"     固定大小 + 重叠（按字符硬切，简单基线）
  - "structure" 结构感知（按标题/章节/表格/条款切块，超长再递归切）——推荐
  - "semantic"  语义分块（用 embedding 检测句子间语义突变，在话题转变处切块）

content_type 标签（对表格/条款/代码/正文分组报告用）：
  - "table"  表格
  - "clause" 条款/推荐意见
  - "code"   代码
  - "text"   普通正文
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .loader import DocPage

# ===== 中文标题识别（行级）=====
# 涵盖 PDF 常见标题格式：序号/条款/括号序号/表格图标题/英文缩写+中文
_HEADING_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"(?:[一二三四五六七八九十]+、)"                              # 一、二、
    r"|(?:[1-9]\d?(?:\.\d+)*[.、．])"                       # 1. 1.1 2、
    r"|(?:（[一二三四五六七八九十]+）)"                            # （一）（二）
    r"|(?:\([一二三四五六七八九十]+\))"                            # (一)(二)
    r"|(?:第[一二三四五六七八九十百\d]+条)"                          # 第1条
    r"|(?:推荐意见\s*[1-9]\d?)"                                     # 推荐意见1
    r"|(?:【[^】]{1,12}】)"                                          # 【摘要】
    r"|(?:表\s*\d+[\s\-]?)"                                         # 表1 表2-1
    r"|(?:图\s*\d+[\s\-]?)"                                         # 图1 图2-1
    r")\s*(.+?)\s*$"
)

# 英文缩写开头 + 中文标题（如 "PHN 的发病机制"）
# 中文部分限制：长度 1-12，含"的/等/类"等结构词，不含标点和长句
_ENCN_HEADING_RE = re.compile(
    r"^\s*([A-Z]{2,10})\s*(?:[、．.：:\s]|的)?\s*([一-鿿]{1,12})$"
)
# 排除包含动词/连词或明显是正文句的（"作为/因/并/影响/需要/见图"等）
_CN_BODY_END_RE = re.compile(r"(作为|原因|影响|需要|进行|出现|引起|导致|包括|分为|用于|来自|并在|其中|见图|见附|以及|以及|通过|采用)")

# 纯中文章节标题（短行、无句号结尾、非正文句）——如 "发病机制" "流行病学"
# 允许含顿号（"癌痛筛查、评估及诊断"）
_CN_WORD_HEADING_RE = re.compile(
    r"^\s*([一-鿿、及和与]{2,15})$"
)
# 常见的纯中文章节词（避免把短正文句误判为标题）
_CN_SECTION_WORDS = {
    "概述", "背景", "发病机制", "流行病学", "诊断", "治疗", "预防",
    "讨论", "结论", "随访", "预后", "并发症", "临床表现", "病理生理",
    "治疗原则", "药物选择", "评估方法", "筛查", "危险因素", "定义",
    "总结", "推荐意见", "参考文献", "证据等级", "病因", "分类",
    "症状", "诊断标准", "处理原则", "预防措施", "随访管理",
}
# 组合式章节标题后缀（"癌痛评估概述" = "癌痛评估"+"概述"）
_CN_TITLE_SUFFIX = ("概述", "评估", "原则", "流程", "路径", "方法", "分类",
                    "病因", "诊断", "治疗", "管理", "筛查", "机制", "定义",
                    "方法学", "使用者", "工作组", "委员会", "专家", "内容",
                    "步骤", "要点", "标准", "工具")
# 组合式章节标题前缀（避免把正文误判）
_CN_TITLE_PREFIX = ("癌痛", "疼痛", "神经", "慢性", "急性", "术后", "老年",
                    "儿童", "肿瘤", "癌性", "带状", "腰椎", "盆腔", "头痛",
                    "偏头痛", "纤维", "骨关", "风湿", "糖尿病", "共识",
                    "出院", "随访", "用药", "镇痛", "首诊", "复诊",
                    "入院", "围术期", "居家", "常用", "罕见", "难治",
                    "辅助", "综合")

# ===== 英文标题识别（行级）=====
# 1) 数字编号标题："1. Introduction" / "3.2 Methods"
_EN_NUM_HEADING_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)*)[.\s]+\s*([A-Z][A-Za-z0-9\s\-&/()]{2,80})$"
)
# 2) 纯文字章节词（Introduction / Methods / Results / Discussion ...）
_EN_WORD_HEADING_RE = re.compile(
    r"^\s*([A-Z][a-z]+(?:\s+[a-z]+)*):?\s*$"
)
# 常见英文章节词（避免把普通大写句首误判为标题）
_EN_SECTION_WORDS = {
    "Introduction", "Background", "Methods", "Materials", "Results",
    "Discussion", "Conclusion", "Conclusions", "Summary", "Abstract",
    "Key Points", "Recommendations", "Guideline", "Guidelines",
    "Clinical Evidence", "Evidence", "References", "Limitations",
    "Definitions", "Scope", "Appendices",
}

# 条款编号（用于 citation）：中文第X条 / 推荐意见X
_CLAUSE_RE = re.compile(r"第\s*([一二三四五六七八九十百\d]+)\s*条|推荐意见\s*([1-9]\d?)")

# 末页/参考文献等无需入库的段落（中英）
_SKIP_HEADINGS = {
    "参考文献", "利益冲突", "声明", "附录", "缩略语", "作者单位",
    "References", "Conflict of Interest", "Declaration", "Appendix",
}

# ===== content_type 识别 =====
_TABLE_MARK_RE = re.compile(r"^\s*\[表格\]", re.MULTILINE)
_CODE_MARK_RE = re.compile(r"^\s*```|^\s*[;{}]\s*$|^\s*(def|class|import|from)\s", re.MULTILINE)


def _classify_content_type(text: str, section: str = "") -> str:
    """判断 chunk 内容类型：table / clause / code / text"""
    s = (section or "").strip()
    t = (text or "").strip()
    # 表格：以 [表格] 开头
    if _TABLE_MARK_RE.search(t):
        return "table"
    # 代码：含代码块标记或常见代码语法
    if _CODE_MARK_RE.search(t):
        return "code"
    # 条款：章节名含"条/推荐意见"或正文含"第X条/推荐意见X/【强】/【中】"
    if re.search(r"条|推荐意见", s):
        return "clause"
    if _CLAUSE_RE.search(t[:200]):
        return "clause"
    if re.search(r"【(强|中|弱|弱推荐)】\s*\d", t[:200]):
        return "clause"
    return "text"


@dataclass
class Chunk:
    text: str
    metadata: dict


_MARKDOWN_PREFIX_RE = re.compile(r"^\s*(#{1,6}\s*)+")  # 剥离 loader 加的 "## " 前缀


def _is_heading(line: str, language: str = "zh"):
    """识别标题行。中文走 _HEADING_RE；英文走 _EN_*_RE。

    loader 会给 Unstructured 的 Title/Header 元素加 "## " 前缀，
    先剥离 markdown 前缀再匹配，避免前缀导致识别失败。
    注意：条款行（【强】N. / 第X条）不是标题，应留在正文由 _split_clauses 处理。
    """
    s = _MARKDOWN_PREFIX_RE.sub("", line)
    # 排除条款行（推荐意见条目）
    if _CLAUSE_ITEM_RE.match(s):
        return None
    if language == "en":
        return _is_en_heading(s)
    # 主模式：序号/括号序号/表格图标题等
    m = _HEADING_RE.match(s)
    if m:
        return m.group(1).strip()
    # 英文缩写 + 中文（"PHN 的发病机制"）→ 保留完整 "PHN 的发病机制"
    m = _ENCN_HEADING_RE.match(s)
    if m:
        # 排除正文句（如 "PHN 作为...严重影响生活" 以动词/连词结尾）
        if _CN_BODY_END_RE.search(s):
            return None
        return f"{m.group(1)} {m.group(2)}".strip()
    # 纯中文章节词（短行、无句号、名词性结尾）——如 "发病机制" "癌痛评估概述"
    m = _CN_WORD_HEADING_RE.match(s)
    if m:
        word = m.group(1).strip()
        # 词表内
        if word in _CN_SECTION_WORDS:
            return word
        # 组合式标题：医学前缀 + 章节后缀（"癌痛评估概述"），且无正文特征
        if word.endswith(_CN_TITLE_SUFFIX) and word.startswith(_CN_TITLE_PREFIX):
            if not _CN_BODY_END_RE.search(word):
                return word
    return None


def _is_en_heading(line: str):
    """英文标题识别（宽松启发式，避免把正文大写句首误判为标题）。"""
    s = line.strip()
    if not s:
        return None
    # 数字编号标题："1. Introduction" / "3.2 Methods"
    m = _EN_NUM_HEADING_RE.match(s)
    if m:
        # 正文很少以"数字+句点"开头（除非是编号条目），视为标题
        return f"{m.group(1)} {m.group(2)}".strip()
    # 纯文字章节词
    m = _EN_WORD_HEADING_RE.match(s)
    if m:
        word = m.group(1)
        # 只有常见章节词/全大写缩写才判标题，避免正文大写句首误判
        if word in _EN_SECTION_WORDS or (word.isupper() and len(word) <= 12):
            return word
    return None


def _clause_no(text: str):
    m = _CLAUSE_RE.search(text)
    if not m:
        return None
    return (m.group(1) or m.group(2)).strip()


def _recursive_split(text: str, target: int, overlap: int,
                     language: str = "zh") -> List[str]:
    """超长文本递归切分（按字符，带重叠）。

    中英文断句策略不同：
      · 中文约 1 字≈0.6 token，目标 800 字符 ≈ 480 token
      · 英文约 1 词≈1.3 token，目标 1600 字符 ≈ 300-400 token
    切分回退点：优先换行 → 中文回退到中文标点 → 英文回退到空格。
    """
    if len(text) <= target:
        return [text] if text.strip() else []
    pieces = []
    start = 0
    while start < len(text):
        end = min(start + target, len(text))
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end == len(text):
            break
        # 回退点：换行 > 语言标点/空格，避免生硬断句
        nl = text.rfind("\n", start, end)
        if nl > start:
            start = nl + 1
            continue
        if language == "en":
            sp = text.rfind(" ", start, end)
            if sp > start:
                start = sp + 1
                continue
        else:
            # 中文回退到句号/分号/逗号
            for sep in ("。", "；", "，", "、"):
                idx = text.rfind(sep, start, end)
                if idx > start:
                    start = idx + 1
                    break
            else:
                start = end
    return pieces


# ======================================================================
# 策略 1：固定大小 + 重叠
# ======================================================================
def _split_fixed(text: str, target: int, overlap: int) -> List[str]:
    """固定大小硬切 + 重叠（纯字符，不考虑结构）。"""
    if len(text) <= target:
        return [text] if text.strip() else []
    pieces = []
    start = 0
    step = max(1, target - overlap)
    while start < len(text):
        end = min(start + target, len(text))
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end == len(text):
            break
        start += step
    return pieces


# ======================================================================
# 策略 3：语义分块（用 embedding 检测语义突变）
# ======================================================================
def _split_semantic(text: str, target: int, overlap: int,
                    language: str = "zh") -> List[str]:
    """语义分块：按句切分 → 计算相邻句相似度 → 在低相似度处切块。

    用 embedding 余弦相似度衡量相邻句子的话题延续性，相似度显著下降处
    视为"话题转变"点，在边界处切块。fallback：若 embedding 不可用，
    退化为固定大小分块。
    """
    # 按句子切分（中文句号/问号/感叹号/换行；英文句点/问号）
    if language == "en":
        sents = re.split(r"(?<=[.!?])\s+|\n+", text)
    else:
        sents = re.split(r"(?<=[。！？；])\s*|\n+", text)
    sents = [s.strip() for s in sents if s.strip()]
    if len(sents) <= 1:
        return _split_fixed(text, target, overlap)

    # 计算相邻句相似度（用 embedding）
    try:
        from .embeddings import get_embedding_provider
        provider = get_embedding_provider()
        vecs = provider.embed(sents)
        # 归一化并计算相邻余弦相似度
        import math
        norms = [math.sqrt(sum(v * v for v in vec)) or 1.0 for vec in vecs]
        sims = []
        for i in range(len(vecs) - 1):
            dot = sum(a * b for a, b in zip(vecs[i], vecs[i + 1]))
            sims.append(dot / (norms[i] * norms[i + 1]))
    except Exception:
        # embedding 不可用 → 退化为固定分块
        return _split_fixed(text, target, overlap)

    # 在低相似度处切块（相似度 < 中位数 - 0.15 视为突变）
    import statistics
    mid = statistics.median(sims) if sims else 0.6
    threshold = max(0.1, mid - 0.15)

    pieces = []
    cur = []
    cur_len = 0
    for i, sent in enumerate(sents):
        cur.append(sent)
        cur_len += len(sent)
        # 话题突变（且当前块已足够大）→ 切块
        if i < len(sims) and sims[i] < threshold and cur_len >= target * 0.6:
            pieces.append("".join(cur))
            cur = []
            cur_len = 0
        # 块过长 → 强制切
        elif cur_len >= target:
            pieces.append("".join(cur))
            cur = []
            cur_len = 0
    if cur:
        pieces.append("".join(cur))
    return [p for p in pieces if p.strip()]


# ======================================================================
# 结构感知分块（策略 2，推荐）——保留原有标题层级切分 + content_type
# ======================================================================
def _split_structure(doc: DocPage, target: int, overlap: int,
                     language: str = "zh") -> List[Chunk]:
    """结构感知 + 内容类型感知分块（推荐策略）。

    按内容类型选择切分方式：
      - 表格     → 整个表格一块（不切分，保留行列结构）
      - 条款     → 一条一块（【强】N. / 第X条 / 推荐意见N 独立成块）
      - 代码     → 按逻辑块切（20-50 行，识别 ``` 块）
      - 正文     → 按标题分节 + 递归字符切（500-800 字）
    """
    chunks: List[Chunk] = []
    base_meta = {
        "source": doc.source,
        "title": doc.title,
        "year": doc.year,
        "category": doc.category,
        "page": doc.page,
        "language": language,
    }

    # 按行切分，聚合成"标题 + 其下内容"
    lines = doc.text.split("\n")
    sections = []          # [(heading_text, content_lines)]
    cur_heading = "正文"
    cur_lines: List[str] = []
    for ln in lines:
        h = _is_heading(ln, language)
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

        # —— 表格：整表一块（不递归切，保留行列）——
        if body.startswith("[表格]") or "[表格]" in body[:20]:
            meta = dict(base_meta)
            meta["section"] = heading
            meta["content_type"] = "table"
            chunks.append(Chunk(text=body, metadata=meta))
            continue

        # —— 代码块：按 ``` 逻辑块切 ——
        if "```" in body:
            chunks.extend(_split_code_blocks(body, base_meta, heading))
            continue

        # —— 条款：每条独立成块 ——
        clause_parts = _split_clauses(body)
        if clause_parts and len(clause_parts) > 1:
            for part in clause_parts:
                meta = dict(base_meta)
                meta["section"] = heading
                meta["content_type"] = "clause"
                cn = _clause_no(part[:200])
                if cn:
                    meta["clause_no"] = cn
                chunks.append(Chunk(text=part.strip(), metadata=meta))
            continue

        # —— 正文：按标题分节 + 递归字符切 ——
        clause = _clause_no(heading + " " + body[:200])
        meta = dict(base_meta)
        meta["section"] = heading
        if clause:
            meta["clause_no"] = clause
        for piece in _recursive_split(body, target, overlap, language):
            c = dict(meta)
            if clause is None:
                # 段内再找一次条款
                c2 = _clause_no(piece)
                if c2:
                    c["clause_no"] = c2
            c["content_type"] = _classify_content_type(piece, c.get("section", ""))
            chunks.append(Chunk(text=piece, metadata=c))
    return chunks


# ===== 条款拆分：一条一块 =====
_CLAUSE_ITEM_RE = re.compile(
    r"(?m)^\s*(?:【[强弱中]】\s*)?\d+[\.、．]\s*"   # 【强】1. / 1. / 1、
    r"|^\s*(?:第[一二三四五六七八九十百\d]+条\s*)"      # 第1条
    r"|^\s*(?:推荐意见\s*[1-9]\d?[\.、．\s]*)"          # 推荐意见1
)


def _split_clauses(body: str) -> List[str]:
    """把"核心推荐意见"节拆成独立条款列表（每条一个字符串）。"""
    parts = []
    cur = []
    for line in body.split("\n"):
        if _CLAUSE_ITEM_RE.match(line):
            if cur:
                parts.append("\n".join(cur).strip())
            cur = [line]
        else:
            cur.append(line)
    if cur:
        parts.append("\n".join(cur).strip())
    # 只有明确拆出 2 条以上才算条款节，否则当作普通正文
    return [p for p in parts if p.strip()] if len(parts) > 1 else []


# ===== 代码块：按 ``` 逻辑块切 =====
def _split_code_blocks(body: str, base_meta: dict, heading: str) -> List[Chunk]:
    """把含 ``` 的正文按代码块 + 文本段分别切块。"""
    chunks: List[Chunk] = []
    # 按代码块切分（保留 ``` 标记）
    segs = re.split(r"(```.*?```)", body, flags=re.DOTALL)
    for seg in segs:
        seg = seg.strip()
        if not seg:
            continue
        meta = dict(base_meta)
        meta["section"] = heading
        if seg.startswith("```"):
            meta["content_type"] = "code"
        else:
            meta["content_type"] = "text"
        chunks.append(Chunk(text=seg, metadata=meta))
    return chunks


def split_pages(pages: List[DocPage], target: int = 800, overlap: int = 100,
                strategy: str = "structure") -> List[Chunk]:
    """
    将 DocPage 列表切分为带元数据的 Chunk。

    Args:
        pages: DocPage 列表
        target: 中文目标字符数（英文 ×2）
        overlap: 重叠字符数（英文 ×2）
        strategy: 分块策略 "fixed" / "structure" / "semantic"
    """
    chunks: List[Chunk] = []
    for doc in pages:
        if not doc.text.strip():
            continue
        language = doc.language or "zh"
        # 分块参数按语言切换
        if language == "en":
            t = target * 2          # 英文：1600 字符 ≈ 300-400 token
            o = overlap * 2
        else:
            t = target
            o = overlap

        # 按策略分块
        if strategy == "fixed":
            # 固定大小：整页文本硬切，保留基础元数据
            base_meta = {
                "source": doc.source,
                "title": doc.title,
                "year": doc.year,
                "category": doc.category,
                "page": doc.page,
                "language": language,
            }
            for piece in _split_fixed(doc.text, t, o):
                c = dict(base_meta)
                c["content_type"] = _classify_content_type(piece)
                chunks.append(Chunk(text=piece, metadata=c))
        elif strategy == "semantic":
            # 语义分块：整页文本按语义突变切块
            base_meta = {
                "source": doc.source,
                "title": doc.title,
                "year": doc.year,
                "category": doc.category,
                "page": doc.page,
                "language": language,
            }
            for piece in _split_semantic(doc.text, t, o, language):
                c = dict(base_meta)
                c["content_type"] = _classify_content_type(piece)
                chunks.append(Chunk(text=piece, metadata=c))
        else:
            # 默认：结构感知
            chunks.extend(_split_structure(doc, t, o, language))
    return chunks


def load_and_split(raw_dir=None, target: int = None, overlap: int = None,
                   strategy: str = "structure") -> List[Chunk]:
    """加载语料并切分的一站式入口"""
    from . import config
    from .loader import load_documents
    pages = load_documents(raw_dir)
    t = target or config.CHUNK_TARGET_CHARS
    o = overlap or config.CHUNK_OVERLAP_CHARS
    return split_pages(pages, t, o, strategy=strategy)
