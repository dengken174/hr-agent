import os

WRITE_TOOLS = frozenset({
    "approval_start_approval", "approval_approve_request", "approval_reject_request",
    "feishu_submit_leave_request", "feishu_create_doc", "feishu_create_calendar_event",
    "feishu_create_task", "feishu_send_feishu_mail", "feishu_write_sheet",
})

DEFAULTS = {
    "max_steps": 30, "max_llm_calls": 12, "max_seconds": 60,
    "collect_max_rounds": 5, "confirm_timeout_seconds": 86400, "messages_max": 20,
}


def engine_mode() -> str:
    return os.getenv("AGENT_ENGINE", "graph")


def checkpointer_backend() -> str:
    return os.getenv("AGENT_CHECKPOINTER", "memory")
