"""审批管理端点。"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from backend.middleware import get_current_user
from backend.models import ApprovalAction, ApprovalItem
from db.repositories import ApprovalRepo, UserRepo

logger = logging.getLogger("backend.routes.approval")
router = APIRouter(prefix="/api/approvals", tags=["approvals"])

_approval_repo = ApprovalRepo()
_user_repo = UserRepo()


async def _notify_applicant(applicant_id: int, title: str, new_status: str, comment: str = ""):
    """审批结果变化后，推送飞书消息通知申请人。"""
    try:
        user = await _user_repo.get_by_id(applicant_id)
        if not user or not user.get("open_id"):
            logger.debug("User %s has no open_id, skipping feishu notification", applicant_id)
            return
        open_id = user["open_id"]
    except Exception:
        logger.debug("Cannot lookup user %s, skipping notification", applicant_id)
        return

    status_text = "已通过" if new_status == "approved" else "已驳回"
    msg = f"您的审批「{title}」{status_text}"
    if comment:
        msg += f"，备注：{comment}"
    msg += "。请在系统中查看详情。"

    try:
        from mcp_servers.feishu.client import feishu_client
        if not feishu_client.is_configured:
            logger.info("[mock] Feishu notification to %s: %s", open_id, msg)
            return
        content = '{"text":"' + msg.replace('"', '\\"').replace('\n', '\\n') + '"}'
        await feishu_client.post(
            "/im/v1/messages",
            params={"receive_id_type": "open_id"},
            body={"receive_id": open_id, "msg_type": "text", "content": content},
        )
        logger.info("Feishu notification sent to user %s (open_id=%s)", applicant_id, open_id)
    except Exception:
        logger.exception("Failed to send feishu notification")

# ── Mock 审批数据 ────────────────────────────────────────────────────

MOCK_APPROVALS: list[dict] = [
    {
        "id": "APR-001",
        "type": "leave",
        "applicant": "张三",
        "applicant_id": 1001,
        "department": "技术部",
        "title": "年假申请 — 3天",
        "detail": {"leave_type": "年假", "start_date": "2026-07-20", "end_date": "2026-07-22", "days": 3},
        "status": "pending",
        "created_at": "2026-07-15T10:30:00Z",
        "updated_at": "2026-07-15T10:30:00Z",
    },
    {
        "id": "APR-002",
        "type": "expense",
        "applicant": "李四",
        "applicant_id": 1002,
        "department": "技术部",
        "title": "差旅报销 — 上海出差",
        "detail": {"amount": 3500, "category": "交通+住宿", "trip": "上海"},
        "status": "pending",
        "created_at": "2026-07-14T14:20:00Z",
        "updated_at": "2026-07-14T14:20:00Z",
    },
    {
        "id": "APR-003",
        "type": "benefit",
        "applicant": "赵六",
        "applicant_id": 2001,
        "department": "人力资源部",
        "title": "培训费用申请 — Python进阶",
        "detail": {"amount": 2800, "course": "Python进阶", "platform": "极客时间"},
        "status": "approved",
        "created_at": "2026-07-10T09:00:00Z",
        "updated_at": "2026-07-11T16:00:00Z",
    },
    {
        "id": "APR-004",
        "type": "leave",
        "applicant": "孙八",
        "applicant_id": 3001,
        "department": "产品部",
        "title": "事假申请 — 1天",
        "detail": {"leave_type": "事假", "start_date": "2026-07-18", "days": 1},
        "status": "rejected",
        "created_at": "2026-07-12T11:00:00Z",
        "updated_at": "2026-07-13T09:30:00Z",
    },
]


def _to_approval_item(row: dict) -> dict:
    """将 MySQL 行转为与 MOCK_APPROVALS 兼容的格式。"""
    return {
        "id": row["id"],
        "type": row["type"],
        "applicant": f"用户{row['applicant_id']}",
        "applicant_id": row["applicant_id"],
        "department": "",
        "title": row["title"],
        "detail": row.get("body", {}) if isinstance(row.get("body"), dict) else {},
        "status": row["status"],
        "created_at": row.get("created_at", ""),
        "updated_at": row.get("updated_at", ""),
    }


@router.get("", response_model=list[ApprovalItem])
async def list_approvals(
    status: str = "",
    user: dict = Depends(get_current_user),
):
    """查询审批列表，可按状态过滤。"""
    try:
        rows = await _approval_repo.list_all(status)
        items = [_to_approval_item(r) for r in rows]
    except Exception:
        logger.warning("MySQL unavailable, falling back to mock")
        items = MOCK_APPROVALS
        if status:
            items = [a for a in items if a["status"] == status]

    if user["role"] == "employee":
        items = [a for a in items if a["applicant_id"] == user["user_id"]]

    return [ApprovalItem(**a) for a in items]


@router.get("/pending", response_model=list[ApprovalItem])
async def pending_approvals(user: dict = Depends(get_current_user)):
    """待审批列表（HR 专用）。"""
    if user["role"] not in ("hr_admin",):
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    try:
        rows = await _approval_repo.list_all("pending")
        items = [_to_approval_item(r) for r in rows]
    except Exception:
        items = [a for a in MOCK_APPROVALS if a["status"] == "pending"]
    return [ApprovalItem(**a) for a in items]


@router.post("/{approval_id}/action", response_model=ApprovalItem)
async def action_approval(
    approval_id: str,
    action: ApprovalAction,
    user: dict = Depends(get_current_user),
):
    """通过/驳回审批。"""
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")

    new_status = "approved" if action.action == "approve" else "rejected"

    applicant_id = None
    approval_title = ""

    try:
        ok = await _approval_repo.update_status(approval_id, new_status, user["user_id"], action.comment)
        if ok:
            row = await _approval_repo.get_by_id(approval_id)
            applicant_id = row.get("applicant_id") if row else None
            approval_title = row.get("title", "") if row else ""
            result = ApprovalItem(**_to_approval_item(row))
            if applicant_id:
                await _notify_applicant(applicant_id, approval_title, new_status, action.comment)
            return result
    except Exception:
        logger.warning("MySQL unavailable for approval action")

    for a in MOCK_APPROVALS:
        if a["id"] == approval_id:
            if a["status"] != "pending":
                raise HTTPException(status_code=400, detail="该审批已处理")
            a["status"] = new_status
            a["updated_at"] = datetime.now(timezone.utc).isoformat()
            applicant_id = a.get("applicant_id")
            approval_title = a.get("title", "")
            logger.info("HR %s %sd approval: %s", user["display_name"], action.action, approval_id)
            if applicant_id:
                await _notify_applicant(applicant_id, approval_title, new_status, action.comment)
            return ApprovalItem(**a)

    raise HTTPException(status_code=404, detail=f"审批 {approval_id} 不存在")


@router.get("/stats")
async def approval_stats(user: dict = Depends(get_current_user)):
    """审批统计。"""
    try:
        stats = await _approval_repo.get_stats()
        return {
            "pending": stats.get("pending", 0) or 0,
            "approved": stats.get("approved", 0) or 0,
            "rejected": stats.get("rejected", 0) or 0,
            "total": stats.get("total", 0) or 0,
        }
    except Exception:
        return {
            "pending": sum(1 for a in MOCK_APPROVALS if a["status"] == "pending"),
            "approved": sum(1 for a in MOCK_APPROVALS if a["status"] == "approved"),
            "rejected": sum(1 for a in MOCK_APPROVALS if a["status"] == "rejected"),
            "total": len(MOCK_APPROVALS),
        }
