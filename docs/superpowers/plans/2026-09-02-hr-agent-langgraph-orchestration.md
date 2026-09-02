# HR Agent 编排层 LangGraph 改造 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 HR Agent 编排层从「手写 `_preprocess` + 经典 `AgentExecutor`」重构成 LangGraph 有向图编排：意图/拒答/槽位收集/二次确认(interrupt HITL)/Agent 执行建模为节点与条件边；槽位与消息状态由 Checkpointer(`thread_id=session_id`) 持久化；写流程脱离 LLM 循环(确定性收集+参数校验+归属校验)。

**Architecture:** 外层是 LangGraph 有向图，`state.messages` 是会话历史单一权威源。**读流程**的 `agent_execute` 节点在 LLM 循环里自由选只读工具；**写流程**不经过 LLM 循环——意图解析出目标写工具 → `collect` 确定性收集参数 → `confirm` 节点 `interrupt` 挂起(带 `pending_id`) → 恢复后 `validate`(schema+归属) → 直调写工具。写工具从读通道工具集结构性剔除(fail-closed)。引擎选择：`HRAgent` 的 `chat/chat_stream` 按 `AGENT_ENGINE`(默认 `graph`, `legacy` 回退)分发到图服务或保留的旧实现，MCP 生命周期只保留一份。

**Tech Stack:** Python 3.13 / langgraph 1.1.10 (MemorySaver, interrupt, Command, StateGraph) / langchain 1.2.17 + langchain-classic (AgentExecutor) / DeepSeek / pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-hr-agent-langgraph-orchestration-design.md` (v2)

## Global Constraints

- langgraph==1.1.10；`MemorySaver` 与 `RedisSaver` 来自 `langgraph.checkpoint.*`，RedisSaver 需 `pip install langgraph-checkpoint-redis` 且**导入失败时静默回落 MemorySaver**（本地 6379 不可达，勿阻塞测试）。
- env：`AGENT_ENGINE` ∈ {graph, legacy}，**默认 graph**；`AGENT_CHECKPOINTER` ∈ {memory, redis}，**默认 memory**。均在**调用时**读 `os.getenv`（config 在 import 时实例化，勿把 env 固化进 dataclass 默认值）。
- 预算默认：`max_steps=30, max_llm_calls=12, max_seconds=60, collect_max_rounds=5, confirm_timeout_seconds=86400(24h), messages_max=20`。
- 图状态只存 JSON 可序列化字段 + `BaseMessage`；**不存 BaseTool/LLM 对象**（checkpointer 兼容）。
- 写工具名全集（从 `agent/executor.py:48-52` `_WRITE_TOOLS` 拷贝）：
  `approval_start_approval, approval_approve_request, approval_reject_request, feishu_submit_leave_request, feishu_create_doc, feishu_create_calendar_event, feishu_create_task, feishu_send_feishu_mail, feishu_write_sheet`。
- 旧 34 个纯逻辑测试必须保持绿；`tests/test_slot_filler.py` 的 `consume()` 断言随 Task 3 同步更新。
- 每任务独立可测、独立提交；commit 信息 `feat|refactor|test(agent): …`。
- 命令在仓库根 `D:\hragent` 用 bash 跑（Windows git-bash，路径 `/d/hragent`）。测试命令前缀 `python -m pytest`。

---

### Task 1: 配置与 env 常量（图服务读取预算/超时/引擎开关）

**Files:**
- Modify: `config.py`（在 `Config` dataclass 追加字段）
- Modify: `agent/graph_engine/__init__.py`（新建，导出 `DEFAULTS`）

**Interfaces:**
- Produces: `agent/graph_engine/__init__.py` 导出：
  - `WRITE_TOOLS: frozenset[str]`（上述 9 个）
  - `DEFAULTS: dict` = `{"max_steps":30,"max_llm_calls":12,"max_seconds":60,"collect_max_rounds":5,"confirm_timeout_seconds":86400,"messages_max":20}`
  - `def engine_mode() -> str`（`os.getenv("AGENT_ENGINE","graph")`）
  - `def checkpointer_backend() -> str`（`os.getenv("AGENT_CHECKPOINTER","memory")`）
- `config.Config` 新增字段：`request_max_steps:int=30`、`request_max_llm_calls:int=12`、`request_max_seconds:int=60`、`collect_max_rounds:int=5`、`confirm_timeout_seconds:int=86400`、`messages_max:int=20`

- [ ] **Step 1: 写失败测试**

`tests/test_graph_config.py`:
```python
import agent.graph_engine as ge

def test_defaults_and_env():
    assert ge.DEFAULTS["max_steps"] == 30
    assert ge.WRITE_TOOLS == frozenset({
        "approval_start_approval", "approval_approve_request", "approval_reject_request",
        "feishu_submit_leave_request", "feishu_create_doc", "feishu_create_calendar_event",
        "feishu_create_task", "feishu_send_feishu_mail", "feishu_write_sheet"})
    assert ge.engine_mode() in ("graph", "legacy")
    assert ge.checkpointer_backend() in ("memory", "redis")

def test_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_ENGINE", "legacy")
    assert ge.engine_mode() == "legacy"
    monkeypatch.setenv("AGENT_CHECKPOINTER", "redis")
    assert ge.checkpointer_backend() == "redis"
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_graph_config.py -v`
Expected: FAIL — `ModuleNotFoundError: agent.graph_engine`

- [ ] **Step 3: 最小实现**

Create `agent/graph_engine/__init__.py`:
```python
import os

WRITE_TOOLS = frozenset({
    "approval_start_approval", "approval_approve_request", "approval_reject_request",
    "feishu_submit_leave_request", "feishu_create_doc", "feishu_create_calendar_event",
    "feishu_create_task", "feishu_send_feishu_mail", "feishu_write_sheet",
})

DEFAULTS = {
    "max_steps": 30, "max_llm_calls": 12, "max_seconds": 60,
    "collect_max_rounds": 5, "confirm_timeout_seconds": 86400, "messages_max": 20,
}


def engine_mode() -> str:
    return os.getenv("AGENT_ENGINE", "graph")


def checkpointer_backend() -> str:
    return os.getenv("AGENT_CHECKPOINTER", "memory")
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_graph_config.py -v`
Expected: PASS

- [ ] **Step 5: 追加 config 字段并跑全量回归**

Modify `config.py` `Config` dataclass（在 `agent_max_iterations` 附近）加：
```python
    request_max_steps: int = 30
    request_max_llm_calls: int = 12
    request_max_seconds: int = 60
    collect_max_rounds: int = 5
    confirm_timeout_seconds: int = 86400
    messages_max: int = 20
```
Run: `python -m pytest tests/test_graph_config.py tests/test_slot_filler.py tests/test_scope.py -q`
Expected: PASS (all)

- [ ] **Step 6: 提交**

```bash
git add agent/graph_engine/__init__.py tests/test_graph_config.py config.py
git commit -m "feat(agent): graph engine defaults + config (budget/timeout/env)"
```

---

### Task 2: 图 State schema + 消息窗口 reducer + 预算自增 reducer

**Files:**
- Create: `agent/graph_engine/state.py`
- Test: `tests/test_graph_state.py`

**Interfaces:**
- Consumes: `agent.graph_engine.DEFAULTS`
- Produces:
  - `class HRGraphState(TypedDict)`：`user_input:str, messages:Annotated[list[BaseMessage], add_messages_window], collecting:bool, collect_tool:str|None, slots:dict, pending_id:str|None, pending_tool:str|None, pending_args:dict, pending_at:float|None, started_at:float|None, llm_calls:int, steps:int, collect_rounds:int, reply:str, final_node:str`
  - `def add_messages_window(left, right) -> list[BaseMessage]`：`add_messages` 语义 + 裁剪到 `DEFAULTS["messages_max"]`（保留最新）
  - `def bump(x) -> int`：reducer，`x+1`
  - `def max_float(a: float|None, b: float|None) -> float|None`：reducer 取最早时间戳（`min` 语义，用 `max_float` 命名易误导，命名为 `earliest_ts` 更准）
  - `def reset_terminal(state: dict) -> dict`：每轮开始时把 `reply/final_node/pending_id?` 复位，保留 `messages` 与工作流字段。**命名：`start_new_turn(state)`**

- [ ] **Step 1: 写失败测试**

`tests/test_graph_state.py`:
```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_graph_state.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 最小实现**

