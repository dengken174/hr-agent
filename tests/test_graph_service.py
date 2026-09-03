"""GraphAgent service 单测：resume 判定 / MySQL-only 归档 / run_turn 状态机 / 预算熔断。

修正自 plan Task 8 草稿测试（P8）：
- save_archive 测试断言改为复用同一 FakeBuf 实例（plan 里 FakeBuf() 每次新建，断言必然真空）。
- run_turn 需真实覆盖：fresh→suspend→confirm/cancel/新消息 三种恢复语义，用带持久 checkpointer 的 stub core。
"""
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver

from agent.intent import IntentResult
from agent.graph_engine import service as svc
from agent.graph_engine.budget import BudgetError, budget_exceeded_reply


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


class _Core:
    """stub core：真实 make_graph 跑，deps 全假 + 跨调用 MemorySaver + 归档记录。"""

    def __init__(self, raise_budget=False):
        self.saver = MemorySaver()
        self.log = []
        self.archive_calls = []
        self.raise_budget = raise_budget

    def _deps_graph(self, session_id, user_id, user_role):
        leave = _make_leave(self.log)
        budget_flag = self.raise_budget

        async def classify(text):
            if "假" in text:  # 请假/年假/请年假 均触发写通道；"假" 是测试语料里最窄的稳定特征
                return IntentResult(intent="start_operation", entity={"type": "leave"})
            return IntentResult(intent="search_own_info", entity={"type": "salary"})

        async def extract(text, slots_def, excerpt=None):
            if "8月18" in text:
                return {"leave_type": "年假", "start_date": "8月18", "end_date": "8月19"}
            return {}

        async def llm(msgs):
            return "hi"

        class E:
            async def ainvoke(self, inp):
                if budget_flag:
                    raise BudgetError("budget test")
                return {"output": "read-answer", "chat_history": [], "agent_scratchpad": []}

        return {"classify": classify, "extract_slots": extract, "llm_invoke": llm,
                "read_factory": lambda intent, entity, scope, chat_history, user_input="": E(),
                "all_tools": [leave],
                "write_tool": lambda name: leave if name == "feishu_submit_leave_request" else None,
                "checkpointer": self.saver,
                "archive": self._archive}

    async def _archive(self, session_id, user_id, user_message, reply, intent=""):
        self.archive_calls.append((user_message, reply, intent))


def test_resume_action_text_mapping():
    assert svc.resume_action("确认") == "confirm"
    assert svc.resume_action("是的") == "confirm"
    assert svc.resume_action("取消") == "cancel"
    assert svc.resume_action("随便聊聊") == "cancel"


def test_memory_archive_skips_redis():
    import agent.memory as mem

    calls = []

    class FakeMy:
        async def save_message(self, *a, **k):
            calls.append(a)

    class FakeBuf:
        def __init__(self):
            self.hit = False

        async def add_message(self, *a, **k):
            self.hit = True

    buf = FakeBuf()
    m = mem.MemoryManager.__new__(mem.MemoryManager)
    m._chat_history = FakeMy()
    m._session_buffer = buf
    run(m.save_archive("s1", 1, "u", "a", "leave"))
    assert [c[2] for c in calls] == ["user", "assistant"]
    assert buf.hit is False


def test_run_turn_fresh_read_archives_once():
    core = _Core()
    reply = run(svc.run_turn(core, "我工资多少", "s1", 7, "employee"))
    assert reply == "read-answer"
    assert len(core.archive_calls) == 1
    assert core.archive_calls[0][0] == "我工资多少"
    assert core.archive_calls[0][1] == "read-answer"


def test_run_turn_budget_error_returns_reply():
    core = _Core(raise_budget=True)
    reply = run(svc.run_turn(core, "我工资多少", "s1", 7, "employee"))
    assert reply == budget_exceeded_reply()
    assert core.archive_calls == []  # 预算熔断不入 MySQL 归档


def test_run_turn_suspend_then_confirm_executes():
    core = _Core()
    r1 = run(svc.run_turn(core, "请年假 8月18到8月19", "s1", 7, "employee"))
    assert "确认" in r1
    r2 = run(svc.run_turn(core, "确认", "s1", 7, "employee"))
    assert r2 == "已提交"
    assert core.log and core.log[0]["employee_id"] == "7"
    assert [c[0] for c in core.archive_calls] == ["请年假 8月18到8月19", "确认"]


def test_run_turn_suspend_then_cancel_word_no_execute():
    core = _Core()
    run(svc.run_turn(core, "请年假 8月18到8月19", "s1", 7, "employee"))
    r2 = run(svc.run_turn(core, "取消", "s1", 7, "employee"))
    assert r2 == "已取消操作。"
    assert core.log == []


def test_run_turn_suspend_then_new_message_drops_pending():
    core = _Core()
    run(svc.run_turn(core, "请年假 8月18到8月19", "s1", 7, "employee"))
    r2 = run(svc.run_turn(core, "我工资多少", "s1", 7, "employee"))
    assert r2 == "read-answer"
    assert core.log == []  # 待确认请假未执行
    assert core.archive_calls[-1][0] == "我工资多少"


def test_run_turn_fresh_turn_read_not_misrouted_by_stale_general_final_node():
    """回归：general/refuse 终局把 final_node 留在 thread state；fresh read turn 若在 _fresh_input
    未重置，route_after_intent 会读到上一轮 stale final_node="general"，把查询误路由去 general。"""
    core = _Core()

    async def classify(text):
        if "你好" in text:
            return IntentResult(intent="general_chat", entity={})
        return IntentResult(intent="search_own_info", entity={"type": "salary"})

    orig = core._deps_graph

    def deps_graph(session_id, user_id, user_role):
        deps = orig(session_id, user_id, user_role)
        deps["classify"] = classify
        return deps

    core._deps_graph = deps_graph
    r1 = run(svc.run_turn(core, "你好，介绍一下自己", "s1", 7, "employee"))
    assert r1 == "hi"  # general 走 llm_invoke
    r2 = run(svc.run_turn(core, "我这个月工资多少", "s1", 7, "employee"))
    assert r2 == "read-answer"  # 应走 execute_read，而非被 stale final_node 送去 general
    assert [c[0] for c in core.archive_calls] == ["你好，介绍一下自己", "我这个月工资多少"]


def test_fresh_input_resets_terminal_fields():
    inp = svc._fresh_input("hi")
    assert inp["steps"] == 0 and inp["llm_calls"] == 0 and inp["started_at"] > 0
    assert inp["final_node"] == "" and inp["reply"] == ""


def test_engine_mode_env(monkeypatch):
    from agent.graph_engine import engine_mode
    monkeypatch.setenv("AGENT_ENGINE", "legacy")
    assert engine_mode() == "legacy"
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    assert engine_mode() == "graph"


def test_import_executor_smoke():
    from agent.executor import HRAgent
    assert hasattr(HRAgent, "chat")
    assert hasattr(HRAgent, "chat_stream")
    assert hasattr(HRAgent, "_deps_graph")
    assert hasattr(HRAgent, "_build_read_executor")
