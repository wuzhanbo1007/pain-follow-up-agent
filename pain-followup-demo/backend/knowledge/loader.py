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

# HuggingFace 国内镜像：unstructured 表格结构识别需下载 table-transformer 模型，
# 直连 huggingface.co 内网不可达 → 走 hf-mirror.com 加速（仅未设置时生效）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from unstructured.partition.auto import partition

# ======================================================================
# OCR 引擎配置（Tesseract）
# 当前使用 conda 环境 tess-pkg 的 tesseract（带全量 125 种语言含 chi_sim）。
# 若用 winget 版（仅 eng/osd），或系统 PATH 里已有 tesseract，可删除下面覆盖。
# ======================================================================
_TESSERACT_CMD = os.getenv(
    "TESSERACT_CMD",
    r"C:\Users\LAN-IT-0272-1\.conda\envs\tess-pkg\Library\bin\tesseract.exe",
)
_TESSDATA_DIR = os.getenv(
    "TESSDATA_PREFIX",
    r"C:\Users\LAN-IT-0272-1\.conda\envs\tess-pkg\share\tessdata",
)
if os.path.exists(_TESSERACT_CMD):
    try:
        # unstructured 内部用 unstructured_pytesseract（pytesseract 的 fork），
        # 必须设置它的 tesseract_cmd，unstructured 的 OCR/表格路径才会生效
        import unstructured_pytesseract as _upt
        _upt.pytesseract.tesseract_cmd = _TESSERACT_CMD
    except Exception:
        pass
if os.path.isdir(_TESSDATA_DIR):
    os.environ.setdefault("TESSDATA_PREFIX", _TESSDATA_DIR)


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
    language: str = "zh"   # 文档语言：zh / en（由首元素检测推断，splitter 据此选择分块策略）


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


# 语言检测：按 CJK 字符占比启发式判断（无需额外模型，快且稳）
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")


def _detect_language(text: str) -> str:
    """粗略判断文本语言：中文/英文。

    逻辑：统计前 2000 字符中 CJK 汉字占比，
      · 汉字占比 ≥ 5%  → zh
      · 否则           → en
    覆盖中英混排（如中文指南里英文药名/术语），只要主体是中文即判 zh。
    """
    sample = (text or "")[:2000]
    if not sample.strip():
        return "zh"
    cjk = len(_CJK_RE.findall(sample))
    total = len(sample)
    ratio = cjk / total
    return "zh" if ratio >= 0.05 else "en"


# ======================================================================
# 解析引擎（Unstructured）
# ======================================================================

# 对 RAG 检索无用的元素类别：页眉/页脚/分页符，直接过滤。
# 注意：Image 不在此列——extract_images_in_pdf=True 会把图片里的文字 OCR 出来
# 存在 el.text 中，需要保留；真正无文字的图片在 _element_to_text 里 text 为空会被跳过。
_SKIP_CATEGORIES = {"Header", "Footer", "PageBreak"}

# 页眉/页脚启发式特征：纯页码、期刊名/卷期、DOI、作者单位行、杂志页眉等
_HEADER_FOOTER_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"(?:\d{1,4}\s*$)"                                        # 纯页码 "70" / "1"
    r"|(?:第?\s*\d{1,3}\s*卷?\s*第?\s*\d{1,3}\s*期?)"           # "第106卷第16期"
    r"|(?:Natl\s*Med\s*J\s*China)"                             # 期刊英文名
    r"|(?:DOI\s*:?\s*10\.\d+)"                                 # DOI 号
    r"|(?:\.{3,}|\.{2,})"                                      # 省略号/分隔符 "· · ·"
    r"|(?:[·•]\s*[·•]\s*[·•])"                                 # 装饰点
    r"|(?:p\.?\s*\d+)"                                         # "p.5" 页码
    r")\s*$"
)

# 期刊页眉常见关键词（页眉碎片里常出现）
_JOURNAL_HDR_WORDS = ("Med", "China", "中华医学杂志", "年第", "卷第", "期", "Vol", "No",
                      "DOI", "doi", "作者单位", "通信作者", "Email", "In Press",
                      "标准与规范", "标准·方案·指南", "·标准")


def _is_header_footer_text(text: str) -> bool:
    """启发式判断一段文本是否属于页眉/页脚（页码/期刊信息/DOI 等）。"""
    t = (text or "").strip()
    # 剥离 markdown 前缀（## / - / [表格] 等），以原文判断
    t = re.sub(r"^(?:#+\s*|-\s*|\[\w+\]\s*)+", "", t).strip()
    if not t:
        return True
    # 纯页码 / 期刊卷期 / DOI / 装饰分隔
    if _HEADER_FOOTER_RE.match(t):
        return True
    # 短文本 + 含期刊页眉关键词 → 高概率是页眉碎片
    if len(t) <= 60 and any(w in t for w in _JOURNAL_HDR_WORDS):
        # 排除真正的标题（如 "带状疱疹后神经痛诊疗专家共识（2026版）" 含"期"的概率低）
        return True
    return False