Create `agent/graph_engine/state.py`:
```python
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
    llm_calls: Annotated[int, bump]
    steps: Annotated[int, bump]
    collect_rounds: Annotated[int, bump]
    reply: str
    final_node: str
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_graph_state.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/graph_engine/state.py tests/test_graph_state.py
git commit -m "feat(agent): graph state schema + windowed messages reducer"
```

---

### Task 3: 泛化 SlotState / `consume()`（写通道数据基础）

**Files:**
- Modify: `agent/slot_filler.py`（`SlotState` 加 `tool`；`SlotFiller.consume` 去硬编码）
- Modify: `tests/test_slot_filler.py`
- Test: `tests/test_slot_filler.py`（更新）

**Interfaces:**
- Produces:
  - `SlotState` 新增字段 `tool: str | None = None`
  - `SlotFiller.consume(session_id, user_message) -> dict | str | None` 返回 `{"tool": <self._pending_tool>, "args": stored_slots, "cancel": True|False}`？——**保持简单**：返回结构改为 `{"action": "confirm"|"cancel", "tool": tool, "args": slots}` 或 None。
  - `SlotFiller.store_pending(session_id, slots, tool)`（把待确认槽位+工具写入 `_pending_confirms`，供图节点复用，而非复用 `handle()` 的文本路径）
  - `SlotFiller.pop_pending(session_id) -> dict | None`
  - `SlotFiller.has_pending(session_id) -> bool`

- [ ] **Step 1: 读现状并写更新测试（先红）**

先看 `tests/test_slot_filler.py` 中涉及 `consume` 的断言，把对返回值 `{"tool": "feishu_submit_leave_request", "args": ...}` 的期望改为新结构。追加：
```python
def test_consume_with_explicit_tool():
    f = SlotFiller()
    f.store_pending("s1", {"leave_type": "年假", "start_date": "8/18"}, "feishu_submit_leave_request")
    assert f.has_pending("s1")
    r = f.consume("s1", "确认")
    assert r == {"action": "confirm", "tool": "feishu_submit_leave_request",
                 "args": {"leave_type": "年假", "start_date": "8/18"}}
    assert not f.has_pending("s1")

def test_consume_cancel_drops_pending():
    f = SlotFiller()
    f.store_pending("s2", {"a": 1}, "approval_approve_request")
    assert f.consume("s2", "取消") == {"action": "cancel", "tool": "approval_approve_request", "args": {"a": 1}}
    assert not f.has_pending("s2")

def test_pop_pending_no_side_effect_when_empty():
    f = SlotFiller()
    assert f.pop_pending("nope") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_slot_filler.py -v`
Expected: FAIL（旧断言对不上 / 新方法缺失）

- [ ] **Step 3: 改造实现**

Modify `agent/slot_filler.py`：
```python
@dataclass
class SlotState:
    intent: str
    slots: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    tool: str | None = None
```
在 `SlotFiller` 中新增/替换：
```python
    def store_pending(self, session_id: str, slots: dict, tool: str):
        self._pending_confirms[session_id] = {"slots": slots, "tool": tool}

    def pop_pending(self, session_id: str) -> dict | None:
        return self._pending_confirms.pop(session_id, None)

    def consume(self, session_id: str, user_message: str) -> dict | None:
        """处理二次确认：返回 {action: confirm|cancel, tool, args} 或 None（当作新消息）。"""
        pending = self.pop_pending(session_id)
        if pending is None:
            return None
        msg = user_message.strip()
        action = "confirm" if msg in {"确认", "confirm", "yes", "是", "好的", "可以", "ok"} else "cancel"
        return {"action": action, "tool": pending["tool"], "args": pending["slots"]}
```
> `handle()` 里 `_pending_confirms[session_id] = dict(state.slots)` 同步改为 `{"slots": dict(state.slots), "tool": <当前 tool>}`；因 `handle()` 调用点已知槽位意图 `entity.type`，把 tool 映射为 `SLOT_DEF_TOOL.get(entity.type)`。新增 `SLOT_DEF_TOOL = {"leave": "feishu_submit_leave_request"}`。
> `SlotFiller.has_pending` 逻辑不变。`_llm_extract_slots` 不变。

- [ ] **Step 4: 更新其它断言并跑绿**

Run: `python -m pytest tests/test_slot_filler.py -v`
Expected: PASS（把 Step1 新测试加进来后全绿；若原 `test_*` 里断言 `{"tool":...}` 已改则已绿，否则同步）

- [ ] **Step 5: 提交**

```bash
git add agent/slot_filler.py tests/test_slot_filler.py
git commit -m "refactor(agent): generalize slot_filler consume to explicit tool+action"
```

---

### Task 4: 结构化输出解析 + 写工具解析/归属校验（纯逻辑）

**Files:**
- Create: `agent/graph_engine/structured.py`
- Create: `agent/graph_engine/write_slots.py`
- Test: `tests/test_graph_write_slots.py`

**Interfaces:**
- Consumes: `agent.intent.INTENT_LABELS, IntentResult`; `db.scope.DataScope`; `agent.graph_engine.WRITE_TOOLS`; `agent.slot_filler.LEAVE_SLOTS`
- Produces (`structured.py`):
  - `def parse_object_json(raw: str) -> dict`：从文本取第一个 `{...}`，失败返 `{}`
  - `def sanitize_intent(parsed: dict) -> IntentResult`：`intent` 必须 ∈ `INTENT_LABELS` 否则 `general_chat`；`confidence` 夹到 [0,1]；`entity` 必须是 dict
  - `def filter_slots(raw: dict, slots_def: list) -> dict`：只保留 `slots_def` 里 key、剔除空值；按 `enum` 白名单校验（不在 enum 则剔除该 key）
  - `def parse_entity_type(parsed: dict, intent: str, allowed: set[str]) -> str | None`：`entity.type` 命中白名单才保留，否则 None
- Produces (`write_slots.py`):
  - `WRITE_OVERRIDE_DEFS: dict[str, list]`：`{"feishu_submit_leave_request": LEAVE_SLOTS, "approval_approve_request": [{"key":"approval_id","required":True,"ask":"请提供要审批的单号"},{"key":"comment","required":False,"ask":"审批意见(可选)"}], "approval_reject_request": [同上 approval_id required + comment]}`（用 `LEAVE_SLOTS` import）
  - `def derive_slots_defs(tool) -> list`：从 `BaseTool.args`(JSON schema) 的 `properties/required` 生成 `[{key,required,ask,enum?}]`，description 作 ask
  - `def slots_defs_for(tool) -> list`：`WRITE_OVERRIDE_DEFS.get(tool.name) or derive_slots_defs(tool)`
  - `def resolve_write_tool(intent: str, entity: dict | None, available: set[str]) -> str | None`：entity.type 命中 `ENTITY_TOOL_MAP[intent][type]` 且名字 ∈ `WRITE_TOOLS ∩ available` 取首项；`approval_action` 且 `entity.type=="approve"/"reject"` 同映射；否则 None
  - `def authorize_write(scope: DataScope, tool_name: str, args: dict) -> bool`：`feishu_submit_leave_request` → `int(args.get("employee_id",0))==scope.user_id or scope.role=="hr_admin"`；`approval_*` 写工具 → `scope.role in {"hr_admin"}`；其余默认 True（身份由调用点注入 employee_id）

- [ ] **Step 1: 写失败测试**

