"""GraphAgent 服务入口：单轮 run_turn（挂起判定 + MySQL 归档 + 预算护栏）。

core 需提供 `_deps_graph(session_id, user_id, user_role) -> dict`，deps 内：
- graph 节点运行所需 classify/extract_slots/write_tool/all_tools/llm_invoke/read_factory
- `checkpointer`（跨调用持久，thread_id=session_id）
- `archive`：`(session_id, user_id, user_message, assistant_message, intent) -> coroutine`（仅 MySQL）
"""
import time

from langgraph.types import Command

from agent.graph_engine.graph import make_graph
from agent.graph_engine.budget import BudgetError, budget_exceeded_reply

_CONFIRM_WORDS = {"确认", "确定", "好的", "可以", "同意", "是的", "confirm", "yes", "ok", "y", "是"}
_CANCEL_WORDS = {"取消", "不要", "算了", "放弃", "拒绝", "cancel", "no", "n"}


def resume_action(text: str) -> str:
    """文本→确认动作宽映射：命中确认词回 confirm，其余一律 cancel。
    是否「明确取消」由 _explicit_cancel 判定，避免把新消息当取消执行。"""
    return "confirm" if text.strip() in _CONFIRM_WORDS else "cancel"


def _explicit_cancel(text: str) -> bool:
    return text.strip() in _CANCEL_WORDS


def is_suspended(g, cfg) -> bool:
    snap = g.get_state(cfg)
    return bool(snap and snap.next and snap.next[0] == "confirm")


def _fresh_input(user_message: str) -> dict:
    # 预算按请求而非线程累计：started_at/steps/llm_calls 为普通字段，输入覆盖即重置
    return {"user_input": user_message, "started_at": time.time(), "steps": 0, "llm_calls": 0}


async def _archive(deps, session_id, user_id, user_message, reply, intent=""):
    if deps.get("archive"):
        await deps["archive"](session_id, user_id, user_message, reply, intent)


async def run_turn(core, user_message: str, session_id: str, user_id: int, user_role: str) -> str:
    deps = core._deps_graph(session_id, user_id, user_role)
    identity = {"user_id": user_id, "user_role": user_role, "session_id": session_id}
    g = make_graph(deps, identity)
    cfg = {"configurable": {"thread_id": session_id}}
    try:
        if is_suspended(g, cfg):
            if resume_action(user_message) == "confirm":
                out = await g.ainvoke(Command(resume={"action": "confirm", "pending_id": None}), cfg)
                await _archive(deps, session_id, user_id, user_message,
                               out.get("reply", ""), out.get("intent", ""))
                return out.get("reply", "")
            if _explicit_cancel(user_message):
                out = await g.ainvoke(Command(resume={"action": "cancel", "pending_id": None}), cfg)
                await _archive(deps, session_id, user_id, user_message,
                               out.get("reply", ""), out.get("intent", ""))
                return out.get("reply", "")
            # 挂起确认期间来了新消息：先取消挂起（丢弃待确认项），再按新消息整轮处理
            await g.ainvoke(Command(resume={"action": "cancel", "pending_id": None}), cfg)
        out = await g.ainvoke(_fresh_input(user_message), cfg)
        await _archive(deps, session_id, user_id, user_message,
                       out.get("reply", ""), out.get("intent", ""))
        return out.get("reply", "")
    except BudgetError:
        # 预算熔断终态由 service 兜底，写 trace final_node=budget_exceeded（T9 executor 接线）
        return budget_exceeded_reply()
