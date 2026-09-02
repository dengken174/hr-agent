"""LangGraph 编排图：意图 → (拒答 | 闲聊 | 读)。写通道节点由后续任务并入。"""
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from agent.intent import should_refuse
from agent.graph_engine.state import HRGraphState
from agent.graph_engine.budget import assert_budget
from agent.graph_engine.write_slots import resolve_write_tool

_OUT_OF_SCOPE_FALLBACK = "抱歉，这个问题超出我的职责范围，请咨询 HR BP 或换一种说法。"


def make_graph(deps, identity):
    g = StateGraph(HRGraphState)

    def _tick(state, llm=False):
        d = {"steps": (state.get("steps") or 0) + 1}
        if llm:
            d["llm_calls"] = (state.get("llm_calls") or 0) + 1
        return d

    def _gate(state):
        assert_budget(state)

    async def intent(state):
        d = _tick(state, llm=True)  # classify 是 LLM 调用
        ir = await deps["classify"](state["user_input"])
        avail = {t.name for t in deps["all_tools"]}
        tool = resolve_write_tool(ir.intent, ir.entity, avail)
        d.update({
            "intent": ir.intent,
            "entity": dict(ir.entity or {}),
            "_write_tool": tool,
            "messages": [HumanMessage(content=state["user_input"])],
        })
        if should_refuse(ir):
            d["final_node"] = "refuse"
        elif ir.intent == "general_chat" or (tool is None and not ir.entity):
            d["final_node"] = "general"
        return d

    async def refuse(state):
        text = deps.get("out_of_scope_reply") or _OUT_OF_SCOPE_FALLBACK
        return {"reply": text, "final_node": "refuse"}

    async def general(state):
        text = await deps["llm_invoke"](state["user_input"])
        return {**_tick(state, llm=True), "reply": text, "final_node": "general",
                "messages": [AIMessage(content=text)]}

    async def execute_read(state):
        _gate(state)
        from db.scope import DataScope
        scope = DataScope(user_id=identity["user_id"], role=identity["user_role"])
        chat_hist = list(state["messages"][:-1])
        ex = deps["read_factory"](state["intent"], state["entity"], scope, chat_hist)
        res = await ex.ainvoke({"chat_history": chat_hist, "input": state["user_input"]})
        text = str(res.get("output", ""))
        return {**_tick(state), "reply": text, "final_node": "execute_read",
                "messages": [AIMessage(content=text)]}

    def route_after_intent(state):
        fn = state.get("final_node")
        if fn in ("refuse", "general"):
            return fn
        return "read"

    g.add_node("intent", intent)
    g.add_node("refuse", refuse)
    g.add_node("general", general)
    g.add_node("execute_read", execute_read)
    g.add_edge(START, "intent")
    g.add_conditional_edges("intent", route_after_intent,
                            {"refuse": "refuse", "general": "general", "read": "execute_read"})
    g.add_edge("refuse", END)
    g.add_edge("general", END)
    g.add_edge("execute_read", END)
    checkpointer = deps.get("checkpointer") or MemorySaver()
    return g.compile(checkpointer=checkpointer)