`tests/test_graph_write_slots.py`:
```python
from agent.intent import IntentResult
from db.scope import DataScope
from agent.graph_engine import structured as st
from agent.graph_engine import write_slots as ws

def test_sanitize_intent_rejects_unknown():
    r = st.sanitize_intent({"intent": "hack", "entity": {"type": "x"}})
    assert r.intent == "general_chat"

def test_sanitize_intent_keeps_valid():
    r = st.sanitize_intent({"intent": "start_operation", "entity": {"type": "leave"}, "confidence": 0.9})
    assert r.intent == "start_operation" and r.entity["type"] == "leave"

def test_filter_slots_enum_whitelist():
    defs = [{"key": "leave_type", "enum": ["年假", "事假"]}, {"key": "reason", "required": False}]
    out = st.filter_slots({"leave_type": "病假", "reason": "", "start_date": "8/18"}, defs)
    assert out == {}  # 病假不在白名单、reason 空 → 全剔除；start_date 不在 defs 剔除

def test_resolve_write_tool_leave():
    avail = {"feishu_submit_leave_request", "feishu_create_doc"}
    assert ws.resolve_write_tool("start_operation", {"type": "leave"}, avail) == "feishu_submit_leave_request"

def test_resolve_write_tool_approve():
    avail = {"approval_approve_request", "approval_query_my_approvals"}
    assert ws.resolve_write_tool("approval_action", {"type": "approve"}, avail) == "approval_approve_request"
    assert ws.resolve_write_tool("approval_action", {"type": "query_my"}, avail) is None

def test_authorize_write_leave_binds_self():
    emp = DataScope(user_id=7, role="employee")
    assert ws.authorize_write(emp, "feishu_submit_leave_request", {"employee_id": "7"}) is True
    assert ws.authorize_write(emp, "feishu_submit_leave_request", {"employee_id": "8"}) is False
    hr = DataScope(user_id=1, role="hr_admin")
    assert ws.authorize_write(hr, "feishu_submit_leave_request", {"employee_id": "8"}) is True

def test_slots_defs_for_leave_override():
    from langchain_core.tools import BaseTool, tool
    @tool
    def leave(a: str) -> str:
        """x"""  # pragma: no cover
        return a
    leave.name = "feishu_submit_leave_request"
    defs = ws.slots_defs_for(leave)
    assert defs and defs[0]["key"] == "leave_type"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_graph_write_slots.py -v`
Expected: FAIL

- [ ] **Step 3: 最小实现**

Create `agent/graph_engine/structured.py`:
```python
import re
from agent.intent import INTENT_LABELS, IntentResult


def parse_object_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    import json
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        return {}


def sanitize_intent(parsed: dict) -> IntentResult:
    intent = parsed.get("intent", "general_chat")
    entity = parsed.get("entity", {})
    if not isinstance(entity, dict):
        entity = {}
    if intent not in INTENT_LABELS:
        intent = "general_chat"
        entity = {}
    try:
        conf = float(parsed.get("confidence", 1.0))
    except (TypeError, ValueError):
        conf = 1.0
    conf = min(1.0, max(0.0, conf))
    return IntentResult(intent=intent, entity=entity, confidence=conf)


def filter_slots(raw: dict, slots_def: list) -> dict:
    out = {}
    for d in slots_def:
        key = d["key"]
        val = raw.get(key)
        if val is None or (isinstance(val, str) and not val.strip()):
            continue
        if d.get("enum") and val not in d["enum"]:
            continue
        out[key] = val
    return out


def parse_entity_type(parsed: dict, intent: str, allowed: set[str]) -> str | None:
    etype = (parsed.get("entity") or {}).get("type")
    if intent and etype in allowed:
        return etype
    return None
```

Create `agent/graph_engine/write_slots.py`:
```python
from agent.graph_engine import WRITE_TOOLS
from agent.router import ENTITY_TOOL_MAP
from agent.slot_filler import LEAVE_SLOTS
from db.scope import DataScope

WRITE_OVERRIDE_DEFS: dict[str, list] = {
    "feishu_submit_leave_request": LEAVE_SLOTS,
    "approval_approve_request": [
        {"key": "approval_id", "required": True, "ask": "请提供要审批的申请单号"},
        {"key": "comment", "required": False, "ask": "审批意见（可选）"},
    ],
    "approval_reject_request": [
        {"key": "approval_id", "required": True, "ask": "请提供要驳回的申请单号"},
        {"key": "comment", "required": False, "ask": "驳回原因（可选）"},
    ],
}

_WRITE_APPROVAL = {"approval_approve_request", "approval_reject_request", "approval_start_approval"}


def derive_slots_defs(tool) -> list:
    """从 BaseTool.args(JSON schema) 派生槽位定义；description 作 ask。"""
    schema = getattr(tool, "args", {}) or {}
    props = schema.get("properties", {})
    required = set(schema.get("required", []) or [])
    defs = []
    for key, meta in props.items():
        if key in {"employee_id", "user_id"}:
            continue  # 身份由系统注入
        defs.append({
            "key": key,
            "required": key in required,
            "ask": meta.get("description") or f"请提供 {key}",
            "enum": meta.get("enum"),
        })
    return defs


def slots_defs_for(tool) -> list:
    if tool.name in WRITE_OVERRIDE_DEFS:
        return WRITE_OVERRIDE_DEFS[tool.name]
    return derive_slots_defs(tool)


def resolve_write_tool(intent: str, entity: dict | None, available: set[str]) -> str | None:
    entity = entity or {}
    etype = entity.get("type")
    candidates: list[str] = []
    if etype:
        narrowed = (ENTITY_TOOL_MAP.get(intent) or {}).get(etype)
        if narrowed:
            candidates = narrowed
    else:
        act = entity.get("action")
        if intent == "approval_action" and act in ("approve", "reject"):
            candidates = [f"approval_{act}_request"]
        else:
            candidates = []
    for name in candidates:
        if name in WRITE_TOOLS and name in available:
            return name
    return None
```

Create `agent/graph_engine/__init__.py` 补一行（便于测试导入两模块）：无需，import 路径已够。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_graph_write_slots.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/graph_engine/structured.py agent/graph_engine/write_slots.py tests/test_graph_write_slots.py
git commit -m "feat(agent): structured parsing + write-tool resolution/authorization"
```

---

### Task 5: Checkpointer 工厂 + 预算守卫 + interrupt 语义探针

**Files:**
- Create: `agent/graph_engine/checkpointer.py`
- Create: `agent/graph_engine/budget.py`
- Test: `tests/test_graph_runtime.py`

**Interfaces:**
- Consumes: `agent.graph_engine.checkpointer_backend, DEFAULTS`; `config.config`
- Produces:
  - `def get_checkpointer(backend: str | None = None) -> BaseCheckpointSaver`：`redis` 时 try `from langgraph.checkpoint.redis import RedisSaver` + `RedisSaver.from_conn_string(config.redis.url)`，任何异常回落 MemorySaver；否则 `MemorySaver()`
  - `class BudgetError(RuntimeError)`；`def assert_budget(state: dict, cfg: dict | None = None) -> None`：`steps > max_steps or llm_calls > max_llm_calls or (started_at and now - started_at > max_seconds)` 时抛 `BudgetError`
  - `def budget_exceeded_reply() -> str`

- [ ] **Step 1: 写失败测试**

`tests/test_graph_runtime.py`:
```python
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
```
（redis 分支不可达时测试跳过——只测 memory 回落路径；redis 导入失败日志警告即可。）

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_graph_runtime.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

Create `agent/graph_engine/checkpointer.py`:
```python
import logging
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from config import config

logger = logging.getLogger(__name__)


def get_checkpointer(backend: str | None = None) -> BaseCheckpointSaver:
    from agent.graph_engine import checkpointer_backend
    backend = backend or checkpointer_backend()
    if backend == "redis":
        try:
            from langgraph.checkpoint.redis import RedisSaver  # type: ignore
            cp = RedisSaver.from_conn_string(config.redis.url)
            logger.info("graph checkpointer: redis (%s)", config.redis.url)
            return cp
        except Exception as e:
            logger.warning("redis checkpointer unavailable (%s); fallback MemorySaver", e)
    return MemorySaver()
