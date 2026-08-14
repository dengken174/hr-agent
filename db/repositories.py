import json
import uuid
import logging
from datetime import datetime, timezone

import aiomysql
from db.connection import get_pool
from db.scope import DataScope

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


class UserRepo:
    async def get_by_username(self, username: str) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM users WHERE username = %s", (username,))
                return await cur.fetchone()

    async def get_by_id(self, user_id: int) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                return await cur.fetchone()

    async def get_by_open_id(self, open_id: str) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM users WHERE open_id = %s", (open_id,))
                return await cur.fetchone()

    async def upsert_by_open_id(self, open_id: str, display_name: str = "") -> dict:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM users WHERE open_id = %s", (open_id,))
                user = await cur.fetchone()
                if user:
                    return user
                # Create new user for this Feishu user
                username = f"feishu_{open_id[-12:]}"
                name = display_name or f"飞书用户{open_id[-6:]}"
                await cur.execute(
                    "INSERT INTO users (username, password, display_name, role, open_id) VALUES (%s, %s, %s, %s, %s)",
                    (username, "", name, "employee", open_id),
                )
                return {"id": cur.lastrowid, "username": username, "role": "employee",
                        "display_name": name, "open_id": open_id, "voice_enabled": True, "voice_rate": "+20%"}


class ConversationRepo:
    async def save(self, session_id: str, user_id: int, role: str, content: str, intent: str = "") -> str:
        conv_id = uuid.uuid4().hex
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO conversations (id, session_id, user_id, role, content, intent) VALUES (%s, %s, %s, %s, %s, %s)",
                    (conv_id, session_id, user_id, role, content, intent),
                )
        return conv_id

    async def get_history(self, session_id: str, limit: int = 50) -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT role, content, intent, created_at FROM conversations WHERE session_id = %s ORDER BY created_at DESC LIMIT %s",
                    (session_id, limit),
                )
                rows = await cur.fetchall()
                return list(reversed(rows))

    async def delete_session(self, session_id: str, user_id: int) -> bool:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM conversations WHERE session_id = %s AND user_id = %s",
                    (session_id, user_id),
                )
                return cur.rowcount > 0

    async def list_sessions(self, user_id: int) -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT
                        session_id,
                        (SELECT content FROM conversations c2
                         WHERE c2.session_id = c1.session_id AND c2.role = 'user' AND c2.user_id = c1.user_id
                         ORDER BY c2.created_at ASC LIMIT 1
                        ) AS title,
                        COUNT(*) AS message_count,
                        MAX(created_at) AS updated_at
                    FROM conversations c1
                    WHERE user_id = %s
                    GROUP BY session_id
                    ORDER BY updated_at DESC
                    LIMIT 50
                """, (user_id,))
                rows = await cur.fetchall()
                for r in rows:
                    if r.get("title") and len(r["title"]) > 20:
                        r["title"] = r["title"][:20] + "..."
                    if r.get("updated_at"):
                        r["updated_at"] = r["updated_at"].strftime('%Y-%m-%dT%H:%M:%S')
                return rows


class ApprovalRepo:
    async def list_all(self, status: str = "", scope: DataScope | None = None) -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                where = []
                params = []
                if status:
                    where.append("status = %s")
                    params.append(status)
                if scope and scope.role == "employee":
                    where.append("applicant_id = %s")
                    params.append(scope.user_id)
                where_clause = (" WHERE " + " AND ".join(where)) if where else ""
                await cur.execute(
                    f"SELECT * FROM approval_requests{where_clause} ORDER BY created_at DESC",
                    tuple(params),
                )
                rows = await cur.fetchall()
                for r in rows:
                    if isinstance(r.get("body"), str):
                        r["body"] = json.loads(r["body"])
                    if r.get("created_at"):
                        r["created_at"] = r["created_at"].strftime('%Y-%m-%dT%H:%M:%S')
                    if r.get("updated_at"):
                        r["updated_at"] = r["updated_at"].strftime('%Y-%m-%dT%H:%M:%S')
                return rows

    async def get_by_id(self, approval_id: str) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM approval_requests WHERE id = %s", (approval_id,))
                row = await cur.fetchone()
                if row and isinstance(row.get("body"), str):
                    row["body"] = json.loads(row["body"])
                return row

    async def update_status(self, approval_id: str, status: str, operator_id: int, comment: str = "") -> bool:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE approval_requests SET status = %s, result_reason = %s, updated_at = NOW(3), resolved_at = NOW(3) WHERE id = %s",
                    (status, comment, approval_id),
                )
                return cur.rowcount > 0

    async def get_stats(self) -> dict:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT
                        SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                        SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                        SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                        COUNT(*) AS total
                    FROM approval_requests
                """)
                return await cur.fetchone()


class KnowledgeRepo:
    async def list_all(self, category: str = "") -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if category:
                    await cur.execute("SELECT * FROM knowledge_docs WHERE category = %s ORDER BY updated_at DESC", (category,))
                else:
                    await cur.execute("SELECT * FROM knowledge_docs ORDER BY updated_at DESC")
                rows = await cur.fetchall()
                for r in rows:
                    if isinstance(r.get("tags"), str):
                        r["tags"] = json.loads(r["tags"])
                    if r.get("updated_at"):
                        r["updated_at"] = r["updated_at"].strftime('%Y-%m-%dT%H:%M:%S')
                    if r.get("created_at"):
                        r["created_at"] = r["created_at"].strftime('%Y-%m-%dT%H:%M:%S')
                return rows

    async def get_categories(self) -> list[str]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT DISTINCT category FROM knowledge_docs ORDER BY category")
                rows = await cur.fetchall()
                return [r[0] for r in rows]

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        # 混合检索：双路并行 + RRF 融合
        try:
            from db.vector_store import hybrid_search
            results = await hybrid_search(query, top_k)
            if results:
                return results
        except Exception:
            logger.debug("Hybrid search unavailable, falling back to keyword search")

        # 回退: MySQL LIKE 关键词搜索
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                like = f"%{query}%"
                await cur.execute(
                    "SELECT id, title, content, category FROM knowledge_docs WHERE title LIKE %s OR content LIKE %s LIMIT %s",
                    (like, like, top_k),
                )
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    results.append({
                        "title": r["title"],
                        "content": r["content"][:200],
                        "relevance": 0.9 if query in r["title"] else 0.5,
                        "doc_id": r["id"],
                    })
                return results

    async def create(self, data: dict) -> dict:
        doc_id = data.get("id") or f"K{uuid.uuid4().hex[:6].upper()}"
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO knowledge_docs (id, title, content, category, tags, created_by) VALUES (%s, %s, %s, %s, %s, %s)",
                    (doc_id, data["title"], data["content"], data.get("category", ""), json.dumps(data.get("tags", []), ensure_ascii=False), data.get("created_by")),
                )
        data["id"] = doc_id
        return data

    async def update(self, doc_id: str, data: dict) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE knowledge_docs SET title=%s, content=%s, category=%s, tags=%s, updated_at=NOW(3) WHERE id=%s",
                    (data["title"], data["content"], data.get("category", ""), json.dumps(data.get("tags", []), ensure_ascii=False), doc_id),
                )
                if cur.rowcount == 0:
                    return None
        return await self.get_by_id(doc_id)

    async def get_by_id(self, doc_id: str) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM knowledge_docs WHERE id = %s", (doc_id,))
                return await cur.fetchone()

    async def delete(self, doc_id: str) -> bool:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM knowledge_docs WHERE id = %s", (doc_id,))
                return cur.rowcount > 0
