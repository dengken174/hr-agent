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
        description="发起新的审批申请（请假/福利/报销/证明）。用于\"帮我请假/提交报销申请/申请房补\"。创建后自动提交进入待审批状态。",
        input_schema={
            "type": "object",
            "properties": {
                "applicant_id": {"type": "integer", "description": "申请人 ID（即本人 user_id）"},
                "req_type": {"type": "string", "description": "类型: leave/benefit/reimbursement/certificate"},
                "title": {"type": "string", "description": "审批标题，如\"年假申请 — 3天\""},
                "body": {"type": "object", "description": "审批详情 JSON"},
                "assignee_id": {"type": "integer", "description": "审批人 ID（选填）"},
            },
            "required": ["applicant_id", "req_type", "title"],
        },
        handler=start_approval,
    ),
    ToolDef(
        name="query_my_approvals",
        description="查询【我发起的】审批单及进度。用于\"我提交的申请怎么样了/我的请假批了吗\"。区别于 query_pending_approvals（待我审批的）。",
        input_schema={
            "type": "object",
            "properties": {"applicant_id": {"type": "integer", "description": "申请人 ID（即本人 user_id）"}},
            "required": ["applicant_id"],
        },
        handler=query_my_approvals,
    ),
    ToolDef(
        name="query_pending_approvals",
        description="查询【待我审批】的列表。用于\"有什么需要我审批的/待办审批\"。区别于 query_my_approvals（我发起的）。仅 HR/审批人可用。",
        input_schema={
            "type": "object",
            "properties": {"assignee_id": {"type": "integer", "description": "审批人 ID（即本人 user_id）"}},
            "required": ["assignee_id"],
        },
        handler=query_pending_approvals,
    ),
    ToolDef(
        name="approve_request",
        description="审批【通过】某个申请。用于\"通过/同意/批准张三的请假申请\"。区别于 reject_request（驳回）。",
        input_schema={
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "审批单 ID"},
                "operator_id": {"type": "integer", "description": "操作人 ID（即本人 user_id）"},
                "comment": {"type": "string", "description": "审批意见（选填）"},
            },
            "required": ["request_id", "operator_id"],
        },
        handler=approve_request,
    ),
    ToolDef(
        name="reject_request",
        description="审批【驳回】某个申请（必填原因）。用于\"驳回/拒绝张三的申请\"。区别于 approve_request（通过）。",
        input_schema={
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "审批单 ID"},
                "operator_id": {"type": "integer", "description": "操作人 ID（即本人 user_id）"},
                "reason": {"type": "string", "description": "驳回原因（必填）"},
            },
            "required": ["request_id", "operator_id", "reason"],
        },
        handler=reject_request,
    ),
    ToolDef(
        name="get_approval_detail",
        description="查看审批单的完整流转记录（含审批历史日志）。用于\"这个审批单的详情/谁审批过/审批到哪一步了\"。",
        input_schema={
            "type": "object",
            "properties": {"request_id": {"type": "string", "description": "审批单 ID"}},
            "required": ["request_id"],
        },
        handler=get_approval_detail,
    ),
]
