"""Golden 回归种子：10 条表驱动用例，锁整个 orchestration 图的 final_node 契约。

覆盖每种可达的终局 final_node：general / refuse / execute_read / collect_ask /
confirm(挂起) / denied。case 8/9 用真实多轮 thread（跨轮续存 state），其余为单次
fresh invoke。断言走真实 make_graph(deps, identity)，deps 全假（classify/extract/
read_factory/llm/write 由用例指定），仅覆盖「路由 + 终局」契约。

相对 plan 草稿（task-10-brief）的两处修正（裁决于 progress.md）：
- case 3/6 缺槽首问在 T7 语义下终局为 collect_ask（collect 缺槽分支 collect_ask→END），
  不是 plan 里写的 "collect"。
- case 10 "替他人请假 → denied" 在图内不可达：authorize_write 用
  args.setdefault("employee_id", self) 注入本人身份，LEAVE_SLOTS 无 employee_id 键，
  员工侧请假永不可能被别人伪造。员工唯一可被拒的写通道是审批（approve/reject 要求
  hr_admin）。故 case 10 以「employee + 审批通过」播种 deny 终局。
"""
import pytest
from langchain_core.tools import BaseTool
from langgraph.types import Command

from agent.intent import IntentResult
from agent.graph_engine.graph import make_graph


def run(coro):
    import asyncio
    return asyncio.run(coro)


def _tool(tool_name, log):
    class _T(BaseTool):
        name: str = tool_name
        description: str = f"{tool_name} 工具桩"

        def _run(self, **kw):
            log.append((tool_name, kw))
            return "ok"

        async def _arun(self, **kw):
            log.append((tool_name, kw))
            return "ok"
    return _T()


# 单次用例共用工具超集：请假写 + 审批写 + 审批读，未命中的工具不影响路由。
_TOOL_SET = ["feishu_submit_leave_request", "approval_approve_request",
             "approval_query_my_approvals"]


def _deps(log, *, intent, entity, confidence=1.0, extract=None, read_output="read-answer",
          tool_names=_TOOL_SET):
    tools = [_tool(n, log) for n in tool_names]

    async def classify(text):
        return IntentResult(intent=intent, entity=dict(entity or {}), confidence=confidence)

    async def extract_slots(text, slots_def, excerpt=None):
        return dict(extract(text, slots_def)) if extract else {}

    async def llm(text):
        return "hi"

    class _Read:
        async def ainvoke(self, inp):
            return {"output": read_output, "chat_history": [], "agent_scratchpad": []}

    return {
        "classify": classify,
        "extract_slots": extract_slots,
        "llm_invoke": llm,
        "read_factory": lambda intent, entity, scope, chat_history, user_input="": _Read(),
        "all_tools": tools,
        "write_tool": lambda name: next((t for t in tools if t.name == name), None),
    }


def _identity(role):
    return {"user_id": 7, "user_role": role, "session_id": "golden"}


def _cfg(thread):
    return {"configurable": {"thread_id": thread}}


# (thread, user_input, intent, entity, confidence, expect_final_node, reply_hint|None)
_SINGLE = [
    pytest.param("g1", "你好，介绍一下自己", "general_chat", {}, 1.0, "general", None,
                 id="1-闲聊问候→general"),
    pytest.param("g2", "张三的工资是多少", "search_own_info", {"type": "salary"}, 0.3, "refuse", None,
                 id="2-越权低置信→refuse"),
    pytest.param("g3", "我要请年假", "start_operation", {"type": "leave"}, 1.0, "collect_ask",
                 "请假类型", id="3-请假缺起止日→collect_ask"),
    pytest.param("g4", "我这个月工资多少", "search_own_info", {"type": "salary"}, 1.0, "execute_read",
                 None, id="4-查自己工资→execute_read"),
    pytest.param("g5", "帮我订张机票", "out_of_scope", {}, 1.0, "refuse", None,
                 id="5-out_of_scope→refuse"),
    pytest.param("g6", "帮我通过一条审批", "approval_action", {"type": "approve"}, 1.0,
                 "collect_ask", "单号", id="6-审批通过缺单号→collect_ask"),
    pytest.param("g7", "我有哪些待审批", "approval_action", {"type": "query_my"}, 1.0,
                 "execute_read", None, id="7-审批查询(只读)→execute_read"),
]


@pytest.mark.parametrize("thread,text,intent,entity,confidence,expected,hint", _SINGLE)
def test_golden_single_turn(thread, text, intent, entity, confidence, expected, hint):
    g = make_graph(_deps([], intent=intent, entity=entity, confidence=confidence),
                   _identity("employee"))
    out = run(g.ainvoke({"user_input": text}, _cfg(thread)))
    assert out["final_node"] == expected
    if hint:
        assert hint in out.get("reply", "")


def test_8_leave_two_rounds_carries_slots():
    log = []

    def extract(text, slots_def, excerpt=None):
        if "8月18" in text:
            return {"leave_type": "年假", "start_date": "8月18", "end_date": "8月19"}
        return {}

    g = make_graph(_deps(log, intent="start_operation", entity={"type": "leave"},
                         extract=extract), _identity("employee"))
    cfg = _cfg("g8")
    out1 = run(g.ainvoke({"user_input": "我要请年假"}, cfg))
    assert out1["final_node"] == "collect_ask"
    out2 = run(g.ainvoke({"user_input": "年假，8月18到8月19"}, cfg))
    assert out2["final_node"] == "confirm"
    slots = out2.get("slots") or {}
    assert slots.get("start_date") == "8月18" and slots.get("end_date") == "8月19"
    assert not log  # 仅到确认挂起，未执行写工具


def test_9_confirm_word_without_pending_is_classified():
    log = []
    g = make_graph(_deps(log, intent="general_chat", entity={}), _identity("employee"))
    cfg = _cfg("g9")
    out1 = run(g.ainvoke({"user_input": "你好"}, cfg))
    assert out1["final_node"] == "general"
    # 无挂起确认时，"确认" 是普通输入，走 classify（此处假 classify → general）
    out2 = run(g.ainvoke({"user_input": "确认"}, cfg))
    assert out2["final_node"] == "general"
    assert not out2.get("pending_id")


def test_10_write_privilege_denied_employee_approval():
    log = []

    def extract(text, slots_def, excerpt=None):
        return {"approval_id": "A-2026-0001"}

    g = make_graph(_deps(log, intent="approval_action", entity={"type": "approve"},
                         extract=extract), _identity("employee"))
    cfg = _cfg("g10")
    out1 = run(g.ainvoke({"user_input": "帮我审批通过 A-2026-0001"}, cfg))
    assert out1["final_node"] == "confirm"
    assert out1.get("pending_id")
    out2 = run(g.ainvoke(
        Command(resume={"pending_id": out1["pending_id"], "action": "confirm"}), cfg))
    assert out2["final_node"] == "denied"
    assert "无权" in out2.get("reply", "")
    assert not log  # 权限拒绝在 execute_write 之前，写工具未执行
