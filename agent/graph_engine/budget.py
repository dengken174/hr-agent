import time
from agent.graph_engine import DEFAULTS


class BudgetError(RuntimeError):
    pass


def assert_budget(state: dict, cfg: dict | None = None) -> None:
    cfg = cfg or DEFAULTS
    started = state.get("started_at")
    if (state.get("steps") or 0) > cfg["max_steps"]:
        raise BudgetError("steps exceeded")
    if (state.get("llm_calls") or 0) > cfg["max_llm_calls"]:
        raise BudgetError("llm calls exceeded")
    if started and time.time() - started > cfg["max_seconds"]:
        raise BudgetError("time exceeded")


def budget_exceeded_reply() -> str:
    return "本轮处理步骤过多或耗时超限，已安全停止。请换一种说法重试。"
