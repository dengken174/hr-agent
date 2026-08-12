"""混合检索 — 双路并行 + RRF 融合 + 可选 Rerank。

架构（业内主流三段式）:
  Path A: 向量检索 (FAISS IndexFlatIP) — 语义相似度
  Path B: 关键词检索 (BM25)            — 精确关键词匹配
        ↓ 双路并行，各取 retrieval_k 条
  Fusion: RRF (Reciprocal Rank Fusion) — 排名融合去重
        ↓
  Rerank: Cross-encoder / LLM 重排序    — 精排 (可选)
        ↓ 取 rerank_top_n 条
  最终结果

参考 config.rag: retrieval_k=20, rrf_k=60, rerank_top_n=5
"""

import json
import logging
import math
import os
import threading
from collections import defaultdict
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_INDEX_DIR = Path(os.getenv("FAISS_INDEX_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "faiss")))
_INDEX_FILE = _INDEX_DIR / "knowledge.index"
_META_FILE = _INDEX_DIR / "knowledge_meta.json"

_index: "faiss.IndexFlatIP | None" = None  # type: ignore
_metadata: list[dict] = []  # [{id, title, content, category}, ...]
_lock = threading.Lock()

# BM25 state
_bm25_corpus: list[list[str]] = []           # tokenized texts
_bm25_doc_ids: list[str] = []
_bm25_avgdl: float = 0.0
_bm25_df: dict[str, int] = {}                # document frequency
_bm25_N: int = 0
_bm25_cache: BM25 | None = None              # cached instance, rebuilt only when corpus changes

# from config
from config import config


def _ensure_dir():
    _INDEX_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════
# 中文分词 (jieba)
# ═══════════════════════════════════════════════════════════════════════

def _tokenize(text: str) -> list[str]:
    """jieba 搜索引擎模式分词，含 HR 领域自定义词典。"""
    import jieba

    # 延迟加载自定义词典，避免重复添加
    if not hasattr(_tokenize, "_dict_loaded"):
        hr_words = [
            "入职", "离职", "转正", "调岗", "晋升", "年假", "事假", "病假",
            "五险一金", "社保", "公积金", "住房补贴", "差旅报销", "培训",
            "面试", "简历", "offer", "薪资", "绩效", "年终奖", "期权",
            "劳动合同", "竞业协议", "保密协议", "实习", "试用期", "背调",
            "报到", "入职流程", "入职培训", "HR", "人力资源", "薪酬",
            "福利", "补贴", "审批", "考勤", "打卡", "加班",
        ]
        for w in hr_words:
            jieba.add_word(w)
        _tokenize._dict_loaded = True  # type: ignore[attr-defined]

    return list(jieba.cut_for_search(text))


# ═══════════════════════════════════════════════════════════════════════
# Path B: BM25 关键词检索
# ═══════════════════════════════════════════════════════════════════════

class BM25:
    """BM25 实现。k1=1.5, b=0.75 (标准参数)。"""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.N = len(corpus)
        self.avgdl = sum(len(doc) for doc in corpus) / max(self.N, 1)
        self.df = {}  # document frequency
        self._build_df()

    def _build_df(self):
        for doc in self.corpus:
            seen = set()
            for token in doc:
                if token not in seen:
                    self.df[token] = self.df.get(token, 0) + 1
                    seen.add(token)

    def _idf(self, token: str) -> float:
        n = self.df.get(token, 0)
        if n == 0:
            return 0.0
        return math.log((self.N - n + 0.5) / (n + 0.5) + 1.0)

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        doc = self.corpus[doc_idx]
        doc_len = len(doc)
        score = 0.0
        tf_map = defaultdict(int)
        for t in doc:
            tf_map[t] += 1

        for token in set(query_tokens):
            tf = tf_map.get(token, 0)
            if tf == 0:
                continue
            idf = self._idf(token)
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / max(self.avgdl, 1))
            score += idf * numerator / denominator
        return score

    def search(self, query_tokens: list[str], top_k: int = 20) -> list[tuple[int, float]]:
        scores = [(i, self.score(query_tokens, i)) for i in range(self.N)]
        scores.sort(key=lambda x: x[1], reverse=True)
        return [(idx, s) for idx, s in scores[:top_k] if s > 0]


async def build_index(documents: list[dict]) -> int:
    """全量重建双路索引 (FAISS + BM25)。"""
    global _index, _metadata, _bm25_corpus, _bm25_doc_ids, _bm25_avgdl, _bm25_df, _bm25_N

    if not documents:
        logger.warning("No documents to index")
        return 0

    # ── FAISS vector index ──────────────────────────────────────────
    from db.embedding import embed_texts

    texts = [f"{d.get('title', '')}\n{d.get('content', '')}" for d in documents]
    logger.info("Building hybrid index for %d documents...", len(texts))
    embeddings = await embed_texts(texts)
    dim = len(embeddings[0]) if embeddings else 0
    if dim == 0:
        return 0

    import faiss
    arr = np.array(embeddings, dtype=np.float32)
    idx = faiss.IndexFlatIP(dim)
    idx.add(arr)

    # ── BM25 keyword index ──────────────────────────────────────────
    bm25_corpus = []
    bm25_ids = []
    for d in documents:
        tokens = _tokenize(f"{d.get('title', '')} {d.get('content', '')}")
        bm25_corpus.append(tokens)
        bm25_ids.append(d.get("id", ""))

    bm25 = BM25(bm25_corpus)

    with _lock:
        _index = idx
        _metadata = [{"id": d.get("id", ""), "title": d.get("title", ""),
                       "content": d.get("content", ""), "category": d.get("category", "")}
                      for d in documents]
        _bm25_corpus = bm25_corpus
        _bm25_doc_ids = bm25_ids
        _bm25_N = bm25.N
        _bm25_df = bm25.df
        _bm25_avgdl = bm25.avgdl
        _rebuild_bm25_cache()

        # 同步到 Elasticsearch (如果可用)
        try:
            from db.es_engine import index_documents
            es_count = await index_documents(documents)
            if es_count:
                logger.info("ES index synced: %d documents", es_count)
        except Exception:
            pass

    # Persist
    _ensure_dir()
    try:
        faiss.write_index(idx, str(_INDEX_FILE))
        index_meta = {
            "metadata": _metadata,
            "bm25_doc_ids": _bm25_doc_ids,
            "bm25_N": _bm25_N,
            "bm25_avgdl": _bm25_avgdl,
            "bm25_df": _bm25_df,
        }
        _META_FILE.write_text(json.dumps(index_meta, ensure_ascii=False), encoding="utf-8")
        logger.info("Hybrid index saved: %d vectors → %s", len(documents), _INDEX_FILE)
    except Exception:
        logger.warning("Failed to persist index")

    return len(documents)


# ═══════════════════════════════════════════════════════════════════════
# 混合检索主入口
# ═══════════════════════════════════════════════════════════════════════

async def hybrid_search(query: str, top_k: int = 5,
                        retrieval_k: int | None = None,
                        rrf_k: int | None = None,
                        use_rerank: bool = True) -> list[dict]:
    """双路并行检索 + RRF 融合 + 可选 Rerank。

    三段式流水线:
      1. 双路并行检索 (FAISS + BM25)，各取 retrieval_k 条
      2. RRF 排名融合，取 rerank_pool 条作为候选池
      3. Reranker 精排 → 返回 top_k

    Args:
        query: 用户查询
        top_k: 最终返回条数
        retrieval_k: 每路检索条数 (默认 config.rag.retrieval_k=20)
        rrf_k: RRF 平滑参数 (默认 config.rag.rrf_k=60)
        use_rerank: 是否启用 Rerank 精排 (默认 True)
    """
    global _index, _metadata, _bm25_corpus, _bm25_doc_ids, _bm25_N, _bm25_df, _bm25_avgdl, _bm25_cache

    r_k = retrieval_k or config.rag.retrieval_k
    rrf_const = rrf_k or config.rag.rrf_k
    rerank_pool = config.rag.rerank_top_n * 3  # RRF 阶段多取 3x 候选送 Reranker

    # Load from disk if needed
    if _index is None:
        _load_from_disk()
    if _index is None:
        return []

    query_tokens = _tokenize(query)

    # ── Path A: 向量检索 ────────────────────────────────────────────
    from db.embedding import embed_query
    q_vec = np.array([await embed_query(query)], dtype=np.float32)
    vec_k = min(r_k, _index.ntotal)

    with _lock:
        scores_vec, indices_vec = _index.search(q_vec, vec_k)

    # ── Path B: 关键词检索 (ES + IK → 回退 BM25) ──────────────────
    keyword_results = await _keyword_search_es(query, r_k)
    if not keyword_results and _bm25_cache is not None:
        # ES 不可用，回退到内存 BM25
        bm25_results = _bm25_cache.search(query_tokens, top_k=r_k)
        # Convert BM25 results to same format as ES
        keyword_results = []
        for idx, score in bm25_results:
            doc_id = _bm25_doc_ids[idx] if idx < len(_bm25_doc_ids) else ""
            keyword_results.append({"doc_id": doc_id, "score": score})

    # ── RRF 融合 ────────────────────────────────────────────────────
    # RRF(doc) = Σ 1/(rrf_k + rank_in_path)
    rrf_scores: dict[int, float] = {}  # idx → RRF score

    for rank, idx in enumerate(indices_vec[0]):
        if idx >= 0 and idx < len(_metadata):
            rrf_scores[idx] = rrf_scores.get(idx, 0.0) + 1.0 / (rrf_const + rank + 1)

    for rank, kw in enumerate(keyword_results):
        meta_idx = _doc_id_to_meta_idx(kw["doc_id"])
        if meta_idx >= 0:
            rrf_scores[meta_idx] = rrf_scores.get(meta_idx, 0.0) + 1.0 / (rrf_const + rank + 1)

    # Sort by RRF score desc, pool for reranker
    pool_size = min(rerank_pool, len(rrf_scores))
    pool_indices = sorted(rrf_scores.keys(), key=lambda i: rrf_scores[i], reverse=True)[:pool_size]

    candidates = []
    for idx in pool_indices:
        meta = _metadata[idx]
        candidates.append({
            "doc_id": meta["id"],
            "title": meta["title"],
            "content": meta["content"][:300],
            "relevance": round(rrf_scores[idx], 4),
            "category": meta.get("category", ""),
        })

    # ── Phase 3: Rerank ─────────────────────────────────────────────
    if use_rerank and len(candidates) > top_k:
        try:
            from db.reranker import get_reranker
            reranker = get_reranker()
            candidates = await reranker.rerank(query, candidates, top_k)
        except Exception:
            logger.debug("Rerank skipped, using RRF order")

    return candidates[:top_k]


async def vector_search(query: str, top_k: int = 5) -> list[dict]:
    """单路向量检索 (留作对比/回退)。"""
    global _index, _metadata
    if _index is None:
        _load_from_disk()
    if _index is None:
        return []
    from db.embedding import embed_query
    q_vec = np.array([await embed_query(query)], dtype=np.float32)
    k = min(top_k, _index.ntotal)
    with _lock:
        scores, indices = _index.search(q_vec, k)
    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= len(_metadata):
            continue
        meta = _metadata[idx]
        results.append({
            "doc_id": meta["id"], "title": meta["title"],
            "content": meta["content"][:300],
            "relevance": round(float((score + 1) / 2), 3),
            "category": meta.get("category", ""),
        })
    return results


async def _keyword_search_es(query: str, top_k: int) -> list[dict]:
    """Path B: ES + IK 关键词检索，不可用时返回空列表。"""
    try:
        from db.es_engine import keyword_search
        return await keyword_search(query, top_k)
    except Exception:
        return []

# Alias: search() = hybrid_search()
search = hybrid_search


# ═══════════════════════════════════════════════════════════════════════
# 索引管理
# ═══════════════════════════════════════════════════════════════════════

def _rebuild_bm25_cache():
    """从全局状态重建 BM25 缓存实例。仅在语料变化时调用。"""
    global _bm25_cache
    bm25 = BM25.__new__(BM25)
    bm25.corpus = _bm25_corpus
    bm25.N = _bm25_N
    bm25.avgdl = _bm25_avgdl
    bm25.df = dict(_bm25_df)
    bm25.k1 = 1.5
    bm25.b = 0.75
    _bm25_cache = bm25


def _invalidate_bm25_cache():
    """语料变化后标记 BM25 缓存失效。"""
    global _bm25_cache
    _bm25_cache = None


def _doc_id_to_meta_idx(doc_id: str) -> int:
    for i, m in enumerate(_metadata):
        if m.get("id") == doc_id:
            return i
    return -1


def _load_from_disk():
    global _index, _metadata, _bm25_corpus, _bm25_doc_ids, _bm25_N, _bm25_df, _bm25_avgdl, _bm25_cache
    if not _INDEX_FILE.exists() or not _META_FILE.exists():
        return
    try:
        import faiss
        saved = json.loads(_META_FILE.read_text(encoding="utf-8"))
        with _lock:
            _index = faiss.read_index(str(_INDEX_FILE))
            _metadata = saved.get("metadata", [])
            _bm25_doc_ids = saved.get("bm25_doc_ids", [])
            _bm25_N = saved.get("bm25_N", 0)
            _bm25_avgdl = saved.get("bm25_avgdl", 0.0)
            _bm25_df = saved.get("bm25_df", {})
            # Rebuild BM25 corpus from metadata
            _bm25_corpus = [_tokenize(f"{m.get('title','')} {m.get('content','')}") for m in _metadata]
            _rebuild_bm25_cache()
        logger.info("Hybrid index loaded: %d vectors", _index.ntotal)
    except Exception:
        logger.warning("Failed to load index from disk")


async def add_document(doc: dict) -> bool:
    """增量添加文档到双路索引。"""
    global _index, _metadata, _bm25_corpus, _bm25_doc_ids
    if _index is None:
        return False
    from db.embedding import embed_texts
    text = f"{doc.get('title', '')}\n{doc.get('content', '')}"
    vec = np.array(await embed_texts([text]), dtype=np.float32)
    tokens = _tokenize(f"{doc.get('title', '')} {doc.get('content', '')}")
    with _lock:
        _index.add(vec)
        _metadata.append({"id": doc.get("id", ""), "title": doc.get("title", ""),
                           "content": doc.get("content", ""), "category": doc.get("category", "")})
        _bm25_corpus.append(tokens)
        _bm25_doc_ids.append(doc.get("id", ""))
        _bm25_N = len(_bm25_corpus)
        _invalidate_bm25_cache()
        # Sync to ES
        try:
            from db.es_engine import index_single
            await index_single(doc)
        except Exception:
            pass
    return True


async def remove_document(doc_id: str) -> bool:
    """删除文档 — FAISS IndexFlatIP 不支持删除，设置脏标记，下次搜索前触发全量重建。

    调用方删除文档后应调用 build_index() 全量重建索引。
    """
    global _metadata
    with _lock:
        for i, m in enumerate(_metadata):
            if m.get("id") == doc_id:
                _metadata.pop(i)
                # Invalidate FAISS index and sync to ES
                try:
                    from db.es_engine import delete_document
                    await delete_document(doc_id)
                except Exception:
                    pass
                return True
    return False


async def index_stats() -> dict:
    return {
        "total": _index.ntotal if _index else 0,
        "dim": _index.d if _index else 0,
        "indexed_docs": len(_metadata),
        "bm25_docs": len(_bm25_doc_ids),
    }
