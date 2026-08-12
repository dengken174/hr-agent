"""知识库管理端点 — CRUD + 文件上传解析。"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form

from backend.middleware import get_current_user
from backend.models import KnowledgeDoc, KnowledgeSearchRequest, KnowledgeSearchResult
from db.repositories import KnowledgeRepo
from db.document_parser import parse_document, SUPPORTED as SUPPORTED_FORMATS
from db.chunker import chunk_document

logger = logging.getLogger("backend.routes.knowledge")
router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

_knowledge_repo = KnowledgeRepo()

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
    try:
        docs = await _knowledge_repo.list_all(category)
        return [KnowledgeDoc(**d) for d in docs]
    except Exception:
        logger.warning("MySQL unavailable, falling back to mock")
        docs = MOCK_DOCS
        if category:
            docs = [d for d in docs if d["category"] == category]
        return [KnowledgeDoc(**d) for d in docs]


@router.get("/formats")
async def supported_formats():
    """返回支持的文档格式列表。"""
    return {
        "formats": sorted(SUPPORTED_FORMATS),
        "office": sorted([f for f in SUPPORTED_FORMATS if f in {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls"}]),
        "text": sorted([f for f in SUPPORTED_FORMATS if f in {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm"}]),
        "image": sorted([f for f in SUPPORTED_FORMATS if f in {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}]),
        "max_file_size_mb": None,  # 由 nginx/CDN 控制
        "ocr_supported": True,
    }


@router.get("/categories")
async def list_categories():
    """列出所有分类。"""
    try:
        cats = await _knowledge_repo.get_categories()
        return [{"value": c, "label": {"policy": "政策制度", "benefit": "福利待遇", "guide": "流程指南", "faq": "常见问题"}.get(c, c)} for c in cats]
    except Exception:
        cats = sorted(set(d["category"] for d in MOCK_DOCS))
        return [{"value": c, "label": {"policy": "政策制度", "benefit": "福利待遇", "guide": "流程指南", "faq": "常见问题"}.get(c, c)} for c in cats]


@router.post("/search", response_model=list[KnowledgeSearchResult])
async def search_knowledge(req: KnowledgeSearchRequest, user: dict = Depends(get_current_user)):
    """关键词搜索知识库。"""
    try:
        return [KnowledgeSearchResult(**r) for r in await _knowledge_repo.search(req.query, req.top_k)]
    except Exception:
        logger.warning("MySQL unavailable for search, falling back to mock")

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
    try:
        doc = await _knowledge_repo.create(data.model_dump())
        await _sync_add_index(doc)
        return KnowledgeDoc(**doc)
    except Exception:
        logger.warning("MySQL unavailable for create, falling back to mock")
        import uuid
        doc = data.model_dump()
        doc["id"] = doc["id"] or f"K{uuid.uuid4().hex[:6].upper()}"
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        MOCK_DOCS.append(doc)
        await _sync_add_index(doc)
        return KnowledgeDoc(**doc)


@router.put("/{doc_id}", response_model=KnowledgeDoc)
async def update_doc(doc_id: str, data: KnowledgeDoc, user: dict = Depends(get_current_user)):
    """更新知识库文档。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    try:
        doc = await _knowledge_repo.update(doc_id, data.model_dump())
        if doc:
            await _sync_add_index(doc)
            return KnowledgeDoc(**doc)
    except Exception:
        logger.warning("MySQL unavailable for update, falling back to mock")
        for i, d in enumerate(MOCK_DOCS):
            if d["id"] == doc_id:
                updated = data.model_dump()
                updated["id"] = doc_id
                updated["updated_at"] = datetime.now(timezone.utc).isoformat()
                MOCK_DOCS[i] = updated
                await _sync_add_index(updated)
                return KnowledgeDoc(**updated)
    raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")


