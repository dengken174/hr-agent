import time
from agent.graph_engine.checkpointer import get_checkpointer
from agent.graph_engine.budget import assert_budget, BudgetError

def test_default_memory_checkpointer():
    cp = get_checkpointer("memory")
    assert cp is not None

def test_budget_ok_and_exceeded():
    assert_budget({"steps": 1, "llm_calls": 1, "started_at": time.time()}, {"max_steps": 5, "max_llm_calls": 5, "max_seconds": 60})
    try:
        assert_budget({"steps": 99, "llm_calls": 1, "started_at": None}, {"max_steps": 5, "max_llm_calls": 5, "max_seconds": 60})
    except BudgetError:
        return
    raise AssertionError("expected BudgetError")