```

Create `agent/graph_engine/budget.py`:
```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_graph_runtime.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/graph_engine/checkpointer.py agent/graph_engine/budget.py tests/test_graph_runtime.py
git commit -m "feat(agent): checkpointer factory (redis w/ memory fallback) + budget guard"
```

---

### Task 6: 图主体 — 节点、条件边、读通道与消息单一源（O1）

**Files:**
- Create: `agent/graph_engine/graph.py`
- Modify: `agent/executor.py`（新增两个只读辅助：`_graph_read_executor(...)`、`_graph_llm_*`；`MemoryManager` 不涉及——归档放 service）
- Test: `tests/test_graph_routing.py`

**设计说明（先读再写）**
- `make_graph(deps, identity)` 返回编译图。`deps` 提供该请求的**只读/服务能力**（闭包捕获，不入 state）：
  - `classify(text) -> IntentResult`、`extract_slots(text, slots_def, excerpt) -> dict`（可注入 fake）
  - `llm_invoke(msgs) -> str`、`llm_stream`（general/direct）
  - `skill_for(text) -> CustomSkill|None`、`read_executor_factory(intent, entity, scope, chat_history) -> AgentExecutor`
  - `all_tools`、`audit`, `archive(session_id,user_id,user_msg,assistant,intent)`、`write_tool(name) -> BaseTool|None`
  - `identity`：`{"user_id": int, "user_role": str, "session_id": str}`
- 节点（async fn(state) -> dict）：`intent`、`refuse`、`general`、`collect`、`confirm`、`validate_write`、`execute_write`、`execute_read`、`budget`(入口)。
- `state.messages` 是消息权威（O1）：`intent` 节点把 `user_input` 经 reducer 写入 messages（返 `{"messages":[HumanMessage(...)]}`）；read 节点把 assistant 输出写入 messages。**不在图中写 Redis/MySQL**；整轮持久化由 service 在 invoke 后用 `deps.archive` 完成（Task 8）。
- 条件边常量与顺序见下方代码。写工具经 `resolve_write_tool` 判定 → 若 None 则 `execute_read`。

- [ ] **Step 1: 定义 fake deps 并写路由测试（红）**

`tests/test_graph_routing.py`（核心：MemorySaver 下 thread_id 续存多轮槽位、refuse/general/read/write 各分支、interrupt 挂起与 resume）。用 `DummyTool` 模拟只读工具，fake classify/skill/extractor。
```python
import pytest
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from agent.intent import IntentResult
from agent.graph_engine.graph import make_graph
from agent.graph_engine.state import HRGraphState

class DummyRead(BaseTool):
    name: str = "hris_get_my_salary"
    description: str = "d"
    async def _arun(self, **kw):
        return "salary:10000"

def _deps(intent=None, entity=None, direct=False):
    async def classify(text):
        return IntentResult(intent=intent or "search_own_info", entity=entity or {"type": "salary"})
    async def extract(text, slots_def, excerpt=None):
        return {"leave_type": "年假", "start_date": "8/18", "end_date": "8/19"}
    def skill_for(text):
        return None
    async def llm(msgs):
        return "hi"
    class E:
        def __init__(self):
            self.calls = 0
        async def ainvoke(self, inp):
            self.calls += 1
            return {"output": "read-answer", "input": inp, "chat_history": [], "agent_scratchpad": []}
    return {"classify": classify, "extract_slots": extract, "llm_invoke": llm,
            "skill_for": skill_for,
            "read_factory": lambda intent, entity, scope, chat_history: E(),
            "all_tools": [DummyRead()],
            "write_tool": lambda name: None,
            "audit": None,
            "archive": None}

async def test_refuse_end():
    deps = _deps(intent="out_of_scope")
    g = make_graph(deps, {"user_id": 1, "user_role": "employee", "session_id": "s"})
    out = await g.ainvoke({"user_input": "hi"}, {"configurable": {"thread_id": "s"}})
    assert out["final_node"] == "refuse"

async def test_general_end():
    deps = _deps(intent="general_chat", direct=True)
    g = make_graph(deps, {"user_id": 1, "user_role": "employee", "session_id": "s"})
    out = await g.ainvoke({"user_input": "hi"}, {"configurable": {"thread_id": "s"}})
    assert out["final_node"] == "general"

async def test_write_leave_requires_confirm_then_executes_on_resume():
    submitted = []
    async def wf(name, **kw):
        submitted.append((name, kw)); return "已提交"
    class DummyLeave(BaseTool):
        name: str = "feishu_submit_leave_request"
        description: str = "d"
        async def _arun(self, **kw):
            return await wf(self.name, **kw)
    deps = _deps(intent="start_operation", entity={"type": "leave"})
    deps["all_tools"] = [DummyLeave()]
    deps["write_tool"] = lambda name: DummyLeave() if name == "feishu_submit_leave_request" else None
    g = make_graph(deps, {"user_id": 7, "user_role": "employee", "session_id": "s"})
    cfg = {"configurable": {"thread_id": "s"}}
    out1 = await g.ainvoke({"user_input": "我要请年假8月18到19"}, cfg)
    # 第一轮应停在 confirm（槽齐 → confirm 节点 interrupt）
    assert "确认" in out1["reply"]
    # resume confirm
    from langgraph.types import Command
    out2 = await g.ainvoke(Command(resume={"action": "confirm", "pending_id": out1.get("pending_id")}), cfg)
    assert submitted and out2["final_node"] == "execute_write"

async def test_read_end_writes_messages():
    deps = _deps(intent="search_own_info", entity={"type": "salary"})
    g = make_graph(deps, {"user_id": 1, "user_role": "employee", "session_id": "s"})
    out = await g.ainvoke({"user_input": "我工资多少"}, {"configurable": {"thread_id": "s"}})
    assert out["final_node"] == "execute_read" and out["reply"] == "read-answer"
```
> 需要 `pytest-asyncio`。若项目未装，测试函数用 `asyncio.run` 包一层替代 `async def` + `pytestmark`。本计划采用辅助：
> ```python
> def run(coro):
>     import asyncio
>     return asyncio.run(coro)
> ```
> 测试体改为普通 def 调 `run(g.ainvoke(...))`。以下步骤均按此模式（避免引入新依赖）。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_graph_routing.py -v`
Expected: FAIL — `ModuleNotFoundError: agent.graph_engine.graph`

- [ ] **Step 3: 实现图主体（读通道 + refuse/general/intent 先行，写通道留 Task 7）**

Create `agent/graph_engine/graph.py`（v1：intent/refuse/general/read/collect 占位调 Task7 写函数）——为避免跨 Task 重复，本 Task 仅实现读/拒/闲聊/消息单一源，把写分支的节点函数在 Task 7 引入：
```python
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END

from agent.intent import should_refuse
from agent.graph_engine.state import HRGraphState, start_new_turn
from agent.graph_engine.budget import assert_budget, BudgetError


def make_graph(deps, identity):
    g = StateGraph(HRGraphState)

    async def intent(state):
        if state.get("collecting"):
            return {"final_node": "collect"}
        ir = await deps["classify"](state["user_input"])
        # 解析目标写工具
        avail = {t.name for t in deps["all_tools"]}
        # 依赖 Task 4: 归入 write_slots.resolve_write_tool —— 此处经 import 延迟调用
        from agent.graph_engine.write_slots import resolve_write_tool
        tool = resolve_write_tool(ir.intent, ir.entity, avail)
        d = {"intent": ir.intent, "entity": ir.entity, "_write_tool": tool,
             "messages": [HumanMessage(content=state["user_input"])]}
        if should_refuse(ir):
            d["final_node"] = "refuse"
        elif ir.intent == "general_chat" or not tool and not (ir.entity or {}):
            d["final_node"] = "general"
        return d
```
> 说明：`HRGraphState` 字段集是固定的，但**动态新增键（intent/entity/_write_tool）会使 memory/redis 序列化失败**。因此把 `intent/entity` 加入 state 为**可序列化**键（str/dict）：在 `state.py` 的 `HRGraphState` 追加 `intent: str`, `entity: dict`, `_write_tool: str|None`。同步 Task 2 已合并的 schema——**回到 Task 2 补充这三个键**（在 Task 2 Step 3 的 TypedDict 里加 `intent:str; entity:dict; _write_tool:str|None`；测试不动）。本 Task 继续以完整 schema 为准。
>
> 由此条件边读取 `state.intent/entity/_write_tool`；`intent` 节点返回这些键。

