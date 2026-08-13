"""智能文档分块 — 中文语义感知。

策略:
  1. 按 markdown 标题层级(H1-H6)粗切
  2. 段落内按中文句界精切（不截断句子）
  3. 父子块关联：小粒度 chunk 用于检索，父级 section 用于上下文

参考: LangChain RecursiveCharacterTextSplitter + 中文适配
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# 中文句界分隔符 — 从粗到细
_CN_SEPARATORS = [
    "\n\n",     # 段落
    "\n",       # 换行
    "。",       # 句号
    "！", "？",  # 感叹/疑问
    "；",       # 分号
    "，",       # 逗号 (最后手段)
]

# Markdown 标题模式
_H_PATTERN = re.compile(r"^#{1,6}\s+", re.MULTILINE)


@dataclass
class Chunk:
    text: str
    title: str           # 所在章节标题
    chunk_index: int     # 在文档中的序号
    parent_section: str  # 父级章节完整文本（用于上下文扩展）
    metadata: dict = field(default_factory=dict)


def chunk_document(
    content: str,
    doc_title: str = "",
    *,
    chunk_size: int = 500,
    chunk_overlap: int = 80,
    min_chunk_size: int = 30,
) -> list[Chunk]:
    """将 markdown 文档智能切分为检索友好的 chunk 列表。

    Args:
        content: markdown 格式文档全文
        doc_title: 文档标题
        chunk_size: 目标 chunk 大小（字符数，中文约 500 字 = ~1000 tokens）
        chunk_overlap: 相邻 chunk 重叠字符数
        min_chunk_size: 最小 chunk 大小，短于此值合并到上一块
    """
    if not content.strip():
        return []

    # ── Step 1: 按标题层级粗切为 sections ──
    sections = _split_by_headers(content)
    if not sections:
        return []

    # ── Step 2: 每个 section 内精切 ──
    chunks: list[Chunk] = []
    chunk_idx = 0

    for section_title, section_text in sections:
        # 标题行本身作为 context
        header_line = f"# {section_title}" if section_title else ""

        # 将 section 文本按中文句界细切
        sub_texts = _split_text_recursive(
            section_text,
            separators=_CN_SEPARATORS,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        for sub in sub_texts:
            text = sub.strip()
            if len(text) < min_chunk_size:
                # 太短的块合并到上一个
                if chunks and len(chunks[-1].text) + len(text) < chunk_size * 1.5:
                    chunks[-1].text += "\n" + text
                    continue
                elif not text:
                    continue
                # 孤立的短块保留（可能是关键信息）
            chunks.append(Chunk(
                text=text,
                title=section_title or doc_title,
                chunk_index=chunk_idx,
                parent_section=section_text.strip(),
                metadata={"doc_title": doc_title, "section": section_title or doc_title},
            ))
            chunk_idx += 1

    return chunks


# ═══════════════════════════════════════════════════════════════════════
# 标题层级切分
# ═══════════════════════════════════════════════════════════════════════

def _split_by_headers(text: str) -> list[tuple[str, str]]:
    """按 markdown 标题将文本切分为 (标题, 内容) 的 sections。"""
    lines = text.split("\n")
    sections: list[tuple[str, str]] = []
    current_title = ""
    current_lines: list[str] = []

    for line in lines:
        m = _H_PATTERN.match(line)
        if m:
            # 保存上一个 section
            if current_lines:
                sections.append((current_title, "\n".join(current_lines)))
            current_title = _H_PATTERN.sub("", line).strip()
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_title, "\n".join(current_lines)))

    # 如果只有一个 section 且无标题，整个文档作为一块
    if len(sections) == 1 and not sections[0][0]:
        sections = [(sections[0][0], text)]

    return sections


# ═══════════════════════════════════════════════════════════════════════
# 递归语义切分
# ═══════════════════════════════════════════════════════════════════════

def _split_text_recursive(
    text: str,
    separators: list[str],
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """递归按分隔符切分，优先用粗粒度分隔符。"""
    if not text.strip():
        return []

    # 文本已足够短
    if len(text) <= chunk_size:
        return [text]

    # 当前层级分隔符
    sep = separators[0] if separators else ""
    next_separators = separators[1:] if len(separators) > 1 else []

    if sep:
        splits = _split_with_keep_sep(text, sep)
    else:
        # 无分隔符可用，强制按字符数切分
        return _split_by_length(text, chunk_size, chunk_overlap)

    # 递归处理每个分段
    result: list[str] = []
    for split in splits:
        result.extend(_split_text_recursive(split, next_separators, chunk_size, chunk_overlap))

    # 合并过短的相邻 chunk
    return _merge_short_chunks(result, chunk_size, chunk_overlap)


def _split_with_keep_sep(text: str, sep: str) -> list[str]:
    """按分隔符切分，但分隔符保留在右侧（作为开头的延续）。"""
    parts = text.split(sep)
    if len(parts) <= 1:
        return [text]

    # 分隔符加到每个片段开头（除第一个）
    result = [parts[0]]
    for p in parts[1:]:
        result.append(sep + p)
    return [r for r in result if r]


def _split_by_length(text: str, chunk_size: int, overlap: int) -> list[str]:
    """无分隔符时的最后手段：按字符数等长切分。"""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        # 尝试在句号后断开
        if end < len(text):
            for punct in ["。", "！", "？", "\n", "；"]:
                pos = text.rfind(punct, start, end)
                if pos > start + chunk_size // 2:
                    end = pos + 1
                    break
        chunks.append(text[start:end])
        start = end - overlap if end < len(text) else end
    return chunks


def _merge_short_chunks(chunks: list[str], chunk_size: int, overlap: int) -> list[str]:
    """合并过短的 chunk 到前一个。"""
    if not chunks:
        return []
    min_size = chunk_size // 3
    merged = []
    for c in chunks:
        if merged and len(c) < min_size and len(merged[-1]) + len(c) < chunk_size * 1.3:
            merged[-1] += c
        else:
            merged.append(c)
    return merged
