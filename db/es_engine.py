"""
Elasticsearch + IK 分词器 — 关键词检索引擎。

业内标准中文检索方案:
  索引时: ik_max_word (最大粒度切分 → 高召回)
  搜索时: ik_smart     (智能切分 → 高精度)

ES 不可用时自动回退到内存 BM25。
"""

import logging
from typing import Any

from elasticsearch import AsyncElasticsearch, NotFoundError

from config import config

logger = logging.getLogger(__name__)

INDEX_NAME = config.es.index_name  # "hr_knowledge"

# IK 索引配置
INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "analyzer": {
                "ik_index": {"type": "custom", "tokenizer": "ik_max_word"},
                "ik_search": {"type": "custom", "tokenizer": "ik_smart"},
            }
        },
    },
    "mappings": {
        "properties": {
            "doc_id": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "ik_index", "search_analyzer": "ik_search"},
            "content": {"type": "text", "analyzer": "ik_index", "search_analyzer": "ik_search"},
            "category": {"type": "keyword"},
        }
    },
}

_es: AsyncElasticsearch | None = None
_available: bool | None = None  # None=未检测, True=可用, False=不可用
_index_ready: bool = False


async def get_client() -> AsyncElasticsearch | None:
    """获取 ES 客户端，不可用时返回 None。"""
    global _es, _available
    if _available is False:
        return None
    if _es is not None:
        return _es
    try:
        _es = AsyncElasticsearch(
            hosts=[config.es.url],
            request_timeout=10,
            max_retries=1,
            retry_on_timeout=False,
        )
        if not await _es.ping():
            logger.warning("ES ping failed at %s, using in-memory BM25 fallback", config.es.url)
            await _es.close()
            _es = None
            _available = False
            return None
        _available = True
        logger.info("Elasticsearch connected: %s", config.es.url)
        return _es
    except Exception as e:
        logger.warning("ES unavailable (%s), using in-memory BM25 fallback", e)
        _available = False
        if _es:
            await _es.close()
            _es = None
        return None


async def ensure_index() -> bool:
    """确保索引存在（含 IK 分词器配置）。"""
    global _index_ready
    if _index_ready:
        return True
    es = await get_client()
    if not es:
        return False
    try:
        exists = await es.indices.exists(index=INDEX_NAME)
        if not exists:
            await es.indices.create(index=INDEX_NAME, body=INDEX_BODY)
            logger.info("ES index '%s' created with IK analyzer", INDEX_NAME)
        else:
            logger.info("ES index '%s' already exists", INDEX_NAME)
        _index_ready = True
        return True
    except Exception as e:
        logger.warning("Failed to ensure ES index: %s", e)
        return False


async def index_documents(docs: list[dict]) -> int:
    """批量索引文档到 ES。docs: [{id, title, content, category}, ...]"""
    es = await get_client()
    if not es or not await ensure_index():
        return 0
    try:
        from elasticsearch.helpers import async_bulk

        actions = []
        for d in docs:
            actions.append({
                "_index": INDEX_NAME,
                "_id": d.get("id", ""),
                "_source": {
                    "doc_id": d.get("id", ""),
                    "title": d.get("title", ""),
                    "content": d.get("content", ""),
                    "category": d.get("category", ""),
                },
            })
        success, errors = await async_bulk(es, actions, refresh=True)
        if errors:
            logger.warning("ES bulk index: %d success, %d errors", success, len(errors))
        else:
            logger.info("ES indexed %d documents", success)
        return success
    except Exception as e:
        logger.warning("ES bulk index failed: %s", e)
        return 0


async def index_single(doc: dict) -> bool:
    """索引单个文档。"""
    es = await get_client()
    if not es or not await ensure_index():
        return False
    try:
        await es.index(
            index=INDEX_NAME,
            id=doc.get("id", ""),
            body={
                "doc_id": doc.get("id", ""),
                "title": doc.get("title", ""),
                "content": doc.get("content", ""),
                "category": doc.get("category", ""),
            },
            refresh=True,
        )
        return True
    except Exception as e:
        logger.warning("ES single index failed: %s", e)
        return False


async def delete_document(doc_id: str) -> bool:
    """从 ES 删除文档。"""
    es = await get_client()
    if not es:
        return False
    try:
        await es.delete(index=INDEX_NAME, id=doc_id, refresh=True)
        return True
    except NotFoundError:
        return True  # already deleted
    except Exception as e:
        logger.warning("ES delete failed: %s", e)
        return False


async def keyword_search(query: str, top_k: int = 20) -> list[dict]:
    """ES 关键词检索 (BM25 评分 + IK 分词)。

    Returns: [{doc_id, title, content, score, category}, ...]
    """
    es = await get_client()
    if not es or not await ensure_index():
        return []

    try:
        resp = await es.search(
            index=INDEX_NAME,
            body={
                "query": {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^2", "content"],  # title 权重 2x
                        "type": "best_fields",
                    }
                },
                "size": top_k,
                "_source": ["doc_id", "title", "content", "category"],
            },
        )
    except Exception as e:
        logger.warning("ES search failed: %s", e)
        return []

    results = []
    max_score = resp["hits"]["max_score"] or 1.0
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        results.append({
            "doc_id": src.get("doc_id", hit["_id"]),
            "title": src.get("title", ""),
            "content": src.get("content", "")[:300],
            "score": round(hit["_score"] / max_score, 4) if max_score else 0,
            "category": src.get("category", ""),
        })
    return results


async def es_stats() -> dict:
    """ES 索引统计。"""
    es = await get_client()
    if not es:
        return {"available": False}
    try:
        count = await es.count(index=INDEX_NAME)
        return {"available": True, "index": INDEX_NAME, "doc_count": count["count"]}
    except Exception:
        return {"available": True, "index": INDEX_NAME, "doc_count": 0}


async def close():
    """关闭 ES 连接。"""
    global _es, _available, _index_ready
    if _es:
        await _es.close()
        _es = None
    _available = None
    _index_ready = False