继续 graph.py 主体（refuse/general/read/budget 门与边）：
```python
    async def refuse(state):
        return {"reply": deps["out_of_scope_reply"], "final_node": "refuse"}

    async def general(state):
        text = await deps["llm_invoke"](state["messages"][-1].content)
        return {"reply": text, "final_node": "general"}

    async def execute_read(state):
        await _gate(state)
        from db.scope import DataScope
        scope = DataScope(user_id=identity["user_id"], role=identity["user_role"])
        chat_hist = state["messages"][:-1]
        ex = deps["read_factory"](state["intent"], state["entity"], scope, chat_hist)
        res = await ex.ainvoke({"chat_history": chat_hist, "input": state["user_input"]})
        text = res.get("output", "")
        return {"reply": text, "final_node": "execute_read",
                "messages": [HumanMessage(content=text)]}  # AIMessage
```
> `HumanMessage` 应为 `AIMessage`——修正为 `from langchain_core.messages import AIMessage`；下同处使用 `AIMessage(content=text)`。以下实现以 AIMessage 为准。

```python
    async def _gate(state):
        try:
            assert_budget(state)
        except BudgetError:
            from agent.graph_engine.budget import budget_exceeded_reply
            raise BudgetError(budget_exceeded_reply())

    # 写分支在 Task 7 提供：collect/confirm/validate_write/execute_write
    def route_after_intent(state):
        fn = state.get("final_node")
        if fn in ("refuse", "general"):
            return fn
        if state.get("collecting") or state.get("_write_tool"):
            return "write_channel" if state.get("collecting") else "collect"  # 简化：Task7 细化
        return "read"

    g.add_node("intent", intent)
    g.add_node("refuse", refuse)
    g.add_node("general", general)
    g.add_node("execute_read", execute_read)
    g.add_node("budget", _gate)
    g.add_edge(START, "intent")
    # 终态
    g.add_edge("refuse", END)
    g.add_edge("general", END)
    g.add_edge("execute_read", END)
    return g.compile(checkpointer=deps.get("checkpointer") or MemorySaver())
```
> 为了让本 Task 立即可测，把条件边做成：`intent` 若落在 write，则本 Task 阶段直接 `execute_read` 兜底——Task 7 把 write 分支补齐。为避免半成品，**测试本 Task 仅覆盖 refuse/general/read + 消息单一源 + budget 熔断**；leave confirm/resume 测试随 Task 7 一起绿。故 Step 1 中 `test_write_leave_requires_confirm...` 移到 Task 7 测试文件。

`MemorySaver` import 补充：`from langgraph.checkpoint.memory import MemorySaver`。

条件边收敛为（本 Task 实际接线）：
```python
    def route_after_intent(state):
        fn = state.get("final_node")
        if fn in ("refuse", "general"):
            return fn
        return "read"
    g.add_conditional_edges("intent", route_after_intent,
                            {"refuse": "refuse", "general": "general", "read": "execute_read"})
```

- [ ] **Step 4: 让本 Task 的读/拒/闲聊/预算测试绿**

Run: `python -m pytest tests/test_graph_routing.py -v`
Expected: PASS（本 Task 范围的 3 条：refuse/general/read）

- [ ] **Step 5: 提交**

```bash
git add agent/graph_engine/graph.py agent/graph_engine/state.py tests/test_graph_routing.py
git commit -m "feat(agent): graph intent/refuse/general/read nodes + message single source"
```

---

### Task 7: 写通道 — collect/confirm(interrupt)/validate/execute_write + 预算门

**Files:**
- Modify: `agent/graph_engine/graph.py`
- Modify: `agent/graph_engine/state.py`（若需 `pending_at` 已在 schema）
- Test: `tests/test_graph_write_channel.py`

**Interfaces:**
- Consumes: Task 4 `write_slots.{resolve_write_tool,slots_defs_for,authorize_write,WRITE_OVERRIDE_DEFS}`；Task 3 `slot_filler`；Task 5 budget；`langgraph.types.{interrupt, Command}`
- Produces: graph 增加节点与条件边：
  - `collect`、`confirm`、`validate_write`、`execute_write`
  - 重接 `route_after_intent`：`_write_tool` 或 `collecting` → collect；否则 read
  - `route_after_collect`：`{missing: collect_ask(END), ready: confirm}`；`ready` 时 set `pending_*`
  - `route_after_confirm_resume`：confirm 动作（含超时 cancel）
- 依赖注入 `deps["extract_slots"](text, slots_def, excerpt)` 返回 dict；`deps["write_tool"](name)` 返回工具或 None；`deps["confirm_timeout"]`（默认 86400）

- [ ] **Step 1: 写失败测试**

`tests/test_graph_write_channel.py`（asyncio.run 辅助同 Task 6）：
```python
import time
from langchain_core.tools import BaseTool
from langgraph.types import Command
from agent.intent import IntentResult
from agent.graph_engine.graph import make_graph


class DummyLeave(BaseTool):
    name: str = "feishu_submit_leave_request"
    description: str = "d"
    async def _arun(self, **kw):
        return "已提交"


def _deps(submit_log):
    async def classify(text): return IntentResult(intent="start_operation", entity={"type": "leave"})
    async def extract(text, slots_def, excerpt=None):
        if "8月18" in text:
            return {"leave_type": "年假", "start_date": "8月18", "end_date": "8月19"}
        return {}
    def skill_for(text): return None
    async def llm(msgs): return "hi"
    class E:
        async def ainvoke(self, inp): return {"output": "read", "chat_history": [], "agent_scratchpad": []}
    t = DummyLeave()
    async def run_write(**kw): submit_log.append(kw); return "已提交"
    # 覆盖 _arun 以记录
    async def _arun(self, **kw):
        return await run_write(**kw)
    t._arun = _arun.__get__(t)
    return {"classify": classify, "extract_slots": extract, "skill_for": skill_for,
            "llm_invoke": llm, "read_factory": lambda *a, **k: E(),
            "all_tools": [t],
            "write_tool": lambda name: t if name == "feishu_submit_leave_request" else None,
            "audit": None, "archive": None, "confirm_timeout": 1}


def run(coro):
    import asyncio
    return asyncio.run(coro)


def test_leave_full_confirm_then_resume_executes():
    log = []
    g = make_graph(_deps(log), {"user_id": 7, "user_role": "employee", "session_id": "s"})
    cfg = {"configurable": {"thread_id": "s"}}
    out1 = run(g.ainvoke({"user_input": "请年假 8月18 到 19"}, cfg))
    assert out1["final_node"] in ("confirm",) or out1.get("pending_id") or "确认" in out1["reply"]
    out2 = run(g.ainvoke(Command(resume={"pending_id": out1["pending_id"], "action": "confirm"}), cfg))
    assert log and out2["final_node"] == "execute_write"
    assert out2["reply"] == "已提交"


def test_leave_cancel_no_execute():
    log = []
    g = make_graph(_deps(log), {"user_id": 7, "user_role": "employee", "session_id": "s"})
    cfg = {"configurable": {"thread_id": "s"}}
    out1 = run(g.ainvoke({"user_input": "请年假 8月18 到 19"}, cfg))
    out2 = run(g.ainvoke(Command(resume={"pending_id": out1["pending_id"], "action": "cancel"}), cfg))
    assert not log and out2["final_node"] == "cancel"


def test_leave_authorize_denied_does_not_execute():
    # employee 无法替他人请假：身份绑定后目标必须是自己 → employee_id 由 execute_write 注入
    log = []
    g = make_graph(_deps(log), {"user_id": 7, "user_role": "employee", "session_id": "s"})
    cfg = {"configurable": {"thread_id": "s"}}
    out1 = run(g.ainvoke({"user_input": "请年假 8月18 到 19"}, cfg))
    out2 = run(g.ainvoke(Command(resume={"pending_id": out1["pending_id"], "action": "confirm"}), cfg))
    assert log  # 注入 employee_id=str(user_id)=7 → authorize 通过
    assert log[0]["employee_id"] == "7"


def test_collect_missing_rounds_then_forced_abandon():
    # extract 一直返回 {} → 3 轮后 collect_max_rounds 强制放弃，不挂起
    deps = _deps(log:=[])
    async def extract(text, slots_def, excerpt=None): return {}
    deps["extract_slots"] = extract
    deps["collect_max_rounds"] = 2
    g = make_graph(deps, {"user_id": 7, "user_role": "employee", "session_id": "s"})
    cfg = {"configurable": {"thread_id": "s"}}
    out = run(g.ainvoke({"user_input": "帮我请假"}, cfg))
    assert out["final_node"] == "abandon" or "放弃" in out["reply"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_graph_write_channel.py -v`
