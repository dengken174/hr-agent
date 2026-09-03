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
# 员工可为本人发起的写操作 → 各自的身份参数（employee/applicant==self 或 hr_admin）；
# 区别于审批 approve/reject（仅 hr_admin）。身份参数随工具 schema 变化，非统一 employee_id。
IDENTITY_PARAMS = {"employee_id", "user_id", "applicant_id", "operator_id", "assignee_id"}
_WRITE_SELF_BOUND_IDENT = {
    "feishu_submit_leave_request": "employee_id",
    "approval_start_approval": "applicant_id",
}


def _args_schema(tool) -> tuple[dict, set]:
    """归一化工具参数 schema：兼容 MCP envelope({properties,required}) 与 langchain flat({name:spec}) 两种形状。"""
    raw = getattr(tool, "args", None) or {}
    if isinstance(raw, dict) and "properties" in raw:
        props = raw.get("properties") or {}
        required = set(raw.get("required") or [])
    else:
        props = raw
        required = {k for k, v in props.items() if v.get("required")}
    return props, required


def bind_identity_params(tool, args: dict, user_id: int, role: str) -> dict:
    """把写调用的身份参数绑定到调用者本人（防冒充）。

    schema 驱动：只绑工具声明且必填/已给的身份参数；员工强制本人，hr_admin 保留已给值仅补缺省。
    无 schema 身份声明的工具（测试桩/未知）沿用 legacy employee_id 基线，避免破坏既有行为。
    """
    props, required = _args_schema(tool)
    idents = [k for k in props if k in IDENTITY_PARAMS and (k in args or k in required)]
    if not idents:
        idents = ["employee_id"]
    for p in idents:
        if role != "hr_admin" or p not in args:
            args[p] = str(user_id)
    return args


def derive_slots_defs(tool) -> list:
    """从 BaseTool.args(JSON schema) 派生槽位定义；description 作 ask。"""
    schema = getattr(tool, "args", {}) or {}
    props = schema.get("properties", {})
    required = set(schema.get("required", []) or [])
    defs = []
    for key, meta in props.items():
        if key in IDENTITY_PARAMS:
            continue  # 身份由系统注入（applicant_id/operator_id 等随工具而异）
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
    # 员工可为本人发起的操作：身份参数 == self（leave=employee_id, 发起审批=applicant_id）或 hr_admin 代行使
    if tool_name in _WRITE_SELF_BOUND_IDENT:
        key = _WRITE_SELF_BOUND_IDENT[tool_name]
        return scope.role == "hr_admin" or int(args.get(key, 0)) == scope.user_id
    if tool_name in _WRITE_APPROVAL:
        return scope.role in {"hr_admin"}
    return True
