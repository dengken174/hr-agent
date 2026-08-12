"""JWT 鉴权 + RBAC + 审计日志中间件。"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import bcrypt
import jwt
from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from config import config

logger = logging.getLogger("backend.middleware")

# ── JWT 配置 ──────────────────────────────────────────────────────────

import os as _os
JWT_SECRET = _os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is required. Generate: python -c 'import secrets; print(secrets.token_hex(32))'")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 24


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: int, role: str, display_name: str) -> str:
    payload = {
        "user_id": user_id,
        "role": role,
        "display_name": display_name,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "user_id": payload["user_id"],
            "role": payload["role"],
            "display_name": payload["display_name"],
        }
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ── Mock 用户（仅 DEV_MODE=1 时启用，DB 故障时拒绝登录而非降级到 mock）───

_DEV_MODE = _os.getenv("DEV_MODE", "") == "1"

MOCK_USERS: dict[str, dict] = {}
if _DEV_MODE:
    _mock_pw = _os.getenv("DEV_MOCK_PASSWORD", "dev123456")
    MOCK_USERS = {
        "admin": {"password": hash_password(_mock_pw), "user_id": 2001, "role": "hr_admin", "display_name": "Dev Admin"},
        "employee": {"password": hash_password(_mock_pw), "user_id": 1001, "role": "employee", "display_name": "Dev Employee"},
    }


# ── 鉴权依赖 ──────────────────────────────────────────────────────────

async def get_current_user(authorization: str = Header(default="")):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    token = authorization.replace("Bearer ", "")
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


# ── RBAC ───────────────────────────────────────────────────────────────

def require_role(*roles: str):
    async def dependency(user: dict = None):
        if user is None:
            user = {}
        if user.get("role") not in roles:
            raise HTTPException(status_code=403, detail=f"Requires one of roles: {roles}")
        return user
    return dependency


# ── 审计日志中间件 ────────────────────────────────────────────────────

class AuditMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        # Skip audit for static files
        self._skip_prefixes = ("/assets/",)

    async def dispatch(self, request: Request, call_next: Callable):
        if any(request.url.path.startswith(p) for p in self._skip_prefixes):
            return await call_next(request)
        start = time.time()
        response = await call_next(request)
        elapsed_ms = (time.time() - start) * 1000
        logger.info(
            "AUDIT | %s %s | %d | %.1fms | %s",
            request.method, request.url.path,
            response.status_code, elapsed_ms,
            request.client.host if request.client else "-",
        )
        return response
