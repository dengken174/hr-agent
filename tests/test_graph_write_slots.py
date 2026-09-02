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

def test_slots_defs_for_leave_override():
    from langchain_core.tools import BaseTool, tool
    @tool
    def leave(a: str) -> str:
        """x"""  # pragma: no cover
        return a
    leave.name = "feishu_submit_leave_request"
    defs = ws.slots_defs_for(leave)
    assert defs and defs[0]["key"] == "leave_type"
