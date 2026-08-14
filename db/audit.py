"""审计日志仓库 — 记录数据访问行为（成功/拒绝/脱敏）。失败降级，不阻塞主流程。"""

import json
import logging

from db.connection import get_pool

logger = logging.getLogger(__name__)


class AuditRepo:
    @staticmethod
    def _build_insert(user_id, user_role, action, result, detail=None,
                      resource_type="", resource_id=""):
        sql = (
            "INSERT INTO audit_log "
            "(user_id, user_role, action, resource_type, resource_id, detail, result) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)"
        )
        params = (user_id, user_role, action, resource_type, resource_id,
                  json.dumps(detail or {}, ensure_ascii=False), result)
        return sql, params

    async def record(self, user_id, user_role, action, result, detail=None,
                     resource_type="", resource_id=""):
        try:
            sql, params = self._build_insert(
                user_id, user_role, action, result, detail, resource_type, resource_id)
            pool = await get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, params)
        except Exception:
            logger.warning("Audit record failed: %s %s user=%s", action, result, user_id)


audit_repo = AuditRepo()
