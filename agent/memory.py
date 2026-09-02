import json
import logging
from datetime import datetime, timezone
from typing import Any

from langchain_classic.memory import ConversationBufferWindowMemory
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from config import config

logger = logging.getLogger(__name__)


# ── L1: Redis 会话缓冲 ──────────────────────────────────────────────

class RedisSessionBuffer:
    """Redis 会话缓冲，连接失败时降级为内存存储。"""

    def __init__(self, redis_url: str | None = None):
        self._redis_url = redis_url or config.redis.url
        self._ttl = config.redis.session_ttl
        self._max_rounds = config.redis.max_history_rounds
        self._redis = None
        self._available = True
        self._fallback: dict[str, list[dict]] = {}

    async def _ensure_connected(self):
        if self._redis is not None:
            return
        try:
            import redis.asyncio as aioredis
            self._redis = await aioredis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()
            self._available = True
        except Exception:
            self._available = False
            self._redis = None

    def _key(self, session_id: str) -> str:
        return f"session:{session_id}"

    async def get_messages(self, session_id: str) -> list[dict]:
        await self._ensure_connected()
        if not self._available:
            return self._fallback.get(self._key(session_id), [])
        try:
            raw = await self._redis.get(self._key(session_id))
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return []

    async def add_message(self, session_id: str, role: str, content: str):
        await self._ensure_connected()
        key = self._key(session_id)
        if not self._available:
            msgs = self._fallback.get(key, [])
        else:
            msgs = await self.get_messages(session_id)
        msgs.append({"role": role, "content": content, "time": datetime.now(timezone.utc).isoformat()})
        max_messages = self._max_rounds * 2
        if len(msgs) > max_messages:
            msgs = msgs[-max_messages:]
        try:
            if self._redis:
                await self._redis.setex(key, self._ttl, json.dumps(msgs, ensure_ascii=False))
            else:
                self._fallback[key] = msgs
        except Exception:
            self._fallback[key] = msgs

    async def clear(self, session_id: str):
        key = self._key(session_id)
        self._fallback.pop(key, None)
        if self._redis:
            try:
                await self._redis.delete(key)
            except Exception:
                pass

    async def close(self):
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None


# ── L2: MySQL 对话历史 ───────────────────────────────────────────────

class MySQLChatHistory:
    """MySQL 对话历史持久化，连接失败时静默降级。"""

    def __init__(self):
        self._available = True

    async def _get_pool(self):
        from db.connection import get_pool
        try:
            pool = await get_pool()
            self._available = True  # retry succeeded, un-latch
            return pool
        except Exception:
            return None

    async def save_message(
        self,
        session_id: str,
        user_id: int,
        role: str,
        content: str,
        intent: str = "",
        tool_calls: str = "",
    ):
        try:
            import uuid
            pool = await self._get_pool()
            if not pool:
                return
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "INSERT INTO conversations (id, session_id, user_id, role, content, intent) VALUES (%s, %s, %s, %s, %s, %s)",
                        (uuid.uuid4().hex, session_id, user_id, role, content, intent),
                    )
        except Exception:
            logger.warning("MySQL unavailable, chat history not persisted")

    async def get_history(self, session_id: str, limit: int = 50) -> list[dict]:
        try:
            import aiomysql
            pool = await self._get_pool()
            if not pool:
                return []
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        "SELECT role, content, intent, created_at FROM conversations WHERE session_id = %s ORDER BY created_at DESC LIMIT %s",
                        (session_id, limit),
                    )
                    rows = await cur.fetchall()
                    return [dict(r) for r in reversed(rows)]
        except Exception:
            return []

    async def close(self):
        pass  # pool 由 db.connection 统一管理


# ── 辅助：消息序列化 ─────────────────────────────────────────────────

def _msg_to_langchain(msg: dict) -> BaseMessage:
    role = msg["role"]
    content = msg.get("content", "")
    if role == "user":
        return HumanMessage(content=content)
    elif role == "assistant":
        return AIMessage(content=content)
    elif role == "system":
        return SystemMessage(content=content)
    return HumanMessage(content=content)


# ── L3: LangChain SummaryBufferMemory ───────────────────────────────

class MemoryManager:
    """三层记忆管理器：Redis 缓冲 + MySQL 持久化 + ConversationSummaryBufferMemory。

    基础设施不可用时自动降级，不阻塞对话。
    """

    def __init__(
        self,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        redis_url: str | None = None,
    ):
        self._session_buffer = RedisSessionBuffer(redis_url)
        self._chat_history = MySQLChatHistory()
        self._llm = ChatOpenAI(
            model=config.llm.chat_model,
            api_key=llm_api_key or config.llm.api_key,
            base_url=llm_base_url or config.llm.base_url,
            temperature=0.1,
        )

    def create_summary_memory(self) -> ConversationBufferWindowMemory:
        return ConversationBufferWindowMemory(
            k=config.redis.max_history_rounds,
            return_messages=True,
            memory_key="chat_history",
        )

    async def load_context(self, session_id: str, memory: ConversationBufferWindowMemory):
        redis_msgs = await self._session_buffer.get_messages(session_id)
        if redis_msgs:
            for msg in redis_msgs:
                if msg["role"] == "user":
                    memory.chat_memory.add_user_message(msg["content"])
                elif msg["role"] == "assistant":
                    memory.chat_memory.add_ai_message(msg["content"])
            return

        pg_history = await self._chat_history.get_history(session_id)
        for msg in pg_history:
            if msg["role"] == "user":
                memory.chat_memory.add_user_message(msg["content"])
            elif msg["role"] == "assistant":
                memory.chat_memory.add_ai_message(msg["content"])

    async def save_turn(
        self,
        session_id: str,
        user_id: int,
        user_message: str,
        assistant_message: str,
        intent: str = "",
        tool_calls: str = "",
    ):
        await self._session_buffer.add_message(session_id, "user", user_message)
        await self._session_buffer.add_message(session_id, "assistant", assistant_message)
        await self._chat_history.save_message(session_id, user_id, "user", user_message, intent, tool_calls)
        await self._chat_history.save_message(session_id, user_id, "assistant", assistant_message, intent, tool_calls)

    async def save_archive(self, session_id, user_id, user_message, assistant_message, intent=""):
        """仅写 MySQL（append-only 审计/冷档），不经 Redis 缓冲（graph 路径专用）。"""
        await self._chat_history.save_message(session_id, user_id, "user", user_message, intent)
        await self._chat_history.save_message(session_id, user_id, "assistant", assistant_message, intent)

    async def clear_session(self, session_id: str):
        await self._session_buffer.clear(session_id)

    async def close(self):
        await self._session_buffer.close()
        await self._chat_history.close()


class _LazyMemoryManager:
    """Lazy proxy — defers MemoryManager creation until first use."""

    def __init__(self):
        self._instance: MemoryManager | None = None

    def _ensure(self) -> MemoryManager:
        if self._instance is None:
            self._instance = MemoryManager()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._ensure(), name)


memory_manager: MemoryManager = _LazyMemoryManager()  # type: ignore