def _element_to_text(el) -> str:
    """把 Unstructured 元素转成可入库文本：
      - Table 优先用 text_as_html 保留行列结构（转 markdown 表）；
      - Title / ListItem 等加轻量前缀，便于 splitter 识别结构；
      - 其余返回纯文本。
    """
    category = el.category
    text = (el.text or "").strip()
    if not text:
        return ""

    # 表格：优先用 text_as_html（含 <table><tr><td>），转成 markdown 表格
    if category == "Table":
        html = getattr(el.metadata, "text_as_html", None) or getattr(el.metadata, "text_as_html", None)
        if html:
            md = _html_table_to_markdown(html)
            if md:
                return md
        return "[表格]\n" + text

    # 其他类别加轻量前缀
    prefix = {
        "Title":            "## ",
        "Header":           "## ",
        "ListItem":         "- ",
        "Formula":          "[公式] ",
        "Image":            "[图片文字]\n",
        "UncategorizedText": "",
    }.get(category, "")
    return prefix + text


def _html_table_to_markdown(html: str) -> str:
    """把 Unstructured 输出的 <table> HTML 转成 Markdown 表格（保留行列结构）。"""
    try:
        from html.parser import HTMLParser
    except ImportError:
        return ""

    class _TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []          # list[list[str]]
            self.cur_row = None
            self.cur_cell = None
            self.in_cell = False

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self.cur_row = []
            elif tag in ("td", "th"):
                self.in_cell = True
                self.cur_cell = []

        def handle_endtag(self, tag):
            if tag in ("td", "th"):
                self.in_cell = False
                if self.cur_row is not None and self.cur_cell is not None:
                    self.cur_row.append("".join(self.cur_cell).strip())
                self.cur_cell = None
            elif tag == "tr":
                if self.cur_row is not None and any(c for c in self.cur_row):
                    self.rows.append(self.cur_row)
                self.cur_row = None

        def handle_data(self, data):
            if self.in_cell and self.cur_cell is not None:
                self.cur_cell.append(data)

    p = _TableParser()
    p.feed(html)
    rows = p.rows
    if not rows:
        return ""

    # 列数 = 首行单元格数
    ncols = max(len(r) for r in rows)
    lines = []
    header = rows[0]
    lines.append("| " + " | ".join(_pad_cell(c, ncols) for c in header) + " |")
    lines.append("|" + "---|" * ncols)
    for r in rows[1:]:
        lines.append("| " + " | ".join(_pad_cell(c, ncols) for c in r) + " |")
    return "[表格]\n" + "\n".join(lines)


def _pad_cell(cell: str, ncols: int) -> str:
    """补齐单元格（不足 ncols 的空补）"""
    c = (cell or "").replace("\n", " ").strip()
    return c if c else " "


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
            #   auto    — 文本型 PDF 走 pdfminer（快），扫描件自动 OCR（需 Tesseract）
            #   hi_res  — 高精度 OCR + 布局分析（慢，但能识别表格位置）
            # infer_table_structure=True：提取表格行列结构（Table 元素带 text_as_html），
            #   依赖 table-transformer 模型（经 HF_ENDPOINT=hf-mirror.com 国内镜像下载，首次需等待）。
            strategy="auto",
            # 指定中英文（用于 PDF 元素分类和 OCR 回退）
            languages=["chi_sim+eng"],
            infer_table_structure=True,
            # 提取图片中的文字（医学 PDF 的流程图/量表截图常含关键信息）
            extract_images_in_pdf=True,
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
        # 过滤页眉/页脚/分页符/图片（对 RAG 检索无用）
        if el.category in _SKIP_CATEGORIES:
            continue

        meta = el.metadata
        page_num = meta.page_number or 1

        text = _element_to_text(el)
        if not text:
            continue

        # 启发式过滤页眉页脚碎片（纯页码/期刊卷期/DOI/装饰点等）。
        # 注意：auto 策略下页眉常被误分类为 Title，故对所有类别都套用启发式，
        # 仅真正正文段落（NarrativeText/ListItem/Table）不完全套用（避免误杀）。
        if _is_header_footer_text(text) and el.category not in ("NarrativeText", "ListItem", "Table"):
            continue

        if page_num not in pages_dict:
            pages_dict[page_num] = ""

        pages_dict[page_num] += text + "\n"

    # 转为 DocPage 列表
    # 语言：用全文合并样本检测（多页文档取全部文本做统计，避免单页标题误判）
    _full_text = "\n".join(v for k, v in sorted(pages_dict.items()))
    lang = _detect_language(_full_text)

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
            language=lang,
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
        full_text = []
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
            full_text.append(txt)
        lang = _detect_language("\n".join(full_text))
        for p in pages:
            p.language = lang
        print(f"  [loader] {path_obj.name}: 兜底 pypdf → {len(pages)} 页, "
              f"{sum(len(p.text) for p in pages)} 字, lang={lang}")
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
