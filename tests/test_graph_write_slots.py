from agent.intent import IntentResult
from db.scope import DataScope
from agent.graph_engine import structured as st
from agent.graph_engine import write_slots as ws

def test_sanitize_intent_rejects_unknown():
    r = st.sanitize_intent({"intent": "hack", "entity": {"type": "x"}})
    assert r.intent == "general_chat"

def test_sanitize_intent_keeps_valid():
    r = st.sanitize_intent({"intent": "start_operation", "entity": {"type": "leave"}, "confidence": 0.9})
    assert r.intent == "start_operation" and r.entity["type"] == "leave"

def test_filter_slots_enum_whitelist():
    defs = [{"key": "leave_type", "enum": ["年假", "事假"]}, {"key": "reason", "required": False}]
    out = st.filter_slots({"leave_type": "病假", "reason": "", "start_date": "8/18"}, defs)
    assert out == {}  # 病假不在白名单、reason 空 → 全剔除；start_date 不在 defs 剔除

def test_resolve_write_tool_leave():
    avail = {"feishu_submit_leave_request", "feishu_create_doc"}
    assert ws.resolve_write_tool("start_operation", {"type": "leave"}, avail) == "feishu_submit_leave_request"

def test_resolve_write_tool_approve():
    avail = {"approval_approve_request", "approval_query_my_approvals"}
    assert ws.resolve_write_tool("approval_action", {"type": "approve"}, avail) == "approval_approve_request"
    assert ws.resolve_write_tool("approval_action", {"type": "query_my"}, avail) is None

def test_authorize_write_leave_binds_self():
    emp = DataScope(user_id=7, role="employee")
    assert ws.authorize_write(emp, "feishu_submit_leave_request", {"employee_id": "7"}) is True
    assert ws.authorize_write(emp, "feishu_submit_leave_request", {"employee_id": "8"}) is False
    hr = DataScope(user_id=1, role="hr_admin")
    assert ws.authorize_write(hr, "feishu_submit_leave_request", {"employee_id": "8"}) is True

def test_authorize_write_start_approval_self_or_hr_admin():
    emp = DataScope(user_id=7, role="employee")
    # 发起审批：员工可为自己发起（对齐 legacy db.scope.authorize 全放行），身份字段是 applicant_id
    assert ws.authorize_write(emp, "approval_start_approval", {"applicant_id": 7}) is True
    assert ws.authorize_write(emp, "approval_start_approval", {"applicant_id": 8}) is False
    hr = DataScope(user_id=1, role="hr_admin")
    assert ws.authorize_write(hr, "approval_start_approval", {"applicant_id": 8}) is True
    # approve/reject 仍限 hr_admin（golden case 10 契约）
    assert ws.authorize_write(emp, "approval_approve_request", {"request_id": "A1"}) is False
    assert ws.authorize_write(hr, "approval_approve_request", {"request_id": "A1"}) is True


def _tool(args):
    from types import SimpleNamespace
    return SimpleNamespace(args=args)


def test_bind_identity_leave_binds_employee_id_not_applicant():
    args = {"reason": "探亲"}
    ws.bind_identity_params(_tool({"properties": {"reason": {}, "employee_id": {}},
                                   "required": ["employee_id"]}), args, user_id=7, role="employee")
    assert args["employee_id"] == "7" and "applicant_id" not in args


def test_bind_identity_start_approval_binds_applicant_not_employee():
    # 可选 assignee_id 未给时不注入；不注入 employee_id（该工具无此参数，避免多余 kwarg 报错）
    args = {"req_type": "请假", "title": "年假"}
    ws.bind_identity_params(_tool({"properties": {"applicant_id": {}, "req_type": {},
                                                  "title": {}, "assignee_id": {}},
                                   "required": ["applicant_id", "req_type", "title"]}),
                            args, user_id=7, role="employee")
    assert args["applicant_id"] == "7"
    assert "employee_id" not in args and "assignee_id" not in args


def test_bind_identity_unknown_tool_falls_back_employee_id():
    args = {}
    ws.bind_identity_params(_tool({"properties": {}}), args, user_id=7, role="employee")
    assert args["employee_id"] == "7"


def test_derive_slots_defs_skips_identity_params():
    tool = _tool({"properties": {
        "applicant_id": {"description": "申请人 ID（即本人 user_id）", "type": "integer"},
        "req_type": {"description": "申请类型", "type": "string"},
        "title": {"description": "标题", "type": "string"},
        "assignee_id": {"description": "审批人 ID", "type": "integer"},
    }, "required": ["applicant_id", "req_type", "title"]})
    defs = ws.derive_slots_defs(tool)
    keys = [d["key"] for d in defs]
    assert "applicant_id" not in keys and "assignee_id" not in keys
    assert keys == ["req_type", "title"]

def test_slots_defs_for_leave_override():
    from langchain_core.tools import BaseTool, tool
    @tool
    def leave(a: str) -> str:
        """x"""  # pragma: no cover
        return a
    leave.name = "feishu_submit_leave_request"
    defs = ws.slots_defs_for(leave)
    assert defs and defs[0]["key"] == "leave_type"
