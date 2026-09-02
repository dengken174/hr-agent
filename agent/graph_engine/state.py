from typing import Annotated, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

from agent.graph_engine import DEFAULTS


def add_messages_window(left: list[BaseMessage], right: list[BaseMessage]) -> list[BaseMessage]:
    merged = add_messages(left, right)
    cap = DEFAULTS["messages_max"]
    return merged[-cap:] if len(merged) > cap else merged


def bump(x: int) -> int:
    return (x or 0) + 1


def earliest_ts(a: float | None, b: float | None) -> float | None:
    vals = [v for v in (a, b) if v is not None]
    return min(vals) if vals else None


def start_new_turn(state: dict) -> dict:
    nxt = dict(state)
    nxt["reply"] = ""
    nxt["final_node"] = ""
    return nxt


class HRGraphState(TypedDict):
    user_input: str
    messages: Annotated[list[BaseMessage], add_messages_window]
    collecting: bool
    collect_tool: str | None
    slots: dict
    pending_id: str | None
    pending_tool: str | None
    pending_args: dict
    pending_at: float | None
    started_at: float | None
    llm_calls: int
    steps: int
    collect_rounds: int
    reply: str
    final_node: str
    intent: str
    entity: dict
    _write_tool: str | None
