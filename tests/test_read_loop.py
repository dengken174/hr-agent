"""read_loop 单测：bind_tools+ToolNode 读循环（替换 AgentExecutor 的最小侵入方案）。

全部 fake：ScriptedLLM（本地脚本化，不发任何网络请求）+ 假工具。测试 _arun 被
ToolNode 调用是验证 scope guard（executor 包 _arun）兼容性的前提。
"""
import asyncio

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from agent.graph_engine.read_loop import (
    ReadExecutor,
    build_read_graph,
    extract_final_answer,
)


def run(coro):
    return asyncio.run(coro)


def make_echo(log):
    class EchoTool(BaseTool):
        name: str = "echo_tool"
        description: str = "回显查询 q"

        def _run(self, q: str) -> str:
            return f"echo:{q}"

        async def _arun(self, q: str) -> str:
            log.append(q)
            return f"echo:{q}"

    return EchoTool()


class ScriptedLLM:
    """本地脚本化模型：首轮看到无工具结果 → 决定调 echo_tool；看到 ToolMessage → 给最终答案。"""

    def __init__(self):
        self.seen: list = []

    def bind_tools(self, tools):
        self.tools = list(tools)
        return self

    def invoke(self, messages):
        self.seen.append(messages)
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="final-answer")
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "echo_tool", "args": {"q": "hi"}, "id": "call-1", "type": "tool_call"}
            ],
        )


def test_agent_tools_cycle_executes_then_answers():
    """agent 首轮 tool_call → tools 执行 echo_tool → agent 看到结果给最终答案。"""
    log = []
    llm = ScriptedLLM()
    graph = build_read_graph(llm, [make_echo(log)], "system-prompt")

    out = run(graph.ainvoke({"messages": [HumanMessage(content="查一下")]}))

    last = out["messages"][-1]
    assert isinstance(last, AIMessage) and not last.tool_calls
    assert last.content == "final-answer"
    assert log == ["hi"]  # tools 节点真的执行了 echo_tool


def test_direct_answer_without_tools():
    """agent 直接给答案（无 tool_call）时收尾，不触发 tools 节点。"""
    class DirectLLM(ScriptedLLM):
        def invoke(self, messages):
            self.seen.append(messages)
            return AIMessage(content="直接答")

    log = []
    graph = build_read_graph(DirectLLM(), [make_echo(log)], "system-prompt")
    out = run(graph.ainvoke({"messages": [HumanMessage(content="你好")]}))
    assert out["messages"][-1].content == "直接答"
    assert log == []


def test_tools_node_calls_async_arun():
    """guard 兼容前提：ToolNode 走 async _arun（executor 的 scope guard 包的是 _arun）。"""
    log = []
    llm = ScriptedLLM()
    graph = build_read_graph(llm, [make_echo(log)], "system-prompt")
    run(graph.ainvoke({"messages": [HumanMessage(content="查")]}))
    assert log == ["hi"]


def test_system_message_prepended_to_agent_input():
    """agent 收到的首条消息是 SystemMessage(system_text)（替换原 ChatPromptTemplate 的 system）。"""
    llm = ScriptedLLM()
    graph = build_read_graph(llm, [make_echo([])], "HR-SYSTEM-TEXT")
    run(graph.ainvoke({"messages": [HumanMessage(content="查")]}))
    first = llm.seen[0][0]
    assert isinstance(first, SystemMessage)
    assert first.content == "HR-SYSTEM-TEXT"


def test_extract_final_answer_skips_tool_calls():
    msgs = [
        AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]),
        ToolMessage(content="r", tool_call_id="c1"),
        AIMessage(content="最终"),
    ]
    assert extract_final_answer(msgs) == "最终"


def test_extract_final_answer_empty_when_no_answer():
    assert extract_final_answer([]) == ""
    assert extract_final_answer([HumanMessage(content="hi")]) == ""


def test_read_executor_contract_matches_read_factory():
    """最小侵入契约：ainvoke({"chat_history", "input"}) → {"output"}，read_factory 复用不变。"""
    log = []
    llm = ScriptedLLM()
    graph = build_read_graph(llm, [make_echo(log)], "system-prompt")
    ex = ReadExecutor(graph)

    history = [HumanMessage(content="上轮问题"), AIMessage(content="上轮回答")]
    res = run(ex.ainvoke({"chat_history": history, "input": "当前问题"}))

    assert res["output"] == "final-answer"
    assert log == ["hi"]
