"""飞书 Webhook + Auth 端点。"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.middleware import MOCK_USERS, create_token, verify_token, get_current_user
from backend.models import LoginRequest, TokenResponse, StatsResponse
from db.repositories import UserRepo

logger = logging.getLogger("backend.routes.feishu")
router = APIRouter(tags=["feishu"])

from agent.skill_manager import skill_manager
from backend.routes.approval import MOCK_APPROVALS
from backend.routes.knowledge import MOCK_DOCS

_user_repo = UserRepo()


# ── Auth ────────────────────────────────────────────────────────────

@router.post("/api/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """登录 — 优先 MySQL，失败回退 mock。"""
    try:
        user = await _user_repo.get_by_username(req.username)
        if user and user["password"] == req.password:
            token = create_token(req.username)
            return TokenResponse(
                access_token=token,
                user_id=user["id"],
                user_role=user["role"],
                display_name=user["display_name"],
            )
    except Exception:
        logger.warning("MySQL unavailable for login, falling back to mock")

    user = MOCK_USERS.get(req.username)
    if not user or user["password"] != req.password:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(req.username)
    return TokenResponse(
        access_token=token,
        user_id=user["user_id"],
        user_role=user["role"],
        display_name=user["display_name"],
    )


@router.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)):
    """获取当前用户信息。"""
    return {
        "user_id": user["user_id"],
        "role": user["role"],
        "display_name": user["display_name"],
    }


# ── Dashboard Stats ─────────────────────────────────────────────────

@router.get("/api/stats", response_model=StatsResponse)
async def get_stats(user: dict = Depends(get_current_user)):
    """仪表盘统计数据。"""
    skill_manager.check_reload()
    pending_count = 0
    docs_count = 0
    try:
        from db.repositories import ApprovalRepo, KnowledgeRepo
        stats = await ApprovalRepo().get_stats()
        pending_count = stats.get("pending", 0) or 0
        cat_counts = await KnowledgeRepo().get_categories()
        all_docs = await KnowledgeRepo().list_all()
        docs_count = len(all_docs)
    except Exception:
        pending_count = sum(1 for a in MOCK_APPROVALS if a["status"] == "pending")
        docs_count = len(MOCK_DOCS)
    return StatsResponse(
        total_conversations=0,
        total_skills=len(skill_manager.all_skills),
        active_skills=sum(1 for s in skill_manager.all_skills if s.enabled),
        pending_approvals=pending_count,
        knowledge_docs=docs_count,
    )


# ── Feishu Webhook ──────────────────────────────────────────────────

@router.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """飞书事件订阅 Webhook 端点。"""
    from mcp_servers.feishu.event_handler import handle_webhook

    try:
        body = await request.json()
        headers = dict(request.headers)
        result = await handle_webhook(body, headers)
        return result
    except Exception as e:
        logger.exception("Feishu webhook error")
        raise HTTPException(status_code=400, detail=str(e))