@router.delete("/{doc_id}")
async def delete_doc(doc_id: str, user: dict = Depends(get_current_user)):
    """删除知识库文档。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    try:
        ok = await _knowledge_repo.delete(doc_id)
        if ok:
            await _sync_remove_index(doc_id)
            return {"status": "deleted", "id": doc_id}
    except Exception:
        logger.warning("MySQL unavailable for delete, falling back to mock")
        for i, d in enumerate(MOCK_DOCS):
            if d["id"] == doc_id:
                MOCK_DOCS.pop(i)
                await _sync_remove_index(doc_id)
                return {"status": "deleted", "id": doc_id}
    raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")


@router.post("/upload")
async def upload_document(
    files: list[UploadFile] = File(..., description="文档文件 (PDF/DOCX/PPTX/XLSX/MD/TXT/CSV)"),
    category: str = Form("", description="分类: policy/benefit/guide/faq"),
    tags: str = Form("", description="标签，逗号分隔"),
    enable_ocr: bool = Form(False, description="是否启用图片 OCR"),
    user: dict = Depends(get_current_user),
):
    """上传文档文件，自动解析 → 分块 → 入库 → 建索引。

    支持格式: PDF, DOCX, PPTX, XLSX, MD, TXT, CSV, JSON, HTML, 图片(需启用OCR)
    每个文件按标题层级智能分块，每个 chunk 作为一条知识库文档存储。
    """
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员可上传文档")

    if not files:
        raise HTTPException(status_code=400, detail="请选择要上传的文件")

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    results = []
    for file in files:
        filename = file.filename or "unknown"
        try:
            # 1. 读取文件内容
            file_bytes = await file.read()

            # 2. 解析文档
            parsed = await parse_document(file_bytes, filename, enable_ocr=enable_ocr)
            logger.info("Parsed %s: %d chars, %d pages", filename, len(parsed.content), parsed.page_count)

            # 3. 智能分块
            chunks = chunk_document(
                parsed.content,
                doc_title=parsed.title,
                chunk_size=500,
                chunk_overlap=80,
            )
            logger.info("Chunked %s into %d chunks", filename, len(chunks))

            # 4. 每个 chunk 存入 MySQL + 同步向量索引
            saved_chunks = []
            for i, chunk in enumerate(chunks):
                doc_id = f"{parsed.title[:30]}_{uuid.uuid4().hex[:6]}"
                chunk_title = chunk.title or parsed.title
                if len(chunks) > 1:
                    chunk_title += f" (第{i + 1}段)"

                doc_data = {
                    "id": doc_id,
                    "title": chunk_title,
                    "content": chunk.text,
                    "category": category or _guess_category(parsed.title, parsed.content),
                    "tags": tag_list,
                    "created_by": user["user_id"],
                }
                try:
                    saved = await _knowledge_repo.create(doc_data)
                    await _sync_add_index(saved)
                    saved_chunks.append({"id": doc_id, "title": chunk_title, "chars": len(chunk.text)})
                except Exception:
                    logger.warning("Failed to save chunk %s/%s", i + 1, len(chunks))

            results.append({
                "filename": filename,
                "title": parsed.title,
                "source_format": parsed.source_format,
                "chars": len(parsed.content),
                "pages": parsed.page_count,
                "chunks": len(chunks),
                "saved": len(saved_chunks),
                "details": saved_chunks,
            })

        except ValueError as e:
            logger.warning("Parse error for %s: %s", filename, e)
            results.append({"filename": filename, "error": str(e)})
        except Exception as e:
            logger.exception("Unexpected error parsing %s", filename)
            results.append({"filename": filename, "error": f"解析失败: {e}"})

    success_count = sum(1 for r in results if "error" not in r)
    return {
        "message": f"处理完成: {success_count}/{len(files)} 个文件成功",
        "results": results,
    }


@router.post("/reindex")
async def reindex_knowledge(user: dict = Depends(get_current_user)):
    """重建向量索引（HR 管理员操作）。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    try:
        from db.vector_store import build_index
        docs = await _knowledge_repo.list_all()
        if not docs:
            docs = MOCK_DOCS
        count = await build_index(docs)
        return {"status": "ok", "indexed": count}
    except Exception as e:
        logger.exception("Reindex failed")
        raise HTTPException(status_code=500, detail=f"索引构建失败: {e}")


@router.get("/index/stats")
async def index_stats():
    """查看向量索引统计。"""
    try:
        from db.vector_store import index_stats as stats
        return await stats()
    except Exception:
        return {"total": 0, "dim": 0, "indexed_docs": 0}


async def _sync_add_index(doc: dict):
    """新增/更新文档后同步到向量索引。"""
    try:
        from db.vector_store import add_document
        await add_document(doc)
    except Exception:
        logger.debug("Vector index sync skipped")


async def _sync_remove_index(doc_id: str):
    """删除文档后同步移除向量索引。"""
    try:
        from db.vector_store import remove_document
        await remove_document(doc_id)
    except Exception:
        logger.debug("Vector index remove skipped")


def _guess_category(title: str, content: str) -> str:
    """根据文档标题和内容自动推测分类。"""
    text = f"{title} {content[:500]}".lower()
    if any(w in text for w in ["入职", "报到", "面试", "招聘", "流程", "指南"]):
        return "guide"
    if any(w in text for w in ["福利", "补贴", "年假", "五险一金", "社保", "公积金", "薪酬"]):
        return "benefit"
    if any(w in text for w in ["政策", "制度", "规定", "标准", "管理办法"]):
        return "policy"
    if any(w in text for w in ["问题", "解答", "常见", "FAQ"]):
        return "faq"
    return "policy"
