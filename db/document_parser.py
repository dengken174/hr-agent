"""多格式文档解析 — 本地私有化，数据不出域。

支持格式:
  PDF/DOCX/DOC/PPTX/PPT/XLSX/XLS (markitdown + PyMuPDF)
  TXT/MD/CSV/JSON/XML/HTML (built-in)
  PNG/JPG/JPEG/TIFF/BMP (PaddleOCR)

解析引擎优先级:
  markitdown (Microsoft) → PyMuPDF (PDF回退) → PaddleOCR (图片)
"""

import io
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── 格式 → 分类 ──

OFFICE_FORMATS = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}
IMAGE_FORMATS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".gif", ".webp"}
TEXT_FORMATS = {".txt", ".md", ".rst", ".csv", ".json", ".xml", ".html", ".htm"}
SUPPORTED = OFFICE_FORMATS | IMAGE_FORMATS | TEXT_FORMATS


@dataclass
class ParsedDocument:
    title: str
    content: str           # markdown
    source_format: str     # 原始格式
    page_count: int = 1
    metadata: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════

async def parse_document(
    file_bytes: bytes,
    filename: str,
    *,
    enable_ocr: bool = False,
) -> ParsedDocument:
    """解析文件为 ParsedDocument。根据扩展名路由到对应解析器。"""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError(f"不支持的格式: {ext}，支持: {', '.join(sorted(SUPPORTED))}")

    title = Path(filename).stem

    # ── Office 文档: markitdown 优先 ──
    if ext in OFFICE_FORMATS:
        return await _parse_with_markitdown(file_bytes, filename, title, ext)

    # ── 图片: OCR ──
    if ext in IMAGE_FORMATS:
        if enable_ocr:
            return await _parse_image_ocr(file_bytes, title, ext)
        raise ValueError(f"图片格式 {ext} 需启用 OCR，设置 enable_ocr=True")

    # ── 纯文本 ──
    return await _parse_text(file_bytes, title, ext)


async def parse_document_batch(
    files: list[tuple[bytes, str]],
    enable_ocr: bool = False,
) -> list[ParsedDocument]:
    """批量解析文件。"""
    results = []
    for data, name in files:
        try:
            doc = await parse_document(data, name, enable_ocr=enable_ocr)
            results.append(doc)
        except Exception as e:
            logger.warning("Failed to parse %s: %s", name, e)
    return results


# ═══════════════════════════════════════════════════════════════════════
# markitdown 引擎 (Office → Markdown)
# ═══════════════════════════════════════════════════════════════════════

async def _parse_with_markitdown(
    data: bytes, filename: str, title: str, ext: str
) -> ParsedDocument:
    """markitdown 转换 Office 文档到 Markdown。"""
    from markitdown import MarkItDown

    md = MarkItDown()
    result = md.convert(io.BytesIO(data), file_extension=ext)

    content = result.text_content.strip()
    if not content:
        raise ValueError(f"markitdown 解析 {filename} 结果为空，文件可能损坏或为扫描件")

    meta = result.metadata or {}
    page_count = _detect_page_count(data, ext)

    return ParsedDocument(
        title=title,
        content=content,
        source_format=ext,
        page_count=page_count,
        metadata={
            "filename": filename,
            "parser": "markitdown",
            "doc_metadata": meta,
        },
    )


def _detect_page_count(data: bytes, ext: str) -> int:
    """检测 PDF/DOCX 页数，失败返回 1。"""
    if ext != ".pdf":
        return 1
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=data, filetype="pdf")
        count = doc.page_count
        doc.close()
        return count
    except Exception:
        return 1


# ═══════════════════════════════════════════════════════════════════════
# 纯文本解析
# ═══════════════════════════════════════════════════════════════════════

async def _parse_text(data: bytes, title: str, ext: str) -> ParsedDocument:
    """纯文本 / markdown / CSV / JSON 等直接读取。"""
    encoding = "utf-8"
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        try:
            text = data.decode("gbk")
            encoding = "gbk"
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")

    # CSV → markdown table
    if ext == ".csv":
        text = _csv_to_markdown(text)

    return ParsedDocument(
        title=title,
        content=text.strip(),
        source_format=ext,
        metadata={"filename": f"{title}{ext}", "parser": "builtin", "encoding": encoding},
    )


def _csv_to_markdown(text: str) -> str:
    import csv
    output = io.StringIO()
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return text
    # Header separator
    output.write("| " + " | ".join(rows[0]) + " |\n")
    output.write("| " + " | ".join(["---"] * len(rows[0])) + " |\n")
    for row in rows[1:]:
        output.write("| " + " | ".join(row) + " |\n")
    return output.getvalue()


# ═══════════════════════════════════════════════════════════════════════
# 图片 OCR (PaddleOCR)
# ═══════════════════════════════════════════════════════════════════════

_ocr: Optional[object] = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        from paddleocr import PaddleOCR
        _ocr = PaddleOCR(lang="ch", use_angle_cls=True, show_log=False)
    return _ocr


async def _parse_image_ocr(data: bytes, title: str, ext: str) -> ParsedDocument:
    """图片 OCR 识别中文文本。"""
    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    img_array = np.array(image)

    ocr = _get_ocr()
    results = ocr.ocr(img_array, cls=True)

    lines: list[str] = []
    if results and results[0]:
        for line in results[0]:
            text = line[1][0] if len(line) > 1 else str(line)
            lines.append(text)

    content = "\n".join(lines) if lines else ""
    if not content:
        raise ValueError(f"OCR 未能从图片中识别出文字")

    return ParsedDocument(
        title=title,
        content=content,
        source_format=ext,
        metadata={
            "filename": f"{title}{ext}",
            "parser": "paddleocr",
            "image_size": list(image.size),
        },
    )
