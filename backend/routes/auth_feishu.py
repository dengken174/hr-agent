"""飞书 Webhook + Auth 端点。"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.middleware import MOCK_USERS, create_token, verify_token, get_current_user
from backend.models import LoginRequest, TokenResponse, StatsResponse

logger = logging.getLogger("backend.routes.feishu")
router = APIRouter(tags=["feishu"])

from agent.skill_manager import skill_manager
from backend.routes.approval import MOCK_APPROVALS
from backend.routes.knowledge import MOCK_DOCS


# ── Auth ────────────────────────────────────────────────────────────

@router.post("/api/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """Mock 登录 — 返回 JWT token。"""
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
    return StatsResponse(
        total_conversations=0,
        total_skills=len(skill_manager.all_skills),
        active_skills=sum(1 for s in skill_manager.all_skills if s.enabled),
        pending_approvals=sum(1 for a in MOCK_APPROVALS if a["status"] == "pending"),
        knowledge_docs=len(MOCK_DOCS),
    )


# ── Feishu Webhook ──────────────────────────────────────────────────

@router.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """飞书事件订阅 Webhook 端点。

    当前为占位实现，后续接入真实飞书事件处理器。
    """
    try:
        body = await request.json()
        event_type = body.get("header", {}).get("event_type", "")

        # URL 验证（飞书开放平台首次配置时）
        if "challenge" in body:
            return {"challenge": body["challenge"]}

        logger.info("Feishu webhook received: %s", event_type)
        return {"status": "ok"}

    except Exception as e:
        logger.exception("Feishu webhook error")
        raise HTTPException(status_code=400, detail=str(e))
