"""知识库管理端点。"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from backend.middleware import get_current_user
from backend.models import KnowledgeDoc, KnowledgeSearchRequest, KnowledgeSearchResult

logger = logging.getLogger("backend.routes.knowledge")
router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

# ── Mock 知识库数据 ──────────────────────────────────────────────────

MOCK_DOCS: list[dict] = [
    {
        "id": "K001",
        "title": "入职流程指南",
        "content": "报到时间：周一至周五 9:00；地点：HR 办公室 3F。携带材料：身份证原件、学历学位证复印件、离职证明、银行卡。当天安排：签劳动合同、领取办公设备、开通公司账号、入职培训。",
        "category": "guide",
        "tags": ["入职", "流程", "报到"],
        "updated_at": "2026-07-01T09:00:00Z",
    },
    {
        "id": "K002",
        "title": "年假政策",
        "content": "入职满1年不满10年：5天年假；满10年不满20年：10天年假；满20年：15天年假。年假可跨年累计最多5天。未休年假按日工资的300%补偿。",
        "category": "policy",
        "tags": ["年假", "休假", "福利"],
        "updated_at": "2026-06-15T14:00:00Z",
    },
    {
        "id": "K003",
        "title": "五险一金缴纳标准",
        "content": "养老保险：公司16% 个人8%；医疗保险：公司8.5% 个人2%；失业保险：公司0.5% 个人0.5%；工伤保险：公司0.2%-1.9% 个人0%；生育保险：公司0.5% 个人0%；住房公积金：公司5%-12% 个人5%-12%。缴纳基数：上一年度月平均工资，每年7月调整。",
        "category": "policy",
        "tags": ["五险一金", "社保", "公积金", "福利"],
        "updated_at": "2026-07-10T11:00:00Z",
    },
    {
        "id": "K004",
        "title": "住房补贴政策",
        "content": "P6及以上每月1500元住房补贴。P5及以下每月800元。需提供租房合同。入职满3个月后开始发放。异地调动另有搬迁补贴。",
        "category": "benefit",
        "tags": ["住房补贴", "福利", "租房"],
        "updated_at": "2026-05-20T10:00:00Z",
    },
    {
        "id": "K005",
        "title": "培训与发展",
        "content": "新员工入职培训持续1周。技术培训：内部技术分享每周三下午。外部培训：年度预算5000元每人，可报销培训课程和书籍费用。晋升周期：每年2次晋升窗口（3月和9月）。",
        "category": "policy",
        "tags": ["培训", "晋升", "发展"],
        "updated_at": "2026-06-01T08:00:00Z",
    },
    {
        "id": "K006",
        "title": "面试指南",
        "content": "面试流程：简历筛选 → 技术笔试/作品集评审 → 技术面（2轮）→ HR面 → 终面。全程约2-3周。面试形式支持线上面试。技术面侧重算法/系统设计/项目经验。",
        "category": "guide",
        "tags": ["面试", "招聘", "流程"],
        "updated_at": "2026-07-05T15:00:00Z",
    },
]


@router.get("", response_model=list[KnowledgeDoc])
async def list_docs(category: str = "", user: dict = Depends(get_current_user)):
    """列出知识库文档，可按分类过滤。"""
    docs = MOCK_DOCS
    if category:
        docs = [d for d in docs if d["category"] == category]
    return [KnowledgeDoc(**d) for d in docs]


@router.get("/categories")
async def list_categories():
    """列出所有分类。"""
    cats = sorted(set(d["category"] for d in MOCK_DOCS))
    return [{"value": c, "label": {"policy": "政策制度", "benefit": "福利待遇", "guide": "流程指南", "faq": "常见问题"}.get(c, c)} for c in cats]


@router.post("/search", response_model=list[KnowledgeSearchResult])
async def search_knowledge(req: KnowledgeSearchRequest, user: dict = Depends(get_current_user)):
    """关键词搜索知识库（mock BM25 模拟）。"""
    results = []
    query_lower = req.query.lower()
    for doc in MOCK_DOCS:
        score = 0.0
        content_lower = doc["content"].lower()
        title_lower = doc["title"].lower()

        if query_lower in title_lower:
            score = 0.9
        elif query_lower in content_lower:
            score = 0.5
        else:
            for word in query_lower.split():
                if word in title_lower:
                    score += 0.2
                if word in content_lower:
                    score += 0.1

        if score > 0:
            results.append(KnowledgeSearchResult(
                title=doc["title"],
                content=doc["content"][:200],
                relevance=round(min(score, 0.99), 3),
                doc_id=doc["id"],
            ))

    results.sort(key=lambda x: x.relevance, reverse=True)
    return results[: req.top_k]


@router.post("", response_model=KnowledgeDoc)
async def create_doc(data: KnowledgeDoc, user: dict = Depends(get_current_user)):
    """新增知识库文档。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    import uuid
    doc = data.model_dump()
    doc["id"] = doc["id"] or f"K{uuid.uuid4().hex[:6].upper()}"
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    MOCK_DOCS.append(doc)
    return KnowledgeDoc(**doc)


@router.put("/{doc_id}", response_model=KnowledgeDoc)
async def update_doc(doc_id: str, data: KnowledgeDoc, user: dict = Depends(get_current_user)):
    """更新知识库文档。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    for i, d in enumerate(MOCK_DOCS):
        if d["id"] == doc_id:
            updated = data.model_dump()
            updated["id"] = doc_id
            updated["updated_at"] = datetime.now(timezone.utc).isoformat()
            MOCK_DOCS[i] = updated
            return KnowledgeDoc(**updated)
    raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")


@router.delete("/{doc_id}")
async def delete_doc(doc_id: str, user: dict = Depends(get_current_user)):
    """删除知识库文档。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    for i, d in enumerate(MOCK_DOCS):
        if d["id"] == doc_id:
            MOCK_DOCS.pop(i)
            return {"status": "deleted", "id": doc_id}
    raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")
