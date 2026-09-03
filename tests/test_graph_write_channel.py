"""写通道回归：collect → confirm(interrupt) → validate(归属) → execute_write。

修正自 plan Task 7 草稿测试（P6）：
- forced-abandon 单次 invoke 到不了 abandon（cap 需跨多轮累计），改为同线程循环 cap+2 次。
- confirm_timeout 不设 1s（会造成 happy-path 偶发误 cancel），默认 86400；
  超时语义用 confirm_timeout=-1 单独确定性覆盖。
"""
from langchain_core.tools import BaseTool
from langgraph.types import Command

from agent.intent import IntentResult
from agent.graph_engine.graph import make_graph


def run(coro):
    import asyncio
    return asyncio.run(coro)


def _make_leave(log):
    class DummyLeave(BaseTool):
        name: str = "feishu_submit_leave_request"
        description: str = "提交请假申请"

        def _run(self, **kw):
            return "已提交"

        async def _arun(self, **kw):
            log.append(kw)
            return "已提交"
    return DummyLeave()


def _deps(log, extract_leave=True, collect_max_rounds=5):
    leave = _make_leave(log)

    async def classify(text):
        return IntentResult(intent="start_operation", entity={"type": "leave"})

    async def extract(text, slots_def, excerpt=None):
        if extract_leave and "8月18" in text:
            return {"leave_type": "年假", "start_date": "8月18", "end_date": "8月19"}
        return {}

    async def llm(msgs):
        return "hi"

    class E:
        async def ainvoke(self, inp):
            return {"output": "read", "chat_history": [], "agent_scratchpad": []}

    return {"classify": classify, "extract_slots": extract, "llm_invoke": llm,
            "read_factory": lambda intent, entity, scope, chat_history, user_input="": E(),
            "all_tools": [leave],
            "write_tool": lambda name: leave if name == "feishu_submit_leave_request" else None,
            "audit": None, "archive": None,
            "collect_max_rounds": collect_max_rounds}


def _graph(deps):
    return make_graph(deps, {"user_id": 7, "user_role": "employee", "session_id": "s"})


def test_leave_full_confirm_then_resume_executes():
    log = []
    g = _graph(_deps(log))
    cfg = {"configurable": {"thread_id": "s"}}
    out1 = run(g.ainvoke({"user_input": "请年假 8月18 到 8月19"}, cfg))
    assert out1["final_node"] == "confirm"
    assert out1["pending_id"] and "确认" in out1["reply"]
    out2 = run(g.ainvoke(Command(resume={"pending_id": out1["pending_id"], "action": "confirm"}), cfg))
    assert log and out2["final_node"] == "execute_write"
    assert out2["reply"] == "已提交"


def test_leave_cancel_no_execute():
    log = []
    g = _graph(_deps(log))
    cfg = {"configurable": {"thread_id": "s"}}
    out1 = run(g.ainvoke({"user_input": "请年假 8月18 到 8月19"}, cfg))
    out2 = run(g.ainvoke(Command(resume={"pending_id": out1["pending_id"], "action": "cancel"}), cfg))
    assert not log and out2["final_node"] == "cancel"


def test_leave_authorize_injects_self_employee_id():
    log = []
    g = _graph(_deps(log))
    cfg = {"configurable": {"thread_id": "s"}}
    out1 = run(g.ainvoke({"user_input": "请年假 8月18 到 8月19"}, cfg))
    out2 = run(g.ainvoke(Command(resume={"pending_id": out1["pending_id"], "action": "confirm"}), cfg))
    assert out2["final_node"] == "execute_write"
    assert log[0]["employee_id"] == "7"


def test_collect_missing_rounds_then_forced_abandon():
    log = []
    g = _graph(_deps(log, extract_leave=False, collect_max_rounds=2))
    cfg = {"configurable": {"thread_id": "s"}}
    final = None
    for _ in range(4):  # 同线程循环：第 3 次 entry rounds==cap → abandon
        out = run(g.ainvoke({"user_input": "帮我请假"}, cfg))
        if out["final_node"] == "abandon":
            final = out
            break
    assert final and final["final_node"] == "abandon"
    assert "放弃" in final["reply"]


def test_confirm_timeout_auto_cancels():
    log = []
    deps = _deps(log)
    deps["confirm_timeout"] = -1  # 任何 elapsed 都超时 → 确定性 cancel
    g = _graph(deps)
    cfg = {"configurable": {"thread_id": "s"}}
    out1 = run(g.ainvoke({"user_input": "请年假 8月18 到 8月19"}, cfg))
    out2 = run(g.ainvoke(Command(resume={"pending_id": out1["pending_id"], "action": "confirm"}), cfg))
    assert not log and out2["final_node"] == "cancel"
