"""Feishu Card Message Templates.

Builds Feishu Card Builder JSON for interactive card messages.
Reference: https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-components
"""

import json


# ── Color constants ────────────────────────────────────────────────────

HEADER_COLORS = {
    "pending": "blue",
    "approved": "green",
    "rejected": "red",
    "info": "blue",
}

TYPE_LABELS = {
    "leave": "请假", "benefit": "福利",
    "reimbursement": "报销", "certificate": "证明",
}


# ── Element builders ───────────────────────────────────────────────────

def _plain_text(content: str) -> dict:
    return {"tag": "plain_text", "content": content}


def _lark_md(content: str) -> dict:
    return {"tag": "lark_md", "content": content}


def _header(title: str, color: str = "blue") -> dict:
    return {"title": _plain_text(title), "template": color}


def _div_md(text: str) -> dict:
    return {"tag": "div", "text": _lark_md(text)}


def _field(is_short: bool, text: str) -> dict:
    return {"is_short": is_short, "text": _lark_md(text)}


def _button(text: str, value: dict, btn_type: str = "primary") -> dict:
    return {
        "tag": "button",
        "text": _plain_text(text),
        "type": btn_type,
        "value": value,
    }


def _action_block(actions: list[dict]) -> dict:
    return {"tag": "action", "actions": actions}


def _note(text: str) -> dict:
    return {"tag": "note", "elements": [_plain_text(text)]}


def _hr() -> dict:
    return {"tag": "hr"}


# ── Serialization helpers ──────────────────────────────────────────────

def card_to_content(card: dict) -> str:
    """JSON-stringify card dict for msg_type=interactive content field."""
    return json.dumps(card, ensure_ascii=False)


def card_to_payload(chat_id: str, card: dict) -> dict:
    """Build full POST body for sending card to a chat."""
    return {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": card_to_content(card),
    }


# ── Card templates ─────────────────────────────────────────────────────

def build_approval_notification_card(
    request_id: str,
    req_type: str,
    title: str,
    applicant_name: str,
    detail: dict | None = None,
) -> dict:
    """Sent to HR (assignee) when applicant submits an approval.

    Contains Approve/Reject buttons whose value carries
    {"approval_id": ..., "action": "approve"|"reject"}.
    """
    type_label = TYPE_LABELS.get(req_type, req_type)

    elements = [
        _div_md(f"**类型**: {type_label}"),
        _div_md(f"**标题**: {title}"),
        _div_md(f"**申请人**: {applicant_name}"),
        _hr(),
    ]

    if detail:
        for key, val in detail.items():
            elements.append(_div_md(f"**{key}**: {val}"))
        elements.append(_hr())

    elements.append(_action_block([
        _button("同意", {"approval_id": request_id, "action": "approve"}, "primary"),
        _button("驳回", {"approval_id": request_id, "action": "reject"}, "danger"),
    ]))
    elements.append(_note(f"审批单号: {request_id[:8]}"))

    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"待审批 - {title}", HEADER_COLORS["pending"]),
        "elements": elements,
    }


def build_approval_result_card(
    request_id: str,
    result_status: str,
    reason: str = "",
    request_title: str = "",
    operator_name: str = "",
) -> dict:
    """Sent to applicant when their approval is processed."""
    if result_status == "approved":
        status_text = "已通过"
        header_color = HEADER_COLORS["approved"]
        icon = ""
    else:
        status_text = "已驳回"
        header_color = HEADER_COLORS["rejected"]
        icon = ""

    title_display = request_title or f"审批单 {request_id[:8]}"
    elements = [
        _div_md(f"{icon} 您的审批申请 **{title_display}** 已被**{status_text}**。"),
    ]
    if operator_name:
        elements.append(_div_md(f"**处理人**: {operator_name}"))
    if reason:
        label = "意见" if result_status == "approved" else "原因"
        elements.append(_div_md(f"**{label}**: {reason}"))
    elements.append(_note(f"审批单号: {request_id[:8]}"))

    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"审批结果 - {title_display}", header_color),
        "elements": elements,
    }


def build_card_processed_update(
    request_id: str,
    result_status: str,
    original_title: str,
    operator_name: str,
) -> dict:
    """Card update returned in HTTP callback response.
    Replaces the notification card buttons with result display.
    """
    if result_status == "approved":
        result_text = "已同意"
        color = HEADER_COLORS["approved"]
    else:
        result_text = "已驳回"
        color = HEADER_COLORS["rejected"]

    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"{original_title} - {result_text}", color),
        "elements": [
            _div_md(f"**处理结果**: {result_text}"),
            _div_md(f"**处理人**: {operator_name}"),
            _div_md(f"**审批单号**: {request_id[:8]}"),
            _note("该审批已处理完毕。"),
        ],
    }
