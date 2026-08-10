"""飞书 Webhook + Auth 端点。"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.middleware import MOCK_USERS, create_token, verify_token, verify_password, get_current_user
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
        if user and verify_password(req.password, user["password"]):
            token = create_token(user["id"], user["role"], user["display_name"])
            return TokenResponse(
                access_token=token,
                user_id=user["id"],
                user_role=user["role"],
                display_name=user["display_name"],
            )
    except Exception:
        logger.warning("MySQL unavailable for login, falling back to mock")

    user = MOCK_USERS.get(req.username)
    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(user["user_id"], user["role"], user["display_name"])
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


# ── User Management ───────────────────────────────────────────────────

@router.get("/api/users")
async def list_users(user: dict = Depends(get_current_user)):
    """列出所有用户（HR 管理员专用）。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    try:
        pool = await _get_pool()
        import aiomysql
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT id, username, display_name, role, open_id, voice_enabled, voice_rate, created_at FROM users ORDER BY id"
                )
                return await cur.fetchall()
    except Exception:
        logger.warning("MySQL unavailable for user list")
        return [
            {"id": v["user_id"], "username": k, "display_name": v["display_name"],
             "role": v["role"], "open_id": None, "voice_enabled": True, "voice_rate": "+20%"}
            for k, v in MOCK_USERS.items()
        ]


@router.post("/api/users")
async def create_user(data: dict, user: dict = Depends(get_current_user)):
    """创建新用户（HR 管理员专用）。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    hashed = hash_password(password)
    try:
        pool = await _get_pool()
        import aiomysql
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "INSERT INTO users (username, password, display_name, role, voice_enabled, voice_rate) VALUES (%s, %s, %s, %s, %s, %s)",
                    (username, hashed, data.get("display_name", username), data.get("role", "employee"),
                     data.get("voice_enabled", True), data.get("voice_rate", "+20%")),
                )
                return {"id": cur.lastrowid, "username": username, "status": "created"}
    except Exception as e:
        logger.exception("Create user failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/api/users/{user_id}")
async def update_user(user_id: int, data: dict, user: dict = Depends(get_current_user)):
    """更新用户信息（HR 管理员专用）。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    try:
        pool = await _get_pool()
        import aiomysql
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                sets = []
                vals = []
                for field in ["display_name", "role", "voice_enabled", "voice_rate"]:
                    if field in data:
                        sets.append(f"{field} = %s")
                        vals.append(data[field])
                if "password" in data and data["password"]:
                    sets.append("password = %s")
                    vals.append(hash_password(data["password"]))
                if not sets:
                    return {"status": "no changes"}
                vals.append(user_id)
                await cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = %s", vals)
                return {"status": "updated"} if cur.rowcount > 0 else {"status": "not found"}
    except Exception as e:
        logger.exception("Update user failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/users/{user_id}")
async def delete_user(user_id: int, user: dict = Depends(get_current_user)):
    """删除用户（HR 管理员专用）。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    try:
        pool = await _get_pool()
        import aiomysql
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
                return {"status": "deleted"} if cur.rowcount > 0 else {"status": "not found"}
    except Exception as e:
        logger.exception("Delete user failed")
        raise HTTPException(status_code=500, detail=str(e))


async def _get_pool():
    from db.connection import get_pool
    return await get_pool()


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
