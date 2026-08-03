import logging
from typing import Any

from config import config

logger = logging.getLogger(__name__)

# ── 模拟知识库文档 ─────────────────────────────────────────────────────

MOCK_DOCUMENTS: list[dict[str, Any]] = [
    # 福利政策
    {"title": "五险一金缴纳比例", "content": "养老保险：个人8%，公司16%；医疗保险：个人2%，公司10%；失业保险：个人0.5%，公司0.5%；工伤保险：公司0.5%个人不缴；生育保险：公司0.8%个人不缴；住房公积金：个人12%，公司12%。缴费基数为上年度月平均工资。"},
    {"title": "补充商业保险", "content": "公司为所有正式员工购买补充医疗保险和意外伤害保险。补充医疗报销比例80%，年度限额2万元。涵盖门诊、住院、牙科。"},
    {"title": "餐补与交通补贴", "content": "餐补：每工作日40元，随工资发放。交通补贴：每月300元。加班餐补：加班超2小时额外补贴50元。出差餐补按目的地城市等级分别是80/100/120元每天。"},
    {"title": "住房补贴政策", "content": "P6及以上每月1500元住房补贴。P5及以下每月800元。需提供租房合同。入职满3个月后开始发放。异地调动另有搬迁补贴。"},
    {"title": "年假政策", "content": "入职满1年不满10年：5天年假；满10年不满20年：10天年假；满20年：15天年假。年假可跨年累计最多5天。未休年假按日工资的300%补偿。"},

    # 流程指南
    {"title": "请假流程", "content": "1. 在飞书/钉钉发起请假申请；2. 选择请假类型和日期；3. 直属上级审批（3天内直接通过）；4. 超过3天需上级的上级加签；5. 审批通过后自动计入考勤系统。病假需上传医院证明。"},
    {"title": "报销流程", "content": "1. 在审批系统提交报销申请；2. 上传发票照片；3. 直属上级审批（1000元以下直接通过）；4. 超1000元需财务审批；5. 审批通过后3个工作日内到账。交通报销需注明出行目的和路线。"},
    {"title": "入职流程指南", "content": "报到时间：周一至周五 9:00；地点：HR 办公室 3F。携带材料：身份证原件、学历学位证复印件、离职证明、银行卡。当天安排：签劳动合同、领取办公设备、开通公司账号、入职培训。"},

    # 公司介绍
    {"title": "公司简介", "content": "公司成立于2015年，总部位于北京，在上海、深圳、成都设有分公司。主营业务为人工智能和企业服务 SaaS，已获 C 轮融资。目前员工超过3000人。"},
    {"title": "公司文化与价值观", "content": "核心价值观：客户第一、创新驱动、团队协作、诚信正直。扁平化管理，鼓励跨部门沟通。每周五下午为 Learning Hour。每月一次全员 Town Hall 会议。"},
    {"title": "面试指南", "content": "面试流程：简历筛选 → 技术笔试/作品集评审 → 技术面（2轮）→ HR面 → 终面。全程约2-3周。面试形式支持线上面试。技术面侧重算法/系统设计/项目经验。"},
    {"title": "培训与发展", "content": "新员工入职培训持续1周。技术培训：内部技术分享每周三下午。外部培训：年度预算5000元每人，可报销培训课程和书籍费用。晋升周期：每年2次晋升窗口（3月和9月）。"},
]


class HybridRetriever:
    """混合检索引擎：BM25(ES) + 向量(Milvus) → RRF 融合 → Jina Reranker 重排序。

    当前为 mock 实现，基于关键词匹配。生产环境对接 ES + Milvus + Jina API。
    """

    def __init__(self):
        self._chunks: list[dict] = []
        self._index_documents()

    def _index_documents(self):
        """将模拟文档分块建索引。"""
        from mcp_servers.knowledge.chunker import chunker

        for doc in MOCK_DOCUMENTS:
            chunks = chunker.chunk(doc["content"], doc["title"])
            for c in chunks:
                c["metadata"]["title"] = doc["title"]
            self._chunks.extend(chunks)
        logger.info("Indexed %d chunks from %d documents", len(self._chunks), len(MOCK_DOCUMENTS))

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> list[dict]:
        """混合检索入口。

        Args:
            query: 搜索查询
            top_k: BM25 + 向量各自召回数
            rerank_top_n: 最终返回数

        Returns:
            [{"content": "...", "score": 0.95, "metadata": {...}}, ...]
        """
        k = top_k or config.rag.retrieval_k
        n = rerank_top_n or config.rag.rerank_top_n

        # 阶段 1: BM25 关键词检索（mock: 简单关键词匹配打分）
        bm25_results = self._bm25_search(query, k)

        # 阶段 2: 向量语义检索（mock: 复用关键词匹配，生产用 Milvus）
        vector_results = self._vector_search(query, k)

        # 阶段 3: RRF 融合去重
        fused = self._rrf_fuse(bm25_results, vector_results, k=config.rag.rrf_k)

        # 阶段 4: Jina Reranker 重排序（mock: 按 RRF 分数排序）
        ranked = self._rerank(query, fused, n)

        return ranked

    def _bm25_search(self, query: str, k: int) -> list[dict]:
        """BM25 关键词检索（mock 实现）。"""
        keywords = query.lower().split()
        scored = []
        for chunk in self._chunks:
            content_lower = chunk["content"].lower()
            score = sum(1 for kw in keywords if kw in content_lower)
            if score > 0:
                scored.append({**chunk, "score": score / len(keywords)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:k]

    def _vector_search(self, query: str, k: int) -> list[dict]:
        """向量语义检索（mock 实现，生产环境用 Milvus embedding）。"""
        return self._bm25_search(query, k)

    def _rrf_fuse(self, results_a: list[dict], results_b: list[dict], k: int = 60) -> list[dict]:
        """RRF (Reciprocal Rank Fusion) 融合去重。"""
        scores: dict[int, float] = {}
        id_to_chunk: dict[int, dict] = {}

        for rank, item in enumerate(results_a):
            idx = id(item["content"])
            id_to_chunk[idx] = item
            scores[idx] = scores.get(idx, 0) + 1.0 / (k + rank + 1)

        for rank, item in enumerate(results_b):
            idx = id(item["content"])
            id_to_chunk[idx] = item
            scores[idx] = scores.get(idx, 0) + 1.0 / (k + rank + 1)

        sorted_ids = sorted(scores, key=scores.get, reverse=True)
        return [{**id_to_chunk[idx], "rrf_score": scores[idx]} for idx in sorted_ids]

    def _rerank(self, query: str, candidates: list[dict], top_n: int) -> list[dict]:
        """Jina Reranker 重排序（mock 实现，生产环境调 Jina API）。"""
        _ = query
        # Mock: 直接按 RRF 分数排序
        candidates.sort(key=lambda x: x.get("rrf_score", x.get("score", 0)), reverse=True)
        return candidates[:top_n]


hybrid_retriever = HybridRetriever()
