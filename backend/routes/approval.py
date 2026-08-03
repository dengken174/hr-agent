"""审批管理端点。"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from backend.middleware import get_current_user
from backend.models import ApprovalAction, ApprovalItem

logger = logging.getLogger("backend.routes.approval")
router = APIRouter(prefix="/api/approvals", tags=["approvals"])

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


@router.get("", response_model=list[ApprovalItem])
async def list_approvals(
    status: str = "",
    user: dict = Depends(get_current_user),
):
    """查询审批列表，可按状态过滤。"""
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

    for a in MOCK_APPROVALS:
        if a["id"] == approval_id:
            if a["status"] != "pending":
                raise HTTPException(status_code=400, detail="该审批已处理")
            a["status"] = action.action + "d" if action.action == "approve" else action.action
            a["updated_at"] = datetime.now(timezone.utc).isoformat()
            logger.info("HR %s %sd approval: %s", user["display_name"], action.action, approval_id)
            return ApprovalItem(**a)

    raise HTTPException(status_code=404, detail=f"审批 {approval_id} 不存在")


@router.get("/stats")
async def approval_stats(user: dict = Depends(get_current_user)):
    """审批统计。"""
    pending = sum(1 for a in MOCK_APPROVALS if a["status"] == "pending")
    approved = sum(1 for a in MOCK_APPROVALS if a["status"] == "approved")
    rejected = sum(1 for a in MOCK_APPROVALS if a["status"] == "rejected")
    return {"pending": pending, "approved": approved, "rejected": rejected, "total": len(MOCK_APPROVALS)}
