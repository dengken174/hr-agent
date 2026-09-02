from agent.graph_engine import WRITE_TOOLS
from agent.router import ENTITY_TOOL_MAP
from agent.slot_filler import LEAVE_SLOTS
from db.scope import DataScope

WRITE_OVERRIDE_DEFS: dict[str, list] = {
    "feishu_submit_leave_request": LEAVE_SLOTS,
    "approval_approve_request": [
        {"key": "approval_id", "required": True, "ask": "请提供要审批的申请单号"},
        {"key": "comment", "required": False, "ask": "审批意见（可选）"},
    ],
    "approval_reject_request": [
        {"key": "approval_id", "required": True, "ask": "请提供要驳回的申请单号"},
        {"key": "comment", "required": False, "ask": "驳回原因（可选）"},
    ],
}

_WRITE_APPROVAL = {"approval_approve_request", "approval_reject_request", "approval_start_approval"}


def derive_slots_defs(tool) -> list:
    """从 BaseTool.args(JSON schema) 派生槽位定义；description 作 ask。"""
    schema = getattr(tool, "args", {}) or {}
    props = schema.get("properties", {})
    required = set(schema.get("required", []) or [])
    defs = []
    for key, meta in props.items():
        if key in {"employee_id", "user_id"}:
            continue  # 身份由系统注入
        defs.append({
            "key": key,
            "required": key in required,
            "ask": meta.get("description") or f"请提供 {key}",
            "enum": meta.get("enum"),
        })
    return defs


def slots_defs_for(tool) -> list:
    if tool.name in WRITE_OVERRIDE_DEFS:
        return WRITE_OVERRIDE_DEFS[tool.name]
    return derive_slots_defs(tool)


def resolve_write_tool(intent: str, entity: dict | None, available: set[str]) -> str | None:
    entity = entity or {}
    etype = entity.get("type")
    candidates: list[str] = []
    if etype:
        narrowed = (ENTITY_TOOL_MAP.get(intent) or {}).get(etype)
        if narrowed:
            candidates = narrowed
    else:
        act = entity.get("action")
        if intent == "approval_action" and act in ("approve", "reject"):
            candidates = [f"approval_{act}_request"]
        else:
            candidates = []
    for name in candidates:
        if name in WRITE_TOOLS and name in available:
            return name
    return None


def authorize_write(scope: DataScope, tool_name: str, args: dict) -> bool:
    if tool_name == "feishu_submit_leave_request":
        return int(args.get("employee_id", 0)) == scope.user_id or scope.role == "hr_admin"
    if tool_name in _WRITE_APPROVAL:
        return scope.role in {"hr_admin"}
    return True
