from dataclasses import dataclass
from typing import Any

import mcp.types as types

from mcp_servers.approval import server_name
from mcp_servers.approval.state_machine import approval_sm


# ── Tool 实现 ──────────────────────────────────────────────────────────

async def start_approval(
    applicant_id: int, req_type: str, title: str,
    body: dict | None = None, assignee_id: int | None = None,
) -> list[types.TextContent]:
    """发起审批（请假/福利/报销/证明），进入草稿状态。调用 submit 提交。"""
    try:
        req_id = approval_sm.create(
            req_type=req_type, title=title,
            applicant_id=applicant_id, body=body, assignee_id=assignee_id,
        )
        # 自动提交为 pending
        approval_sm.submit(req_id, applicant_id)
        return [types.TextContent(type="text", text=str({
            "status": "pending", "request_id": req_id,
            "message": f"审批申请已提交：{title}",
        }))]
    except ValueError as e:
        return [types.TextContent(type="text", text=f"参数错误: {e}")]


async def query_my_approvals(applicant_id: int) -> list[types.TextContent]:
    """查询我发起的审批单及进度。"""
    items = approval_sm.list_by_applicant(applicant_id)
    return [types.TextContent(type="text", text=str(items or "暂无审批单"))]


async def query_pending_approvals(assignee_id: int) -> list[types.TextContent]:
    """查询待我审批的列表。"""
    items = approval_sm.list_pending_for_assignee(assignee_id)
    return [types.TextContent(type="text", text=str(items or "暂无待审批项"))]


async def approve_request(request_id: str, operator_id: int, comment: str = "") -> list[types.TextContent]:
    """审批通过。"""
    ok = approval_sm.approve(request_id, operator_id, comment)
    return [types.TextContent(type="text", text=str({
        "status": "approved" if ok else "failed",
        "message": "审批已通过" if ok else f"操作失败，请检查审批单状态: {request_id}",
    }))]


async def reject_request(request_id: str, operator_id: int, reason: str) -> list[types.TextContent]:
    """审批驳回（必填原因）。"""
    if not reason.strip():
        return [types.TextContent(type="text", text="驳回必须填写原因")]
    ok = approval_sm.reject(request_id, operator_id, reason)
    return [types.TextContent(type="text", text=str({
        "status": "rejected" if ok else "failed",
        "message": f"已驳回，原因: {reason}" if ok else f"操作失败: {request_id}",
    }))]


async def get_approval_detail(request_id: str) -> list[types.TextContent]:
    """查看审批单的完整流转记录。"""
    req = approval_sm.get(request_id)
    logs = approval_sm.get_logs(request_id)
    return [types.TextContent(type="text", text=str({
        "request": req, "logs": logs,
    }))]


# ── Tool 注册表 ────────────────────────────────────────────────────────

@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict
    handler: Any

    def to_mcp_tool(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            description=self.description,
            inputSchema=self.input_schema,
        )


APPROVAL_TOOLS = [
    ToolDef(
        name="start_approval",
        description="发起审批申请",
        input_schema={
            "type": "object",
            "properties": {
                "applicant_id": {"type": "integer", "description": "申请人 ID"},
                "req_type": {"type": "string", "description": "类型: leave/benefit/reimbursement/certificate"},
                "title": {"type": "string", "description": "审批标题"},
                "body": {"type": "object", "description": "审批详情 JSON"},
                "assignee_id": {"type": "integer", "description": "审批人 ID"},
            },
            "required": ["applicant_id", "req_type", "title"],
        },
        handler=start_approval,
    ),
    ToolDef(
        name="query_my_approvals",
        description="查询我发起的审批单及进度",
        input_schema={
            "type": "object",
            "properties": {"applicant_id": {"type": "integer", "description": "申请人 ID"}},
            "required": ["applicant_id"],
        },
        handler=query_my_approvals,
    ),
    ToolDef(
        name="query_pending_approvals",
        description="查询待我审批的列表",
        input_schema={
            "type": "object",
            "properties": {"assignee_id": {"type": "integer", "description": "审批人 ID"}},
            "required": ["assignee_id"],
        },
        handler=query_pending_approvals,
    ),
    ToolDef(
        name="approve_request",
        description="审批通过",
        input_schema={
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "审批单 ID"},
                "operator_id": {"type": "integer", "description": "操作人 ID"},
                "comment": {"type": "string", "description": "审批意见（选填）"},
            },
            "required": ["request_id", "operator_id"],
        },
        handler=approve_request,
    ),
    ToolDef(
        name="reject_request",
        description="审批驳回（必填原因）",
        input_schema={
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "审批单 ID"},
                "operator_id": {"type": "integer", "description": "操作人 ID"},
                "reason": {"type": "string", "description": "驳回原因"},
            },
            "required": ["request_id", "operator_id", "reason"],
        },
        handler=reject_request,
    ),
    ToolDef(
        name="get_approval_detail",
        description="查看审批单的完整流转记录",
        input_schema={
            "type": "object",
            "properties": {"request_id": {"type": "string", "description": "审批单 ID"}},
            "required": ["request_id"],
        },
        handler=get_approval_detail,
    ),
]