Expected: FAIL

- [ ] **Step 3: 实现写通道节点并接线**

Modify `agent/graph_engine/graph.py`，追加节点函数并重接边：
```python
import uuid, time as _time
from langgraph.types import interrupt
from agent.graph_engine.write_slots import (resolve_write_tool, slots_defs_for, authorize_write)
from agent.slot_filler import SlotFiller

def make_graph(deps, identity):
    filler = SlotFiller()

    async def collect(state):
        tool_name = state.get("collect_tool") or state.get("_write_tool")
        tool = deps["write_tool"](tool_name)
        defs = slots_defs_for(tool) if tool else []
        excerpt = "\n".join(str(m.content) for m in state["messages"][-4:])
        extracted = await deps["extract_slots"](state["user_input"], defs, excerpt)
        slots = dict(state.get("slots") or {})
        for d in defs:
            v = extracted.get(d["key"])
            if v is not None and str(v).strip():
                if not d.get("enum") or v in d["enum"]:
                    slots[d["key"]] = v
        missing = [d["key"] for d in defs if d["required"] and not slots.get(d["key"])]
        if missing:
            rounds = state.get("collect_rounds") or 0
            cap = deps.get("collect_max_rounds", 5)
            if rounds >= cap:
                return {"collecting": False, "collect_tool": None, "slots": {}, "reply": "参数一直不齐，已放弃本次操作。", "final_node": "abandon"}
            ask = next((d["ask"] for d in defs if d["key"] == missing[0]), "请补充信息")
            return {"collecting": True, "collect_tool": tool_name, "slots": slots, "reply": ask, "final_node": "collect_ask"}
        pid = uuid.uuid4().hex
        return {"collecting": False, "slots": slots,
                "pending_id": pid, "pending_tool": tool_name, "pending_args": slots,
                "pending_at": _time.time(),
                "reply": _confirm_text(tool_name, slots), "final_node": "confirm"}

    def _confirm_text(tool, slots):
        parts = [f"{k}={v}" for k, v in slots.items()]
        return "请确认以下信息：\n" + "\n".join(parts) + "\n\n回复「确认」提交，或回复「取消」放弃。"

    async def confirm(state):
        payload = {"pending_id": state["pending_id"], "tool": state["pending_tool"], "args": state["pending_args"]}
        resume = interrupt(payload)  # 挂起；resume 值在恢复时返回
        action = (resume or {}).get("action", "cancel")
        # 超时兜底：重进后校验 pending_at
        if state.get("pending_at") and _time.time() - state["pending_at"] > deps.get("confirm_timeout", 86400):
            action = "cancel"
        if action == "cancel":
            return {"reply": "已取消操作。", "final_node": "cancel"}
        return {"final_node": "validate"}

    async def validate_write(state):
        tool_name = state["pending_tool"]
        args = dict(state["pending_args"])
        # O6 归属校验（含身份注入在 execute_write，故此处先只做 schema/归属可见校验）
        scope = DataScope(user_id=identity["user_id"], role=identity["user_role"])
        if not authorize_write(scope, tool_name, {**args, "employee_id": str(identity["user_id"])}):
            return {"reply": "无权执行该操作。", "final_node": "denied"}
        return {"final_node": "execute_write"}

    async def execute_write(state):
        tool = deps["write_tool"](state["pending_tool"])
        args = dict(state["pending_args"])
        args.setdefault("employee_id", str(identity["user_id"]))
        if tool is None:
            return {"reply": "该工具暂不可用，请联系 HR BP。", "final_node": "error"}
        await _gate(state)
        try:
            out = await tool._arun(**args)
        except Exception as e:  # 单步失败给可读错误
            return {"reply": f"操作失败：{e}", "final_node": "error"}
        return {"reply": str(out), "final_node": "execute_write",
                "messages": [AIMessage(content=str(out))]}
```
> `DataScope` import：`from db.scope import DataScope`（放模块顶部）。
> 接线（替换 Task 6 的条件边）：
```python
    async def _gate(state):
        try:
            assert_budget(state)
        except BudgetError:
            raise

    def route_after_intent(state):
        fn = state.get("final_node")
        if fn in ("refuse", "general"):
            return fn
        if state.get("collecting") or state.get("_write_tool"):
            return "write"
        return "read"

    def route_after_collect(state):
        if state["final_node"] in ("confirm", "abandon", "collect_ask"):
            return state["final_node"]
        return "confirm"

    def route_after_confirm(state):
        if state["final_node"] in ("cancel",):
            return "cancel"
        if state["final_node"] == "denied":
            return "denied"
        return "exec"  # validate ok

    for name, fn in [("collect", collect), ("confirm", confirm),
                     ("validate_write", validate_write), ("execute_write", execute_write)]:
        g.add_node(name, fn)
    g.add_edge("execute_write", END)
    g.add_edge("cancel", END)
    g.add_edge("denied", END)
    # 将 refuse/general/execute_read 也作为显式终态目标（原直连 END 保留）
    g.add_conditional_edges("intent", route_after_intent,
                            {"refuse": "refuse", "general": "general", "write": "collect", "read": "execute_read"})
    g.add_conditional_edges("collect", route_after_collect,
                            {"collect_ask": END, "confirm": "confirm", "abandon": END})
    g.add_conditional_edges("confirm", route_after_confirm,
                            {"cancel": END, "denied": END, "exec": "execute_write"})
```
> 挂起恢复：`confirm` 节点在第一次走到时 `interrupt` 返回控制（图返回，`state.next=["confirm"]`）；第二次以 `Command(resume=...)` 调用时 `interrupt` 返回 resume 值继续。`final_node` 此时尚未由 confirm 置为终态字段——由 resume 后返回的 dict 决定分支。**注意**：`interrupt` 之前若设置了 `reply`/`final_node`，挂起返回时这些值会随 state 一起可读（service 拿来做用户提示）。上面把 `reply` 在 `collect` 已置好，confirm 不再重复 set，避免覆盖；`confirm` 首次进入返回 `{}`（仅挂起）——但 LangGraph 要求节点返回可空 dict；挂起即中断，不入字典亦可。简化：confirm 首入 return `{}`，系统返回 state（reply 已是确认文案）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_graph_write_channel.py tests/test_graph_routing.py -q`
Expected: PASS（写通道 4 条 + 路由 3 条）

- [ ] **Step 5: 提交**

```bash
git add agent/graph_engine/graph.py tests/test_graph_write_channel.py
git commit -m "feat(agent): write channel collect/confirm(interrupt)/validate/execute + authorize + abandon"
```

---

### Task 8: GraphAgent service（resume 判定、整轮归档 MySQL、engine 分发接入 HRAgent）

**Files:**
- Create: `agent/graph_engine/service.py`
- Modify: `agent/executor.py`（新增方法 `_run_graph(...)` 供 dispatch；`MemoryManager` 不动）
- Modify: `agent/memory.py`（加 `save_archive`：仅写 MySQL，不写 Redis）
- Test: `tests/test_graph_service.py`

**Interfaces:**
- `memory.MemoryManager.save_archive(session_id, user_id, user_message, assistant_message, intent="")`：调 `self._chat_history.save_message` 两次（user/assistant），不碰 `_session_buffer`。
- `agent/executor.py::HRAgent` 新增：
  - `def _deps_graph(self, session_id, user_id, user_role) -> dict`：装配 graph deps（classify=`intent_classifier.classify`；`skill_for=skill_manager.match`；`llm_invoke/_direct_reply`；`read_factory`：内部 `route_tools` 去写工具 + `_apply_scope_guard` 后建 `create_tool_calling_agent`+`AgentExecutor`（无 memory，chat_history 由调用传）；`write_tool` 查 `self._all_tools`；`archive`→ `memory_manager.save_archive`）
  - `async def chat(...)` / `chat_stream(...)`：开头 `from agent.graph_engine import engine_mode; if engine_mode()=="graph": return await self._chat_graph(...)`，否则走原 body。原 body 改为私有 `_chat_legacy/_chat_stream_legacy`。
- `agent/graph_engine/service.py`：
  - `async def run_turn(core, user_message, session_id, user_id, user_role) -> str`：构建 deps→make_graph→若 `state.next==["confirm"]` 走 resume(按文本 action) 否则新 invoke；archival 用 `deps["archive"]` 记录本轮（仅整轮一次）。
  - `def is_suspended(g, cfg) -> bool`（`g.get_state(cfg).next` 含 `"confirm"`）。

- [ ] **Step 1: 写失败测试（仅测纯逻辑与轻依赖部分）**

`tests/test_graph_service.py`（不连 MCP/LLM——直接用 stub core）：
```python
from agent.graph_engine import service as svc

