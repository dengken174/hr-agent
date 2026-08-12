"""文本向量化 — 本地私有化部署，数据不出域。

默认 BAAI/bge-m3 (1024d, 中英多语), 通过 HF 镜像下载。
备选: all-MiniLM-L6-v2 (384d), bge-large-zh-v1.5 (1024d)

环境变量:
  HF_ENDPOINT         HuggingFace 镜像地址 (默认 https://hf-mirror.com)
  EMBEDDING_MODEL     模型名称 (默认 BAAI/bge-m3)
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── 国内网络: 自动设置 HF 镜像 ─────────────────────────────────────
if "HF_ENDPOINT" not in os.environ:
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    logger.info("HF_ENDPOINT set to https://hf-mirror.com (国内镜像)")

_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
_embedder: Any = None
_available: bool | None = None  # None=未检测, True=可用, False=不可用

# BGE 系列模型建议对 query 加前缀以提升检索质量
_BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


def is_available() -> bool:
    global _available
    if _available is not None:
        return _available
    try:
        get_embedder()
        _available = True
    except Exception as e:
        logger.warning("Embedding model unavailable: %s", e)
        logger.warning("Vector search will be unavailable. To fix:")
        logger.warning("  1. Set HF_ENDPOINT to a reachable mirror")
        logger.warning("  2. Or set EMBEDDING_MODEL to a local model path")
        _available = False
    return _available


def get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        mirror = os.environ.get("HF_ENDPOINT", "default")
        logger.info("Loading embedding model: %s (HF mirror: %s)", _MODEL_NAME, mirror)
        _embedder = SentenceTransformer(_MODEL_NAME)
        dim = _embedder.get_sentence_embedding_dimension()
        logger.info("Embedding model loaded, dimension=%d", dim)
    return _embedder


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量文本 → 向量。文本原样编码（不加 query prefix），用于文档入库。"""
    if not texts or not is_available():
        return []
    import asyncio
    model = get_embedder()
    loop = asyncio.get_running_loop()
    embeddings = await loop.run_in_executor(
        None,
        lambda: model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist(),
    )
    return embeddings


async def embed_query(query: str) -> list[float]:
    """查询文本 → 向量。BGE 系列自动加 query prefix 提升检索质量。"""
    results = await _embed_queries([query])
    if results:
        return results[0]
    raise RuntimeError("Embedding model not available")


async def _embed_queries(queries: list[str]) -> list[list[float]]:
    """多条查询 → 向量（加 BGE query prefix）。"""
    if not queries:
        return []
    if not is_available():
        return []
    model = get_embedder()
    # BGE/M3 系列: query 加前缀提升检索效果, 文档不加
    prefixed = [_BGE_QUERY_PREFIX + q for q in queries]
    import asyncio
    loop = asyncio.get_running_loop()
    embeddings = await loop.run_in_executor(
        None,
        lambda: model.encode(
            prefixed,
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist(),
    )
    return embeddings


def embedding_dim() -> int:
    if not is_available():
        return 0
    model = get_embedder()
    return model.get_sentence_embedding_dimension()
