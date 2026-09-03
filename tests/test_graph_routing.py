from langchain_core.tools import BaseTool
from agent.intent import IntentResult
from agent.graph_engine.graph import make_graph


def run(coro):
    import asyncio
    return asyncio.run(coro)


class DummyRead(BaseTool):
    name: str = "hris_get_my_salary"
    description: str = "d"

    def _run(self, **kw):
        return "salary:10000"

    async def _arun(self, **kw):
        return "salary:10000"


def _deps(intent=None, entity=None, direct=False):
    async def classify(text):
        return IntentResult(intent=intent or "search_own_info",
                            entity=entity or {"type": "salary"})

    async def llm(msgs):
        return "hi"

    class E:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, inp):
            self.calls += 1
            return {"output": "read-answer", "input": inp, "chat_history": [],
                    "agent_scratchpad": []}

    return {"classify": classify, "llm_invoke": llm,
            "skill_for": lambda text: None,
            "read_factory": lambda intent, entity, scope, chat_history, user_input="": E(),
            "all_tools": [DummyRead()],
            "write_tool": lambda name: None,
            "audit": None,
            "archive": None}


def test_refuse_end():
    deps = _deps(intent="out_of_scope")
    g = make_graph(deps, {"user_id": 1, "user_role": "employee", "session_id": "s"})
    out = run(g.ainvoke({"user_input": "hi"}, {"configurable": {"thread_id": "s"}}))
    assert out["final_node"] == "refuse"


def test_general_end():
    deps = _deps(intent="general_chat", direct=True)
    g = make_graph(deps, {"user_id": 1, "user_role": "employee", "session_id": "s"})
    out = run(g.ainvoke({"user_input": "hi"}, {"configurable": {"thread_id": "s"}}))
    assert out["final_node"] == "general"


def test_read_end_writes_messages():
    deps = _deps(intent="search_own_info", entity={"type": "salary"})
    g = make_graph(deps, {"user_id": 1, "user_role": "employee", "session_id": "s"})
    out = run(g.ainvoke({"user_input": "我工资多少"}, {"configurable": {"thread_id": "s"}}))
    assert out["final_node"] == "execute_read" and out["reply"] == "read-answer"
