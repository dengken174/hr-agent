"""LangGraph 编排图：意图 → (拒答 | 闲聊 | 读 | 写通道 collect→confirm(interrupt)→validate→execute)。
读/拒/闲聊分支保留 Task 6 原样；Task 7 追加写通道节点并重接 intent 条件边。
"""
import time
import uuid

from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from agent.intent import should_refuse
from agent.graph_engine.state import HRGraphState
from agent.graph_engine.budget import assert_budget
from agent.graph_engine.write_slots import resolve_write_tool, slots_defs_for, authorize_write
from db.scope import DataScope

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

    def confirm_text(slots):
        lines = [f"{k}={v}" for k, v in slots.items()]
        return "请确认以下信息：\n" + "\n".join(lines) + "\n\n回复「确认」提交，或回复「取消」放弃。"

    async def intent(state):
        d = {"messages": [HumanMessage(content=state["user_input"])]}
        if state.get("collecting"):
            # 槽位续填：不再分类/拒答，直接回 collect 补槽（工具由 collect_tool 续上）
            d["_write_tool"] = state.get("collect_tool") or state.get("_write_tool")
            return d
        d.update(_tick(state, llm=True))
        ir = await deps["classify"](state["user_input"])
        avail = {t.name for t in deps["all_tools"]}
        tool = resolve_write_tool(ir.intent, ir.entity, avail)
        d.update({
            "intent": ir.intent,
            "entity": dict(ir.entity or {}),
            "_write_tool": tool,
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
        scope = DataScope(user_id=identity["user_id"], role=identity["user_role"])
        chat_hist = list(state["messages"][:-1])
        ex = deps["read_factory"](state["intent"], state["entity"], scope, chat_hist)
        res = await ex.ainvoke({"chat_history": chat_hist, "input": state["user_input"]})
        text = str(res.get("output", ""))
        return {**_tick(state), "reply": text, "final_node": "execute_read",
                "messages": [AIMessage(content=text)]}

    async def collect(state):
        tool_name = state.get("collect_tool") or state.get("_write_tool")
        tool = deps["write_tool"](tool_name) if tool_name else None
        defs = slots_defs_for(tool) if tool else []
        slots = dict(state.get("slots") or {})
        if defs:
            excerpt = "\n".join(str(m.content) for m in state.get("messages", [])[-4:])
            extracted = await deps["extract_slots"](state["user_input"], defs, excerpt) or {}
            for sd in defs:
                v = extracted.get(sd["key"])
                if v is not None and str(v).strip():
                    if not sd.get("enum") or str(v) in sd["enum"]:
                        slots[sd["key"]] = str(v)
        missing = [sd["key"] for sd in defs if sd.get("required") and not slots.get(sd["key"])]
        if missing:
            rounds = state.get("collect_rounds") or 0
            cap = deps.get("collect_max_rounds", 5)
            if rounds >= cap:
                return {**_tick(state),
                        "collecting": False, "collect_tool": None, "slots": {},
                        "pending_id": None, "pending_tool": None, "pending_args": None, "pending_at": None,
                        "reply": "参数多次不齐，已放弃本次操作。", "final_node": "abandon"}
            ask = next((sd["ask"] for sd in defs if sd["key"] == missing[0]), "请补充信息")
            return {**_tick(state), "collecting": True, "collect_tool": tool_name, "slots": slots,
                    "collect_rounds": (rounds) + 1,
                    "reply": ask, "final_node": "collect_ask"}
        pid = uuid.uuid4().hex
        return {**_tick(state), "collecting": False, "slots": slots,
                "pending_id": pid, "pending_tool": tool_name, "pending_args": dict(slots),
                "pending_at": time.time(),
                "reply": confirm_text(slots), "final_node": "confirm"}

    async def confirm(state):
        # 首次执行 interrupt 挂起返回控制；resume 时 interrupt 返回用户动作值后继续
        resume = interrupt({
            "pending_id": state["pending_id"],
            "tool": state["pending_tool"],
            "args": state["pending_args"],
        })
        action = (resume or {}).get("action", "cancel")
        pend_at = state.get("pending_at")
        if pend_at and time.time() - pend_at > deps.get("confirm_timeout", 86400):
            action = "cancel"
        d = _tick(state)
        if action == "cancel":
            d.update({"reply": "已取消操作。", "final_node": "cancel",
                      "pending_id": None, "pending_tool": None,
                      "pending_args": None, "pending_at": None})
            return d
        return {**d, "final_node": "validate"}

    async def validate_write(state):
        args = dict(state.get("pending_args") or {})
        args.setdefault("employee_id", str(identity["user_id"]))
        scope = DataScope(user_id=identity["user_id"], role=identity["user_role"])
        if not authorize_write(scope, state.get("pending_tool"), args):
            return {**_tick(state), "reply": "无权执行该操作。", "final_node": "denied",
                    "pending_id": None, "pending_tool": None,
                    "pending_args": None, "pending_at": None}
        return {**_tick(state), "final_node": "ok"}

    async def execute_write(state):
        _gate(state)
        tool = deps["write_tool"](state.get("pending_tool"))
        args = dict(state.get("pending_args") or {})
        args.setdefault("employee_id", str(identity["user_id"]))
        if tool is None:
            return {"reply": "该工具暂不可用，请联系 HR BP。", "final_node": "error"}
        try:
            out = await tool._arun(**args)
        except Exception as e:  # 单步失败给可读错误，不中断会话
            return {"reply": f"操作失败：{e}", "final_node": "error"}
        return {**_tick(state), "reply": str(out), "final_node": "execute_write",
                "messages": [AIMessage(content=str(out))],
                "pending_id": None, "pending_tool": None,
                "pending_args": None, "pending_at": None}

    def route_after_intent(state):
        fn = state.get("final_node")
        if fn in ("refuse", "general"):
            return fn
        if state.get("collecting") or state.get("_write_tool"):
            return "write"
        return "read"

    def route_after_collect(state):
        return state.get("final_node", "confirm")

    def route_after_confirm(state):
        return "cancel" if state.get("final_node") == "cancel" else "validate"

    def route_after_validate(state):
        return "denied" if state.get("final_node") == "denied" else "ok"

    g.add_node("intent", intent)
    g.add_node("refuse", refuse)
    g.add_node("general", general)
    g.add_node("execute_read", execute_read)
    g.add_node("collect", collect)
    g.add_node("confirm", confirm)
    g.add_node("validate_write", validate_write)
    g.add_node("execute_write", execute_write)
    g.add_edge(START, "intent")
    g.add_conditional_edges("intent", route_after_intent,
                            {"refuse": "refuse", "general": "general",
                             "write": "collect", "read": "execute_read"})
    g.add_conditional_edges("collect", route_after_collect,
                            {"confirm": "confirm", "collect_ask": END, "abandon": END})
    g.add_conditional_edges("confirm", route_after_confirm,
                            {"cancel": END, "validate": "validate_write"})
    g.add_conditional_edges("validate_write", route_after_validate,
                            {"denied": END, "ok": "execute_write"})
    g.add_edge("refuse", END)
    g.add_edge("general", END)
    g.add_edge("execute_read", END)
    g.add_edge("execute_write", END)
    checkpointer = deps.get("checkpointer") or MemorySaver()
    return g.compile(checkpointer=checkpointer)
