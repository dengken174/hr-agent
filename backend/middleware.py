"""JWT Mock 鉴权 + RBAC + 审计日志中间件。"""

import logging
import time
from typing import Callable

from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("backend.middleware")

# ── Mock 用户数据库 ──────────────────────────────────────────────────

MOCK_USERS = {
    "admin": {
        "password": "admin123",
        "user_id": 2001,
        "role": "hr_admin",
        "display_name": "HR Admin",
    },
    "employee": {
        "password": "emp123",
        "user_id": 1001,
        "role": "employee",
        "display_name": "张三",
    },
    "interviewer": {
        "password": "int123",
        "user_id": 3001,
        "role": "interviewer",
        "display_name": "面试者",
    },
}

MOCK_TOKENS: dict[str, dict] = {}


# ── Token 工具函数 ──────────────────────────────────────────────────

def create_token(username: str) -> str:
    import uuid
    token = f"mock-jwt-{uuid.uuid4().hex[:16]}"
    user = MOCK_USERS[username]
    MOCK_TOKENS[token] = {
        "user_id": user["user_id"],
        "role": user["role"],
        "display_name": user["display_name"],
        "expires_at": time.time() + 86400,
    }
    return token


def verify_token(token: str) -> dict | None:
    data = MOCK_TOKENS.get(token)
    if not data:
        return None
    if time.time() > data["expires_at"]:
        del MOCK_TOKENS[token]
        return None
    return data


# ── 鉴权依赖 ────────────────────────────────────────────────────────

async def get_current_user(authorization: str = Header(default="")):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    token = authorization.replace("Bearer ", "")
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


# ── RBAC 装饰器 ─────────────────────────────────────────────────────

def require_role(*roles: str):
    """要求用户具有指定角色之一。"""

    async def dependency(user: dict = None):
        if user is None:
            user = {}
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail=f"Requires one of roles: {roles}")
        return user

    return dependency


# ── 审计日志中间件 ──────────────────────────────────────────────────

class AuditMiddleware(BaseHTTPMiddleware):
    """记录每个请求的审计日志。"""

    async def dispatch(self, request: Request, call_next: Callable):
        start = time.time()
        response = await call_next(request)
        elapsed_ms = (time.time() - start) * 1000
        logger.info(
            "AUDIT | %s %s | %d | %.1fms | %s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request.client.host if request.client else "-",
        )
        return response
