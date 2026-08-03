from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 本地开发使用 SQLite，生产用 PostgreSQL
DB_PATH = Path(__file__).resolve().parent / "approval.db"

STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft":     {"pending", "cancelled"},
    "pending":   {"approved", "rejected", "cancelled"},
    "approved":  set(),    # 终态
    "rejected":  set(),    # 终态
    "cancelled": set(),    # 终态
}

ALLOWED_TYPES = frozenset({"leave", "benefit", "reimbursement", "certificate"})


class ApprovalStateMachine:
    """轻量审批状态机。

    状态流转: draft → pending → approved/rejected → 终态。
    支持撤回、超时自动升级。
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or str(DB_PATH)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS approval_requests (
                    id            TEXT PRIMARY KEY,
                    type          TEXT NOT NULL,
                    title         TEXT NOT NULL,
                    applicant_id  INTEGER NOT NULL,
                    body          TEXT NOT NULL DEFAULT '{}',
                    status        TEXT NOT NULL DEFAULT 'draft',
                    assignee_id   INTEGER,
                    result_reason TEXT,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL,
                    resolved_at   TEXT
                );
                CREATE TABLE IF NOT EXISTS approval_logs (
                    id            TEXT PRIMARY KEY,
                    request_id    TEXT NOT NULL REFERENCES approval_requests(id),
                    from_status   TEXT,
                    to_status     TEXT NOT NULL,
                    operator_id   INTEGER NOT NULL,
                    operator_role TEXT DEFAULT 'applicant',
                    comment       TEXT,
                    created_at    TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ar_applicant ON approval_requests(applicant_id);
                CREATE INDEX IF NOT EXISTS idx_ar_assignee ON approval_requests(assignee_id);
                CREATE INDEX IF NOT EXISTS idx_ar_status ON approval_requests(status);
            """)

    # ── 状态转换 ─────────────────────────────────────────────────

    def can_transition(self, from_status: str, to_status: str) -> bool:
        return to_status in STATUS_TRANSITIONS.get(from_status, set())

    def transition(
        self,
        request_id: str,
        to_status: str,
        operator_id: int,
        operator_role: str = "applicant",
        comment: str = "",
    ) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if not row:
                return False

            from_status = row["status"]
            if not self.can_transition(from_status, to_status):
                return False

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE approval_requests
                   SET status = ?, updated_at = ?, resolved_at = ?
                   WHERE id = ?""",
                (to_status, now, now if to_status in ("approved", "rejected", "cancelled") else None, request_id),
            )

            log_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO approval_logs (id, request_id, from_status, to_status,
                       operator_id, operator_role, comment, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (log_id, request_id, from_status, to_status, operator_id, operator_role, comment, now),
            )
        return True

    # ── CRUD ─────────────────────────────────────────────────────

    def create(
        self, req_type: str, title: str, applicant_id: int,
        body: dict | None = None, assignee_id: int | None = None,
    ) -> str:
        if req_type not in ALLOWED_TYPES:
            raise ValueError(f"Invalid type: {req_type}, allowed: {ALLOWED_TYPES}")

        req_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO approval_requests
                   (id, type, title, applicant_id, body, status, assignee_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?)""",
                (req_id, req_type, title, applicant_id, json.dumps(body or {}), assignee_id, now, now),
            )
            log_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO approval_logs
                   (id, request_id, from_status, to_status, operator_id, operator_role, comment, created_at)
                   VALUES (?, ?, NULL, 'draft', ?, 'applicant', '创建申请', ?)""",
                (log_id, req_id, applicant_id, now),
            )
        return req_id

    def submit(self, request_id: str, operator_id: int) -> bool:
        """从草稿提交为待审批。"""
        return self.transition(request_id, "pending", operator_id, "applicant", "提交审批")

    def approve(self, request_id: str, operator_id: int, comment: str = "") -> bool:
        return self.transition(request_id, "approved", operator_id, "assignee", comment or "同意")

    def reject(self, request_id: str, operator_id: int, reason: str) -> bool:
        return self.transition(request_id, "rejected", operator_id, "assignee", reason)

    def cancel(self, request_id: str, operator_id: int) -> bool:
        return self.transition(request_id, "cancelled", operator_id, "applicant", "申请人撤回")

    def get(self, request_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["body"] = json.loads(result["body"])
            return result

    def list_by_applicant(self, applicant_id: int, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM approval_requests WHERE applicant_id = ? ORDER BY created_at DESC LIMIT ?",
                (applicant_id, limit),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    def list_pending_for_assignee(self, assignee_id: int, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM approval_requests WHERE assignee_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT ?",
                (assignee_id, limit),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    def get_logs(self, request_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM approval_logs WHERE request_id = ? ORDER BY created_at ASC",
                (request_id,),
            ).fetchall()
            return [dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "body" in d and isinstance(d["body"], str):
        d["body"] = json.loads(d["body"])
    return d


# 全局单例
approval_sm = ApprovalStateMachine()
