from config import config


class DocumentChunker:
    """文档分块策略：固定大小 + overlap，中文友好。"""

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ):
        self.chunk_size = chunk_size or config.rag.chunk_size
        self.chunk_overlap = chunk_overlap or config.rag.chunk_overlap

    def chunk(self, text: str, title: str = "") -> list[dict]:
        """将文本切分为带元数据的 chunk 列表。

        返回: [{"content": "...", "metadata": {...}}, ...]
        """
        if not text.strip():
            return []

        chunks = []
        start = 0
        idx = 0
        while start < len(text):
            end = start + self.chunk_size
            chunk_text = text[start:end]
            chunks.append({
                "content": chunk_text,
                "metadata": {
                    "title": title,
                    "chunk_index": idx,
                    "start_char": start,
                    "end_char": min(end, len(text)),
                },
            })
            start = start + self.chunk_size - self.chunk_overlap
            idx += 1
        return chunks


chunker = DocumentChunker()
