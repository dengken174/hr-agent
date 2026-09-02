from langchain_core.messages import HumanMessage, AIMessage
from agent.graph_engine.state import (start_new_turn, bump, earliest_ts,
                                      add_messages_window, HRGraphState)

def test_window_keeps_latest():
    msgs = [HumanMessage(content=f"u{i}") for i in range(25)]
    out = add_messages_window([], msgs)
    assert len(out) <= 20

def test_window_concat_and_dedupe_append():
    a = [HumanMessage(content="h1")]
    b = [AIMessage(content="a1")]
    out = add_messages_window(a, b)
    assert [m.content for m in out] == ["h1", "a1"]

def test_start_new_turn_resets_terminal_only():
    state = dict(HRGraphState(user_input="x", reply="old", final_node="refuse",
                              collecting=True, collect_tool="leave", slots={"a": 1},
                              steps=3, llm_calls=2))
    nxt = start_new_turn(state)
    assert nxt["reply"] == "" and nxt["final_node"] == ""
    assert nxt["collecting"] is True and nxt["collect_tool"] == "leave"

def test_earliest_ts_and_bump():
    assert earliest_ts(None, 5.0) == 5.0
    assert earliest_ts(3.0, 5.0) == 3.0
    assert bump(1) == 2
