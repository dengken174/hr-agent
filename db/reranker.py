"""Reranker — 精排模块。

对 RRF 融合后的候选集进行二次排序，提升 top_k 精度。

支持三种策略 (按优先级):
  1. LLM Listwise  — 用 DeepSeek 一次性排序 (当前实现)
  2. Cross-encoder — BGE-Reranker / Jina Reranker (需下载模型或 API key)
  3. Noop          — 透传，不做重排序

策略选择:
  - 有 JINA_API_KEY  → Jina Reranker API (config.jina)
  - 有 DEEPSEEK_API_KEY → LLM Reranker
  - 都没有 → Noop (保留 RRF 原始排序)
"""

import json
import logging
import os
from typing import Any

from config import config

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Reranker 接口
# ═══════════════════════════════════════════════════════════════════════

class BaseReranker:
    async def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        raise NotImplementedError


class NoopReranker(BaseReranker):
    """透传 — 不做重排序。"""
    async def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        return candidates[:top_k]


# ═══════════════════════════════════════════════════════════════════════
# LLM Listwise Reranker
# ═══════════════════════════════════════════════════════════════════════

_RERANK_PROMPT = """你是一个文档相关性评估专家。请根据用户查询，对以下候选文档按相关性从高到低排序。

用户查询: {query}

候选文档:
{documents}

请返回排序后的文档 ID 列表(JSON 数组格式)，只保留与查询相关的文档，不相关的直接丢弃。
例如: ["K003", "K001", "K005"]

只返回 JSON 数组，不要其他内容。"""


class LLMReranker(BaseReranker):
    """DeepSeek 单次 Listwise 排序。

    把 top-N 候选 + query 一次性发给 LLM，让 LLM 直接输出排序后的 doc_id 列表。
    优点: 不依赖外部模型，支持中文，LLM 天然理解语义。
    限制: 候选数受 context window 限制，建议 N ≤ 15。
    """

    def __init__(self, api_key: str = "", base_url: str = "", model: str = ""):
        self._api_key = api_key or config.llm.api_key
        self._base_url = base_url or config.llm.base_url
        self._model = model or config.llm.chat_model

    async def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        if not candidates:
            return []
        if len(candidates) == 1:
            return candidates[:top_k]

        # 构建候选文档文本
        doc_lines = []
        for i, c in enumerate(candidates):
            doc_lines.append(f"[{c['doc_id']}] {c['title']}: {c.get('content', '')[:300]}")
        doc_text = "\n".join(doc_lines)

        prompt = _RERANK_PROMPT.format(query=query, documents=doc_text)

        try:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                        "max_tokens": 256,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                raw = data["choices"][0]["message"]["content"].strip()
        except Exception:
            logger.exception("LLM rerank failed, falling back to original order")
            return candidates[:top_k]

        # 解析 LLM 返回的排序列表
        try:
            # 去掉可能的 markdown 代码块标记
            if raw.startswith("```"):
                lines = raw.split("\n")
                raw = "\n".join(lines[1:]) if len(lines) > 1 else raw
                if raw.endswith("```"):
                    raw = raw[:-3]
            ranked_ids = json.loads(raw.strip())
        except json.JSONDecodeError:
            logger.warning("LLM rerank returned invalid JSON: %s", raw[:100])
            return candidates[:top_k]

        # 按 LLM 排序重新组织结果
        id_to_doc = {c["doc_id"]: c for c in candidates}
        reranked = []
        for doc_id in ranked_ids:
            if doc_id in id_to_doc:
                reranked.append(id_to_doc[doc_id])

        # 补充 LLM 可能漏掉的文档（排在最后）
        for c in candidates:
            if c["doc_id"] not in ranked_ids:
                reranked.append(c)

        logger.debug("LLM rerank: %d → %d results", len(candidates), len(reranked[:top_k]))
        return reranked[:top_k]


# ═══════════════════════════════════════════════════════════════════════
# 工厂方法
# ═══════════════════════════════════════════════════════════════════════

_reranker: BaseReranker | None = None


def get_reranker() -> BaseReranker:
    """根据可用配置自动选择合适的 Reranker。"""
    global _reranker
    if _reranker is not None:
        return _reranker

    # 策略 1: Jina Reranker API (如果配置了 key)
    if config.jina.api_key:
        logger.info("Using Jina Reranker: %s", config.jina.reranker_model)
        _reranker = JinaReranker()
        return _reranker

    # 策略 2: DeepSeek LLM Reranker
    if config.llm.api_key:
        logger.info("Using LLM Reranker: %s", config.llm.chat_model)
        _reranker = LLMReranker()
        return _reranker

    # 策略 3: 无 reranker，透传
    logger.info("No reranker configured, using NoopReranker")
    _reranker = NoopReranker()
    return _reranker


class JinaReranker(BaseReranker):
    """Jina Reranker API — Cross-encoder 精排。

    通过 Jina 的 rerank API 对 (query, doc) 对打分。
    """

    def __init__(self):
        self._api_key = config.jina.api_key
        self._model = config.jina.reranker_model

    async def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        if not candidates or len(candidates) == 1:
            return candidates[:top_k]

        documents = [c.get("content", "")[:500] for c in candidates]

        try:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    "https://api.jina.ai/v1/rerank",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "query": query,
                        "documents": documents,
                        "top_n": top_k,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.exception("Jina rerank failed")
            return candidates[:top_k]

        # Jina 返回 {results: [{index, relevance_score}, ...]}
        ranked = []
        seen = set()
        for item in data.get("results", []):
            idx = item.get("index", -1)
            if 0 <= idx < len(candidates) and idx not in seen:
                c = candidates[idx].copy()
                c["relevance"] = round(item.get("relevance_score", c.get("relevance", 0)), 4)
                ranked.append(c)
                seen.add(idx)

        # 补充未返回的候选
        for i, c in enumerate(candidates):
            if i not in seen:
                ranked.append(c)

        logger.debug("Jina rerank: %d → %d results", len(candidates), len(ranked[:top_k]))
        return ranked[:top_k]
