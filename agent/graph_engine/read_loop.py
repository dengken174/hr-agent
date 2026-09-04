"""LangGraph 原生读循环：bind_tools + ToolNode + tools_condition（替换 AgentExecutor）。

最小侵入适配：build_read_graph 返回编译子图（MessagesState，agent↔tools 循环）；
ReadExecutor 是适配器，把 read_factory 契约 ainvoke({"chat_history","input"}) → {"output"}
接到子图上，供 _build_read_executor 复用、图侧 execute_read 无需改动。
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition


def build_read_graph(llm, tools, system_text: str):
    """agent(bind_tools) ↔ tools(ToolNode) 循环子图。

    ainvoke({"messages": [...]}) → {"messages": [...]}；最后一条无 tool_calls 的
    AIMessage.content 即回答（extract_final_answer）。
    """
    llm_tools = llm.bind_tools(tools)

    def agent_node(state):
        msgs = [SystemMessage(content=system_text)] + list(state["messages"])
        return {"messages": [llm_tools.invoke(msgs)]}

    g = StateGraph(MessagesState)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    return g.compile()


def extract_final_answer(messages) -> str:
    """取最后一条无 tool_calls 的 AIMessage 文本；无则返回空串。"""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            return str(m.content or "")
    return ""


class ReadExecutor:
    """适配器：把 read_factory 契约接到编译好的读子图（system 由 agent_node 注入）。

    recursion_limit 对齐原 AgentExecutor 的 max_iterations（agent+tools 每轮约 2 步）。
    """

    def __init__(self, graph, recursion_limit: int | None = None):
        self._graph = graph
        self._recursion_limit = recursion_limit

    async def ainvoke(self, payload: dict) -> dict:
        chat_history = list(payload.get("chat_history") or [])
        user_input = payload.get("input", "")
        msgs = list(chat_history) + [HumanMessage(content=user_input)]
        cfg = {"recursion_limit": self._recursion_limit} if self._recursion_limit else None
        res = await self._graph.ainvoke({"messages": msgs}, config=cfg)
        return {"output": extract_final_answer(res["messages"])}
