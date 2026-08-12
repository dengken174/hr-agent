"""混合检索引擎 — 对接 db/vector_store 的真实 FAISS+BM25+RRF。

自动从 MOCK_DOCUMENTS 构建索引，不可用时回退到关键词匹配。
"""

import logging
from typing import Any

from config import config

logger = logging.getLogger(__name__)

# ── 模拟知识库文档 ─────────────────────────────────────────────────────

MOCK_DOCUMENTS: list[dict[str, Any]] = [
    {"title": "五险一金缴纳比例", "content": "养老保险：个人8%，公司16%；医疗保险：个人2%，公司10%；失业保险：个人0.5%，公司0.5%；工伤保险：公司0.5%个人不缴；生育保险：公司0.8%个人不缴；住房公积金：个人12%，公司12%。缴费基数为上年度月平均工资。"},
    {"title": "补充商业保险", "content": "公司为所有正式员工购买补充医疗保险和意外伤害保险。补充医疗报销比例80%，年度限额2万元。涵盖门诊、住院、牙科。"},
    {"title": "餐补与交通补贴", "content": "餐补：每工作日40元，随工资发放。交通补贴：每月300元。加班餐补：加班超2小时额外补贴50元。出差餐补按目的地城市等级分别是80/100/120元每天。"},
    {"title": "住房补贴政策", "content": "P6及以上每月1500元住房补贴。P5及以下每月800元。需提供租房合同。入职满3个月后开始发放。异地调动另有搬迁补贴。"},
    {"title": "年假政策", "content": "入职满1年不满10年：5天年假；满10年不满20年：10天年假；满20年：15天年假。年假可跨年累计最多5天。未休年假按日工资的300%补偿。"},
    {"title": "请假流程", "content": "1. 在飞书/钉钉发起请假申请；2. 选择请假类型和日期；3. 直属上级审批（3天内直接通过）；4. 超过3天需上级的上级加签；5. 审批通过后自动计入考勤系统。病假需上传医院证明。"},
    {"title": "报销流程", "content": "1. 在审批系统提交报销申请；2. 上传发票照片；3. 直属上级审批（1000元以下直接通过）；4. 超1000元需财务审批；5. 审批通过后3个工作日内到账。交通报销需注明出行目的和路线。"},
    {"title": "入职流程指南", "content": "报到时间：周一至周五 9:00；地点：HR 办公室 3F。携带材料：身份证原件、学历学位证复印件、离职证明、银行卡。当天安排：签劳动合同、领取办公设备、开通公司账号、入职培训。"},
    {"title": "公司简介", "content": "公司成立于2015年，总部位于北京，在上海、深圳、成都设有分公司。主营业务为人工智能和企业服务 SaaS，已获 C 轮融资。目前员工超过3000人。"},
    {"title": "公司文化与价值观", "content": "核心价值观：客户第一、创新驱动、团队协作、诚信正直。扁平化管理，鼓励跨部门沟通。每周五下午为 Learning Hour。每月一次全员 Town Hall 会议。"},
    {"title": "面试指南", "content": "面试流程：简历筛选 → 技术笔试/作品集评审 → 技术面（2轮）→ HR面 → 终面。全程约2-3周。面试形式支持线上面试。技术面侧重算法/系统设计/项目经验。"},
    {"title": "培训与发展", "content": "新员工入职培训持续1周。技术培训：内部技术分享每周三下午。外部培训：年度预算5000元每人，可报销培训课程和书籍费用。晋升周期：每年2次晋升窗口（3月和9月）。"},
]


class HybridRetriever:
    """混合检索引擎：FAISS + BM25 → RRF 融合。

    优先使用 db/vector_store 的真实混合检索，
    不可用时回退到简单关键词匹配。
    """

    def __init__(self):
        self._indexed = False
        self._chunks: list[dict] = []
        self._init_mock_index()

    def _init_mock_index(self):
        """用 MOCK_DOCUMENTS 构建分块索引（回退用）。"""
        from mcp_servers.knowledge.chunker import chunker

        for doc in MOCK_DOCUMENTS:
            chunks = chunker.chunk(doc["content"], doc["title"])
            for c in chunks:
                c["metadata"]["title"] = doc["title"]
            self._chunks.extend(chunks)
        logger.info("Mock index: %d chunks from %d documents", len(self._chunks), len(MOCK_DOCUMENTS))

    async def _ensure_real_index(self) -> bool:
        """MCP 子进程跳过真实索引构建（模型加载太慢，子进程资源有限）。

        真实混合检索由主进程 HTTP API (/api/knowledge/search) 提供。
        MCP 工具使用轻量关键词匹配保证响应速度。
        """
        return False

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> list[dict]:
        k = top_k or config.rag.retrieval_k
        n = rerank_top_n or config.rag.rerank_top_n

        # 关键词匹配（轻量，毫秒级响应，适合 MCP 工具调用）
        return self._mock_search(query, min(k, n))

    def _mock_search(self, query: str, top_k: int) -> list[dict]:
        """关键词匹配回退。"""
        keywords = query.lower().split()
        scored = []
        for chunk in self._chunks:
            content_lower = chunk["content"].lower()
            score = sum(1 for kw in keywords if kw in content_lower)
            if score > 0:
                scored.append({**chunk, "score": score / len(keywords)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]


hybrid_retriever = HybridRetriever()