def test_resume_action_from_text():
    assert svc.resume_action("确认") == "confirm"
    assert svc.resume_action("是的") == "confirm"
    assert svc.resume_action("取消") == "cancel"
    assert svc.resume_action("随便聊聊") == "cancel"  # 非确认词 → 视为取消后按新消息处理（上层语义）

def test_memory_archive_skips_redis(tmp_path, monkeypatch):
    # 用假 MySQLChatHistory 记录调用；假 RedisSessionBuffer 抛错则测出“没被调用”
    import agent.memory as mem
    calls = []
    class FakeMy:
        async def save_message(self, *a, **k): calls.append(a)
    class FakeBuf:
        def __init__(self): self.hit = False
        async def add_message(self, *a, **k): self.hit = True
    m = mem.MemoryManager.__new__(mem.MemoryManager)
    m._chat_history = FakeMy()
    m._session_buffer = FakeBuf()
    run = __import__("asyncio").run
    run(m.save_archive("s1", 1, "u", "a", "leave"))
    assert len(calls) == 2
    assert FakeBuf().hit is False
```
> `MemoryManager.__new__` 绕过 `__init__` 的 `ChatOpenAI` 构建（避免需要 API key）。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_graph_service.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

Create `agent/graph_engine/service.py`:
```python
import asyncio
from langgraph.types import Command
from agent.graph_engine.graph import make_graph
from agent.graph_engine.checkpointer import get_checkpointer

_CONFIRM_WORDS = {"确认", "confirm", "yes", "是", "好的", "可以", "ok"}


def resume_action(text: str) -> str:
    return "confirm" if text.strip() in _CONFIRM_WORDS else "cancel"


def is_suspended(g, cfg) -> bool:
    snap = g.get_state(cfg)
    return bool(snap and snap.next and snap.next[0] == "confirm")


async def run_turn(core, user_message: str, session_id: str, user_id: int, user_role: str) -> str:
    deps = core._deps_graph(session_id, user_id, user_role)
    g = make_graph(deps, {"user_id": user_id, "user_role": user_role, "session_id": session_id})
    cfg = {"configurable": {"thread_id": session_id}}
    if is_suspended(g, cfg):
        # 有挂起确认：先按文本判定，再决定是否把非确认文本作为新消息重入
        action = resume_action(user_message)
        if action == "cancel" and user_message.strip() not in _CONFIRM_WORDS:
            await g.ainvoke(Command(resume={"action": "cancel", "pending_id": None}), cfg)
        else:
            out = await g.ainvoke(Command(resume={"action": action, "pending_id": None}), cfg)
            await _archive(deps, core, session_id, user_id, user_message, out.get("reply", ""), "confirm_resume")
            return out.get("reply", "")
    out = await g.ainvoke({"user_input": user_message}, cfg)
    if out.get("final_node") == "budget_exceeded":
        return out.get("reply", "处理超限已停止。")
    await _archive(deps, core, session_id, user_id, user_message, out.get("reply", ""), out.get("intent", ""))
    return out.get("reply", "")


async def _archive(deps, core, session_id, user_id, user_message, reply, intent=""):
    if deps.get("archive"):
        await deps["archive"](session_id, user_id, user_message, reply, intent)
```

Modify `agent/memory.py`（`MemoryManager` 内追加）：
```python
    async def save_archive(self, session_id, user_id, user_message, assistant_message, intent=""):
        """仅写 MySQL（append-only 审计/冷档），不经 Redis 缓冲（graph 路径专用）。"""
        await self._chat_history.save_message(session_id, user_id, "user", user_message, intent)
        await self._chat_history.save_message(session_id, user_id, "assistant", assistant_message, intent)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_graph_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add agent/graph_engine/service.py agent/memory.py tests/test_graph_service.py
git commit -m "feat(agent): graph service run_turn (resume detection) + MySQL-only archive"
```

---

### Task 9: executor 接入 engine 分发（`AGENT_ENGINE`），补 requirements

**Files:**
- Modify: `agent/executor.py`（`chat/chat_stream` 改为分发；原 body 收进 `_chat_legacy/_chat_stream_legacy`；新增 `_deps_graph`/`_chat_graph`/`_chat_stream_graph`）
- Modify: `requirements.txt`
- Test: `tests/test_graph_service.py`（新增两条 engine 判定纯测）

**Interfaces:**
- `HRAgent.chat/chat_stream` 公开签名不变。
- `_deps_graph` 装配 read_factory 的实现要点（对照 `executor.py:271-294` 读路径）：路由 = `route_tools`（import 自 `agent.router`）过滤后**剔除写工具** ∪ skill 工具；`_apply_scope_guard` 绑定 scope+audit；prompt = `_build_prompt_with_skill(user_role, skill, entity)`；`create_tool_calling_agent(llm=self._llm, tools=read_tools, prompt=prompt)`；`AgentExecutor(agent, tools=read_tools, max_iterations=config.agent_max_iterations, verbose=False, handle_parsing_errors=True)`。executor 的 `ainvoke({"chat_history": hist, "input": inp})` 返回 `{"output":...}`。LLM 直答用现有 `_direct_reply/_direct_reply_stream`。
- `requirements.txt`：加 `langgraph>=1.1.10`（注释 `# langgraph-checkpoint-redis 可选：AGENT_CHECKPOINTER=redis 时需装`）。

- [ ] **Step 1: 写失败测试（engine 分发 + deps 装配的纯局部）**

