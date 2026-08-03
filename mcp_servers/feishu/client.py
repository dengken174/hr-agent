"""飞书开放平台 API 客户端。

管理 tenant_access_token 的获取和刷新，提供统一的异步 HTTP 接口。
所有 Tool 函数通过此客户端调用飞书 REST API。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from config import config

logger = logging.getLogger(__name__)


class FeishuClient:
    """飞书 API 客户端 — token 自动管理 + 通用请求方法。"""

    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
        base_url: str | None = None,
    ):
        self._app_id = app_id or config.feishu.app_id
        self._app_secret = app_secret or config.feishu.app_secret
        self._base_url = base_url or config.feishu.base_url
        self._token: str = ""
        self._token_expires_at: datetime | None = None
        self._client: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._app_id and self._app_secret)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=config.feishu.http_timeout)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── 鉴权 ──────────────────────────────────────────────────────

    async def _ensure_token(self):
        """确保 token 有效，过期自动刷新。"""
        now = datetime.now(timezone.utc)
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return

        client = await self._get_client()
        resp = await client.post(
            self._build_url("/auth/v3/tenant_access_token/internal"),
            json={"app_id": self._app_id, "app_secret": self._app_secret},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuAPIError(data.get("code"), data.get("msg", "token fetch failed"))

        self._token = data["tenant_access_token"]
        # 提前 200s 过期以留缓冲
        self._token_expires_at = now + timedelta(seconds=data.get("expire", 7200) - 200)
        logger.info("Feishu token refreshed, expires in %ds", data.get("expire", 7200))

    # ── HTTP 方法 ──────────────────────────────────────────────────

    def _build_url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    async def get(self, path: str, params: dict | None = None, **kwargs) -> dict:
        await self._ensure_token()
        client = await self._get_client()
        resp = await client.get(
            self._build_url(path),
            headers={"Authorization": f"Bearer {self._token}"},
            params=params,
            **kwargs,
        )
        return self._handle_response(resp)

    async def post(self, path: str, body: dict | None = None, params: dict | None = None, **kwargs) -> dict:
        await self._ensure_token()
        client = await self._get_client()
        resp = await client.post(
            self._build_url(path),
            headers={"Authorization": f"Bearer {self._token}"},
            json=body,
            params=params,
            **kwargs,
        )
        return self._handle_response(resp)

    async def patch(self, path: str, body: dict | None = None, params: dict | None = None, **kwargs) -> dict:
        await self._ensure_token()
        client = await self._get_client()
        resp = await client.patch(
            self._build_url(path),
            headers={"Authorization": f"Bearer {self._token}"},
            json=body,
            params=params,
            **kwargs,
        )
        return self._handle_response(resp)

    async def delete(self, path: str, params: dict | None = None, **kwargs) -> dict:
        await self._ensure_token()
        client = await self._get_client()
        resp = await client.delete(
            self._build_url(path),
            headers={"Authorization": f"Bearer {self._token}"},
            params=params,
            **kwargs,
        )
        return self._handle_response(resp)

    # ── 响应处理 ───────────────────────────────────────────────────

    @staticmethod
    def _handle_response(resp: httpx.Response) -> dict:
        if resp.status_code >= 500:
            resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            # 某些 API 返回非 JSON（如 404 HTML），转为统一错误
            raise FeishuAPIError(resp.status_code, resp.text[:200] or "non-json response")
        code = data.get("code", -1)
        if code != 0:
            logger.error("Feishu API error: code=%s msg=%s", code, data.get("msg", ""))
            raise FeishuAPIError(code, data.get("msg", "unknown error"), data)
        return data


class FeishuAPIError(Exception):
    """飞书 API 错误。"""

    def __init__(self, code: int, message: str, raw: dict | None = None):
        self.code = code
        self.message = message
        self.raw = raw or {}
        super().__init__(f"[{code}] {message}")


# ── 全局客户端实例 ────────────────────────────────────────────────────

feishu_client = FeishuClient()