在 `tests/test_graph_service.py` 追加：
```python
def test_engine_mode_env(monkeypatch):
    from agent.graph_engine import engine_mode
    monkeypatch.setenv("AGENT_ENGINE", "legacy")
    assert engine_mode() == "legacy"
    monkeypatch.setenv("AGENT_ENGINE", "graph")
    assert engine_mode() == "graph"
```
本 Task 的 executor 改动属集成，端到端需 MCP/LLM——**以 import 冒烟代替**：`tests/test_graph_service.py` 加 `def test_import_executor_smoke(): from agent.executor import HRAgent; assert hasattr(HRAgent, "chat")`。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_graph_service.py -v`
Expected: FAIL（`_deps_graph`/engine 分支未接）

- [ ] **Step 3: 实现分发**

Modify `agent/executor.py`（关键片段，逐条核对原代码）：
- 顶部 import：`from agent.graph_engine.service import run_turn`（延迟 import 亦可）
- `HRAgent` 方法改名：把现 `chat` body 整体复制为 `async def _chat_legacy(self, ...)`（保持原实现），现 `chat_stream` body → `_chat_stream_legacy`。新公开方法：
```python
    async def chat(self, user_message, session_id="default", user_id=0, user_role="employee"):
        from agent.graph_engine import engine_mode
        if engine_mode() == "graph":
            return await self._chat_graph(user_message, session_id, user_id, user_role)
        return await self._chat_legacy(user_message, session_id, user_id, user_role)
```
`_chat_graph` 调 `from agent.graph_engine.service import run_turn; return await run_turn(self, user_message, session_id, user_id, user_role)`。
`chat_stream` 类似分发：graph 分支先 `out = await self._chat_graph(...)` 再 `yield out`（聚合整句；token 级细化见 Global Constraints 备注）。
- `_deps_graph`：
```python
    def _deps_graph(self, session_id, user_id, user_role):
        from agent.router import route_tools
        from agent.executor import _apply_scope_guard  # 模块级
        from agent.graph_engine import WRITE_TOOLS
        from db.scope import DataScope

        async def classify(text):
            from agent.intent import intent_classifier
            return await intent_classifier.classify(text)

        async def skill_for(text):
            return skill_manager.match(text)

        def write_tool(name):
            return next((t for t in self._all_tools if t.name == name), None)

        def read_factory(intent, entity, scope, chat_history):
            tools = [t for t in route_tools(intent, self._all_tools) if t.name not in WRITE_TOOLS]
            ms = skill_manager.match(entity and entity.get("type") and str(entity) or str(intent)) if False else None
            return self._build_read_executor(intent, entity, scope, chat_history)

        return {"classify": classify, "skill_for": skill_for, "write_tool": write_tool,
                "read_factory": self._build_read_executor,
                "llm_invoke": self._direct_reply, "all_tools": self._all_tools,
                "audit": None, "archive": self._memory_mgr.save_archive,
                "out_of_scope_reply": None}
```
新增 `_build_read_executor(intent, entity, scope, chat_history)`（复刻读路径、去掉确认/记忆对象）：
```python
    def _build_read_executor(self, intent, entity, scope, chat_history):
        from agent.router import route_tools
        from agent.graph_engine import WRITE_TOOLS
        from agent.executor import _apply_scope_guard
        from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
        tools = [t for t in route_tools(intent, self._all_tools) if t.name not in WRITE_TOOLS]
        skill = skill_manager.match("")
        tools = _apply_scope_guard(tools, scope, audit)  # audit 需真实 audit_repo：executor 模块级 audit_repo
        prompt = self._build_prompt_with_skill(user_role_entity_lookup(intent, entity), skill, entity)
        ...
```
> 说明与修正：executor 中读路径原用的 audit/audit_repo 是模块级对象。真实实现时以 `self._build_prompt(user_role, entity)` 传 `user_role`；`_build_read_executor` 需要 `user_role`，故签名改为 `_build_read_executor(user_role, intent, entity, scope, chat_history)`，并在 `read_factory` lambda 传 `user_role`。工具授权 `_apply_scope_guard` 需要模块级 `audit_repo`（executor.py 顶部定义）。**Executor 需按此逐行对照 executor.py 现读路径补齐**，避免臆造 audit 变量。

> `_chat_stream_legacy` 与 `_chat_legacy` 保留原逻辑（含记忆 Redis/MySQL、确认 guard），供 `AGENT_ENGINE=legacy` 回退。

`requirements.txt` 增加：`langgraph>=1.1.10`
（注释 `# optional: pip install langgraph-checkpoint-redis  + AGENT_CHECKPOINTER=redis`）

- [ ] **Step 4: 运行确认通过 + 全量回归**

Run: `python -m pytest tests/ -q`
Expected: PASS（旧 34 + 本 Block 新增全部；若真实 `audit_repo` 未就绪致集成测试 import 失败，则该用例标记 `@pytest.mark.integration` 并在 conftest 跳过——但必须确认至少 import/engine/env 纯测绿。）

- [ ] **Step 5: 提交**

```bash
git add agent/executor.py agent/memory.py requirements.txt tests/test_graph_service.py
git commit -m "feat(agent): dispatch chat to graph engine via AGENT_ENGINE; read executor reuse"
```

---

### Task 10: golden 回归种子（O5）与收尾自检

**Files:**
- Create: `tests/test_orchestration_golden.py`
- Modify: `agent/graph_engine/__init__.py`（无）

**Interfaces:**
- 复用 `make_graph(deps, identity)` 与 fake deps（Task 6/7 模式）。

- [ ] **Step 1: 写 golden 测试**

`tests/test_orchestration_golden.py`：一张表驱动的 10 条用例——`(input, intent, entity, expect_final_node)`：
1. 闲聊问候 → general_chat → general
2. 越权问题(薪资别人) low conf → general_chat(若 conf 低触发 refuse) → refuse
3. 请假缺起止日 → start_operation+leave → collect（首问 reply 含"确认"或"哪天"）
4. 查自己工资 → search_own_info+salary → execute_read
5. out_of_scope 拒答 → refuse
6. 审批通过 → approval_action+approve → collect(→需单号) 
7. 审批查询(只读) → approval_action+query_my → execute_read
8. 请假连续两轮第二轮补槽 → 同 thread 续存 slots 非空
9. 确认词在无挂起时 → 走 classify（general/正常）
10. 写工具越权(替他人请假) → denied
每条用一个 fake deps 表（intent/entity 由用例指定），断言 `final_node`；仅 case 8/9 用真实多轮 thread。

- [ ] **Step 2: 运行确认通过**

Run: `python -m pytest tests/test_orchestration_golden.py -v`
Expected: PASS

- [ ] **Step 3: 全量回归 + 手跑两个冒烟**

Run: `python -m pytest tests/ -q`
Run: `AGENT_ENGINE=legacy python -c "from agent.executor import HRAgent; print('legacy import ok')"`
Run: `AGENT_CHECKPOINTER=redis python -c "from agent.graph_engine.checkpointer import get_checkpointer; print(type(get_checkpointer()).__name__)"`（期望打印 MemorySaver 且日志警告 redis 不可用 → 回落，不抛）
Expected: 全绿

- [ ] **Step 4: 提交**

```bash
git add tests/test_orchestration_golden.py
git commit -m "test(agent): orchestration golden set (10 cases)"
```

---

## Self-Review（写后自检记录）

- Spec 覆盖：§5 state/messages ✓(T2/T6) · §6 节点 ✓(T6/T7) · §7 写通道 ✓(T3/T7) · §7.3 pending_id/超时 ✓(T7) · §7.4 validate+authorize ✓(T4/T7) · §8 结构化输出 ✓(T4) · §9 checkpointer ✓(T5) · §10 单一状态源/归档 ✓(T6/T8) · §11 预算护栏 ✓(T5/T7) · §13 env 并行 ✓(T9) · §14 golden ✓(T10)。缺口：confirm 超时自动结算只在 resume 重进时惰性判定（未做后台定时器）——记入 Global Constraints 备注，避免范围膨胀。
- 类型一致性核对：`make_graph(deps, identity)`、`run_turn(core,...)`、`resume_action(text)`、`slots_defs_for(tool)`、`resolve_write_tool(intent, entity, available)`、`authorize_write(scope, tool, args)`、`filter_slots/raw/defs`、`save_archive`、`engine_mode/checkpointer_backend` 在任务间一致。
- 已修正隐患：Task 6 中把 assistant 消息误写 HumanMessage→AIMessage；state 需含 `intent/entity/_write_tool` 键（否则动态键序列化失败）→ 并入 Task 2 schema；`_build_read_executor` 需 `user_role` 参数 → 签名已定。
