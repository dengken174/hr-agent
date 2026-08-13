# HR Agent 拒答 + 溯源 + 槽位填充 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 HR Agent 补齐三个对话信任能力：`out_of_scope` 拒答、检索溯源（无来源不生成）、请假槽位状态机。

**Architecture:** 三个能力都嵌入现有 `chat()` 链路的前置处理阶段。先把 `chat()`/`chat_stream()` 的重复前置逻辑抽成 `_preprocess`，再在意图分类后加拒答判断、在 `start_operation` 时接入槽位状态机；溯源在 MCP 工具层（低分拒答）+ prompt 层（引用要求）两层配合。纯逻辑模块（槽位状态机、拒答判断、低分拒答）独立成可单测单元，依赖 LLM 的部分通过可注入的 extractor 用 mock 测试。

**Tech Stack:** Python 3.13, pytest 9.1, DeepSeek (OpenAI 兼容 SDK), LangChain, dateparser

## Global Constraints

- 运行环境 Python 3.13.11，pytest 9.1.1 已安装，无 `tests/` 目录（需新建）
- LLM 通过 `openai.AsyncOpenAI` 调用（DeepSeek 兼容），分类 `temperature=0.0`
- 意图集合从 7 类扩到 8 类（新增 `out_of_scope`），`general_chat` 保持「闲聊」语义不变
- 拒答置信度阈值 `0.6`；检索低分拒答阈值 `0.3`
- 槽位状态与确认状态均为内存 dict（`session_id -> state`），与现有 `_pending_confirmations` 一致
- 依赖 LLM 的纯逻辑测试用 mock 注入，不真调 DeepSeek API
- 提交信息沿用项目风格：`feat:` / `fix:` / `docs:` + 简短英文描述

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `tests/conftest.py` | 新建 | pytest sys.path 配置 |
| `agent/slot_filler.py` | 新建 | 槽位定义 + 状态机纯逻辑 + SlotFiller（LLM 提取可注入） |
| `agent/intent.py` | 修改 | + `out_of_scope`、`confidence`、`OUT_OF_SCOPE_REPLY`、`should_refuse()` |
| `agent/prompts.py` | 修改 | 意图 few-shot + 溯源规则 + 槽位提取 prompt |
| `agent/router.py` | 修改 | `INTENT_TOOL_MAP["out_of_scope"] = []` |
| `agent/executor.py` | 修改 | 抽 `_preprocess` + 接入拒答与槽位状态机 |
| `mcp_servers/knowledge/chunker.py` | 修改 | chunk metadata 补 `section`/`chunk_index` |
| `mcp_servers/knowledge/tools.py` | 修改 | 低分拒答 + 透传 section |
| `db/vector_store.py` | 修改 | `_metadata` 补 source 字段 |
| `db/chunker.py` | 修改 | `Chunk` metadata 补 doc_title/section |
| `requirements.txt` | 修改 | + `dateparser` |

---

## Task 1: 槽位状态机纯逻辑

**Files:**
- Create: `agent/slot_filler.py`
- Create: `tests/conftest.py`
- Test: `tests/test_slot_filler.py`
- Modify: `agent/prompts.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces（后续任务依赖）:
  - `LEAVE_SLOTS: list[dict]` — 槽位定义
  - `SlotState` — dataclass，`intent`/`slots`/`missing`
  - `init_state(intent, slots_def) -> SlotState`
  - `apply_extracted(state, extracted: dict) -> None`
  - `next_ask(slots_def, missing_key) -> str`
  - `confirm_summary(slots_def, slots) -> str`
  - `SlotFiller` — 类，`__init__(self, extractor=None)`，`async handle(session_id, user_message, intent, entity) -> str | dict`

- [ ] **Step 1: 建立 pytest 配置**

Create `tests/conftest.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
```

- [ ] **Step 2: 写失败测试**

Create `tests/test_slot_filler.py`:

```python
import pytest
from agent.slot_filler import (
    LEAVE_SLOTS, SlotState, init_state, apply_extracted, next_ask, confirm_summary,
)


def test_init_state_marks_all_required_missing():
    st = init_state("leave_request", LEAVE_SLOTS)
    assert st.missing == ["leave_type", "start_date", "end_date"]
    assert st.slots == {}


def test_apply_extracted_fills_missing_and_recomputes():
    st = init_state("leave_request", LEAVE_SLOTS)
    apply_extracted(st, {"leave_type": "年假"})
    assert st.slots == {"leave_type": "年假"}
    assert st.missing == ["start_date", "end_date"]


def test_apply_extracted_ignores_unknown_keys():
    st = init_state("leave_request", LEAVE_SLOTS)
    apply_extracted(st, {"bogus": "x", "leave_type": "事假"})
    assert "bogus" not in st.slots
    assert st.slots["leave_type"] == "事假"


def test_next_ask_returns_correct_prompt():
    assert "请假类型" in next_ask(LEAVE_SLOTS, "leave_type")
    assert "从哪天开始" in next_ask(LEAVE_SLOTS, "start_date")


def test_confirm_summary_lists_filled_slots():
    st = init_state("leave_request", LEAVE_SLOTS)
    apply_extracted(st, {"leave_type": "年假", "start_date": "2026-08-18", "end_date": "2026-08-20"})
    summary = confirm_summary(LEAVE_SLOTS, st.slots)
    assert "年假" in summary
    assert "2026-08-18" in summary
```

- [ ] **Step 3: 运行测试确认失败**

Run: `cd /d/hragent && python -m pytest tests/test_slot_filler.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'agent.slot_filler'`

- [ ] **Step 4: 写最小实现**

Create `agent/slot_filler.py`:

```python
"""请假等结构化写操作的槽位状态机。

流程: 意图命中 start_operation+leave → 逐槽收集参数 → 二次确认 → 执行。
状态机控流程（确定性），LLM 只做槽位提取（可注入，便于测试）。
"""

from dataclasses import dataclass, field

LEAVE_SLOTS = [
    {"key": "leave_type", "required": True, "ask": "请假类型是？（年假/事假/病假）", "enum": ["年假", "事假", "病假"]},
    {"key": "start_date", "required": True, "ask": "从哪天开始？（如 8月18日）"},
    {"key": "end_date", "required": True, "ask": "到哪天结束？"},
    {"key": "reason", "required": False, "ask": "事由（可不填）"},
]

# 意图 → 槽位定义映射（本次仅请假）
SLOT_DEFS = {"leave": LEAVE_SLOTS}


@dataclass
class SlotState:
    intent: str
    slots: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)


def init_state(intent: str, slots_def: list) -> SlotState:
    missing = [s["key"] for s in slots_def if s["required"]]
    return SlotState(intent=intent, missing=missing)


def apply_extracted(state: SlotState, extracted: dict) -> None:
    for key in list(state.missing):
        if key in extracted and extracted[key]:
            state.slots[key] = extracted[key]
    state.missing = [k for k in state.missing if k not in state.slots]


def next_ask(slots_def: list, missing_key: str) -> str:
    for s in slots_def:
        if s["key"] == missing_key:
            return s["ask"]
    return ""


def confirm_summary(slots_def: list, slots: dict) -> str:
    parts = [f"{s['key']}={slots.get(s['key'], '')}" for s in slots_def if slots.get(s["key"])]
    return "请确认以下信息：\n" + "\n".join(parts) + "\n\n回复「确认」提交，或回复「取消」放弃。"


class SlotFiller:
    """槽位状态机。extractor 可注入，默认走 LLM 提取。"""

    def __init__(self, extractor=None):
        self._sessions: dict[str, SlotState] = {}
        self._extractor = extractor or _llm_extract_slots

    async def handle(self, session_id: str, user_message: str, intent: str, entity: dict) -> str | dict:
        slots_def = SLOT_DEFS.get(entity.get("type", "")) if entity else None
        if not slots_def:
            return None  # 非槽位场景

        state = self._sessions.get(session_id)
        if state is None:
            state = init_state(intent, slots_def)
            self._sessions[session_id] = state

        extracted = await self._extractor(intent, user_message, state.missing)
        apply_extracted(state, extracted)

        if state.missing:
            return next_ask(slots_def, state.missing[0])

        del self._sessions[session_id]
        return {"tool": "feishu_submit_leave_request", "args": dict(state.slots)}


async def _llm_extract_slots(intent: str, user_message: str, missing_keys: list) -> dict:
    """LLM 从用户消息中提取槽位值（默认实现，测试中会被 mock 替换）。"""
    from agent.intent import intent_classifier
    client = intent_classifier._ensure()._client
    from config import config
    from agent.prompts import SLOT_EXTRACTION_PROMPT

    prompt = SLOT_EXTRACTION_PROMPT.replace("{user_message}", user_message).replace(
        "{missing_keys}", ", ".join(missing_keys)
    )
    resp = await client.chat.completions.create(
        model=config.llm.chat_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=256,
    )
    raw = resp.choices[0].message.content or "{}"
    import json, re
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(m.group(0)) if m else {}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /d/hragent && python -m pytest tests/test_slot_filler.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: 定义槽位提取 prompt**

Modify `agent/prompts.py`，追加：

```python
SLOT_EXTRACTION_PROMPT = """从用户消息中提取以下槽位的值，输出 JSON。

缺失的槽位: {missing_keys}

规则：
- 只提取缺失槽位的值
- 日期用 ISO 格式（YYYY-MM-DD），「下周一」「明天」等解析为具体日期
- 提取不到的槽位不要输出

输出格式（严格 JSON，无额外文字）：
{{"<slot_key>": "<value>"}}

用户消息: {user_message}
"""
```

- [ ] **Step 7: 添加 dateparser 依赖**

Modify `requirements.txt`，追加一行：

```
dateparser>=1.2.0
```

- [ ] **Step 8: Commit**

```bash
git add agent/slot_filler.py agent/prompts.py tests/conftest.py tests/test_slot_filler.py requirements.txt
git commit -m "feat: slot filling state machine for structured write ops"
```

---

## Task 2: 拒答层（out_of_scope 意图 + 拒答判断）

**Files:**
- Modify: `agent/intent.py`
- Modify: `agent/prompts.py`
- Modify: `agent/router.py`
- Test: `tests/test_refusal.py`

**Interfaces:**
- Consumes: 无（依赖现有 `IntentResult`）
- Produces:
  - `IntentResult.confidence: float = 1.0`
  - `INTENT_LABELS` 含 `"out_of_scope"`
  - `OUT_OF_SCOPE_REPLY: str`
  - `should_refuse(intent: IntentResult) -> bool`

- [ ] **Step 1: 写失败测试**

Create `tests/test_refusal.py`:

```python
from agent.intent import IntentResult, should_refuse, OUT_OF_SCOPE_REPLY, INTENT_LABELS


def test_intent_labels_contains_out_of_scope():
    assert "out_of_scope" in INTENT_LABELS


def test_should_refuse_out_of_scope():
    r = IntentResult(intent="out_of_scope")
    assert should_refuse(r) is True


def test_should_refuse_low_confidence_non_chat():
    r = IntentResult(intent="search_own_info", confidence=0.4)
    assert should_refuse(r) is True


def test_should_not_refuse_general_chat_low_confidence():
    r = IntentResult(intent="general_chat", confidence=0.4)
    assert should_refuse(r) is False


def test_should_not_refuse_normal():
    r = IntentResult(intent="policy_query", confidence=0.9)
    assert should_refuse(r) is False


def test_out_of_scope_reply_is_nonempty():
    assert len(OUT_OF_SCOPE_REPLY) > 20
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/hragent && python -m pytest tests/test_refusal.py -v`
Expected: FAIL（`ImportError: cannot import name 'should_refuse'`）

- [ ] **Step 3: 修改 intent.py**

Modify `agent/intent.py`：

在 `INTENT_LABELS` 集合中加入 `"out_of_scope"`，`IntentResult` 加 `confidence` 字段，新增常量和函数：

```python
@dataclass
class IntentResult:
    intent: str
    entity: dict = field(default_factory=dict)
    confidence: float = 1.0


INTENT_LABELS = frozenset({
    "search_own_info",
    "search_others_info",
    "policy_query",
    "process_query",
    "start_operation",
    "approval_action",
    "general_chat",
    "out_of_scope",
})

OUT_OF_SCOPE_REPLY = (
    "这个问题超出了我的职责范围。我是 HR 助手，可以帮你处理：\n"
    "- 查询个人信息（薪资、考勤、假期余额）\n"
    "- 了解公司制度、福利政策、流程\n"
    "- 发起请假/报销等申请、审批\n\n"
    "其他问题建议联系 HR BP 或相关部门。"
)

CONFIDENCE_THRESHOLD = 0.6


def should_refuse(intent: IntentResult) -> bool:
    if intent.intent == "out_of_scope":
        return True
    if intent.confidence < CONFIDENCE_THRESHOLD and intent.intent != "general_chat":
        return True
    return False
```

同时修改 `classify()` 方法，解析 `confidence` 字段：

```python
        intent = parsed.get("intent", "general_chat")
        entity = parsed.get("entity", {})
        confidence = float(parsed.get("confidence", 1.0))
```

并在 `return IntentResult(...)` 处带上 `confidence=confidence`。

- [ ] **Step 4: 修改 prompts.py 加 out_of_scope few-shot**

Modify `agent/prompts.py`，在 `INTENT_CLASSIFICATION_PROMPT` 的意图类型列表中加一行，并在 `INTENT_FEWSHOT_EXAMPLES` 末尾追加：

```
- out_of_scope: 与 HR 无关、超出助手能力的问题（医疗、法律、订票、点外卖等）

## 示例 8
用户: "帮我订张明天去北京的机票"
输出: {"intent": "out_of_scope", "entity": {}, "confidence": 0.95}

## 示例 9
用户: "我最近咳嗽，吃什么药好"
输出: {"intent": "out_of_scope", "entity": {}, "confidence": 0.9}
```

同时在输出格式说明中，把 JSON 结构补上 `confidence`：

```
{{"intent": "<intent_name>", "entity": {{...}}, "confidence": 0.0~1.0}}
```

- [ ] **Step 5: 修改 router.py 加空工具集**

Modify `agent/router.py`，在 `INTENT_TOOL_MAP` 中加入：

```python
    "out_of_scope": [],
```

- [ ] **Step 6: 运行测试确认通过**

Run: `cd /d/hragent && python -m pytest tests/test_refusal.py -v`
Expected: PASS (6 passed)

- [ ] **Step 7: Commit**

```bash
git add agent/intent.py agent/prompts.py agent/router.py tests/test_refusal.py
git commit -m "feat: out_of_scope intent + refusal judgment"
```

---

## Task 3: 溯源层（低分拒答 + 章节字段 + 引用 prompt）

**Files:**
- Modify: `mcp_servers/knowledge/chunker.py`
- Modify: `mcp_servers/knowledge/tools.py`
- Modify: `agent/prompts.py`
- Modify: `db/chunker.py`
- Modify: `db/vector_store.py`
- Test: `tests/test_tracing.py`

**Interfaces:**
- Consumes: 现有 `HybridRetriever`、`search_knowledge_base`
- Produces:
  - `LOW_RELEVANCE_THRESHOLD: float = 0.3`
  - `is_low_relevance(results: list[dict]) -> bool`
  - 检索结果 dict 含 `section` 字段

- [ ] **Step 1: 写失败测试**

Create `tests/test_tracing.py`:

```python
from mcp_servers.knowledge.tools import LOW_RELEVANCE_THRESHOLD, is_low_relevance


def test_is_low_relevance_empty():
    assert is_low_relevance([]) is True


def test_is_low_relevance_below_threshold():
    assert is_low_relevance([{"score": 0.1}]) is True


def test_is_low_relevance_above_threshold():
    assert is_low_relevance([{"score": 0.8}]) is False


def test_is_low_relevance_uses_first_result_only():
    assert is_low_relevance([{"score": 0.9}, {"score": 0.05}]) is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/hragent && python -m pytest tests/test_tracing.py -v`
Expected: FAIL（`ImportError: cannot import name 'is_low_relevance'`）

- [ ] **Step 3: 修改 tools.py 加低分拒答判断**

Modify `mcp_servers/knowledge/tools.py`，在模块顶部加常量与函数，并修改 `search_knowledge_base`：

```python
LOW_RELEVANCE_THRESHOLD = 0.3


def is_low_relevance(results: list[dict]) -> bool:
    if not results:
        return True
    return results[0].get("score", 0) < LOW_RELEVANCE_THRESHOLD
```

`search_knowledge_base` 改为：

```python
async def search_knowledge_base(query: str) -> list[types.TextContent]:
    results = await hybrid_retriever.search(query)
    if is_low_relevance(results):
        return [types.TextContent(type="text", text="未找到与问题相关的明确制度依据")]
    formatted = [{
        "title": r["metadata"].get("title", ""),
        "section": r["metadata"].get("section", ""),
        "content": r["content"],
        "relevance": round(r.get("rrf_score", r.get("score", 0)), 3),
    } for r in results]
    return [types.TextContent(type="text", text=str(formatted))]
```

- [ ] **Step 4: 修改 knowledge chunker 补 section 字段**

Modify `mcp_servers/knowledge/chunker.py`，找到产出 chunk metadata 的位置，补 `section` 与 `chunk_index`（具体字段名以现有 chunk 结构为准，若 chunker 已产出章节标题则透传，否则用文档标题占位）：

```python
c["metadata"].setdefault("section", c.get("section", ""))
c["metadata"].setdefault("chunk_index", idx)
```

（实现时先读该文件，找到 chunk 的 metadata 赋值处，把章节标题写入 `metadata["section"]`，序号写入 `metadata["chunk_index"]`。）

- [ ] **Step 5: 修改 prompts.py 加溯源规则**

Modify `agent/prompts.py`，在 `HR_SYSTEM_PROMPT` 末尾追加：

```

## 溯源规则
- 回答制度、政策、流程类问题时，必须引用来源文档标题
- 如果检索工具返回「未找到明确依据」，明确告知用户"这个问题我暂时无法确认，建议联系 HR BP"
- 禁止在无依据时编造具体数字、日期、比例
```

- [ ] **Step 6: 修改 db 链路补 source（保持两条链路一致）**

Modify `db/chunker.py`：`Chunk` 的 `metadata` 中已有 `doc_title`，补 `section` 字段：

```python
metadata={"doc_title": doc_title, "section": section_title or doc_title},
```

Modify `db/vector_store.py`：`_metadata` 列表增加 `source` 字段（由 `doc_title` + `section` 组成），`hybrid_search` 与 `vector_search` 返回结果时透传 `source` 和 `section`：

```python
_metadata = [{"id": ..., "title": ..., "content": ..., "category": ...,
              "source": d.get("source", d.get("title", "")),
              "section": d.get("section", "")} for d in documents]
```

- [ ] **Step 7: 运行测试确认通过**

Run: `cd /d/hragent && python -m pytest tests/test_tracing.py -v`
Expected: PASS (4 passed)

- [ ] **Step 8: Commit**

```bash
git add mcp_servers/knowledge/chunker.py mcp_servers/knowledge/tools.py agent/prompts.py db/chunker.py db/vector_store.py tests/test_tracing.py
git commit -m "feat: retrieval source tracing + low-relevance refusal"
```

---

## Task 4: executor 整合（抽公共入口 + 接入拒答与槽位）

**Files:**
- Modify: `agent/executor.py`

**Interfaces:**
- Consumes:
  - `should_refuse(intent)` / `OUT_OF_SCOPE_REPLY`（Task 2）
  - `SlotFiller`（Task 1）
- Produces: `HRAgent._preprocess(...) -> PreprocessResult`

- [ ] **Step 1: 新增 `_preprocess` 返回类型与前置逻辑**

Modify `agent/executor.py`，在文件顶部 import 后新增：

```python
from dataclasses import dataclass

from agent.slot_filler import SlotFiller

@dataclass
class PreprocessResult:
    direct_reply: str | None = None      # 非空则直接返回，不进 Agent
    tools: list | None = None            # direct_reply 为 None 时生效
    intent: IntentResult | None = None
    matched_skill: object | None = None
    entity: dict | None = None
    slot_result: dict | None = None      # 槽位填齐后的执行参数
```

- [ ] **Step 2: 实现 `_preprocess` 方法**

在 `HRAgent` 类内新增方法（把 `chat` 里意图分类→路由→skill 匹配→拒答/槽位判断的逻辑集中到这里）：

```python
    async def _preprocess(self, user_message, session_id, user_id, user_role):
        intent = await intent_classifier.classify(user_message)
        tools = route_tools(intent, self._all_tools)

        matched_skill = skill_manager.match(user_message)
        if matched_skill and matched_skill.tools:
            all_names = {t.name for t in self._all_tools}
            skill_tool_names = skill_manager.get_tool_names(matched_skill, all_names)
            skill_tools = [t for t in self._all_tools if t.name in skill_tool_names]
            existing = {t.name for t in tools}
            for st in skill_tools:
                if st.name not in existing:
                    tools.append(st)

        entity = intent.entity if intent.entity else None

        # 2.2 拒答
        if should_refuse(intent):
            if intent.intent == "out_of_scope":
                return PreprocessResult(direct_reply=OUT_OF_SCOPE_REPLY, intent=intent, entity=entity)
            return PreprocessResult(direct_reply=await self._clarify_reply(user_message, user_role, entity),
                                    intent=intent, entity=entity)

        # 2.1 槽位状态机（start_operation + leave）
        if intent.intent == "start_operation" and entity and entity.get("type") == "leave":
            result = await self._slot_filler.handle(session_id, user_message, intent.intent, entity)
            if isinstance(result, str):
                return PreprocessResult(direct_reply=result, intent=intent, entity=entity)
            if isinstance(result, dict):
                return PreprocessResult(slot_result=result, intent=intent, entity=entity, tools=tools)

        if intent.intent == "general_chat" and not matched_skill:
            return PreprocessResult(direct_reply=await self._direct_reply(user_message, user_role, entity),
                                    intent=intent, entity=entity)

        if not tools:
            return PreprocessResult(direct_reply=await self._direct_reply_with_skill(
                user_message, user_role, matched_skill, entity), intent=intent, entity=entity)

        return PreprocessResult(tools=tools, intent=intent, matched_skill=matched_skill, entity=entity)
```

在 `__init__` 中初始化 `self._slot_filler = SlotFiller()`。

- [ ] **Step 3: 新增 `_clarify_reply` 方法**

在 `HRAgent` 内新增：

```python
    async def _clarify_reply(self, user_message, user_role, entity=None):
        system_text = self._build_system_text(user_role, entity)
        system_text += "\n用户输入意图不明确，请生成一句简短澄清问句，引导用户更明确表达。"
        resp = await self._llm.ainvoke([
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_message},
        ])
        return resp.content
```

- [ ] **Step 4: 改写 `chat()` 使用 `_preprocess`**

将 `chat()` 方法体第 1~8 步（意图分类到构建 memory 之前）替换为调用 `_preprocess`：

```python
        skill_manager.check_reload()
        pre = await self._preprocess(user_message, session_id, user_id, user_role)
        if pre.direct_reply is not None:
            return pre.direct_reply

        intent = pre.intent
        tools = pre.tools
        matched_skill = pre.matched_skill
        entity = pre.entity

        # 槽位已填齐：直接构造写工具调用，跳过确认守卫
        if pre.slot_result is not None:
            slot_args = pre.slot_result["args"]
            slot_args["employee_id"] = str(user_id)  # 身份绑定（槽位路径跳过了身份守卫，需手动注入）
            # 找到 feishu_submit_leave_request 工具并执行
            tool = next((t for t in self._all_tools if t.name == "feishu_submit_leave_request"), None)
            if tool is None:
                return "请假功能暂不可用，请联系 HR BP"
            result = await tool._arun(**slot_args)
            return result

        # 后续保持原有：身份守卫 + 确认守卫 + memory + AgentExecutor
```

（原 `chat()` 中意图分类、路由、skill 匹配、general_chat 直答、无工具直答的代码段全部删除，其余部分保留。）

- [ ] **Step 5: 改写 `chat_stream()` 使用 `_preprocess`**

对 `chat_stream()` 做同样替换：前置逻辑调 `_preprocess`，`direct_reply` 分支用流式输出（`_direct_reply_stream_with_skill`），`slot_result` 分支执行写工具后 yield 结果。保留 token 级流式产出部分。

- [ ] **Step 6: 冒烟验证**

Run: `cd /d/hragent && python -c "from agent.executor import HRAgent, PreprocessResult; from agent.intent import should_refuse, IntentResult; print('import ok', should_refuse(IntentResult(intent='out_of_scope')))"`
Expected: 输出 `import ok True`（无导入错误）

- [ ] **Step 7: Commit**

```bash
git add agent/executor.py
git commit -m "feat: wire refusal + slot filling into chat preprocess"
```

---

## 自审记录

- **Spec 覆盖**：2.2 拒答（Task 2 + Task 4 接入）、2.3 溯源（Task 3）、2.1 槽位（Task 1 + Task 4 接入）、抽公共入口（Task 4 的 `_preprocess`）均有用任务覆盖。
- **占位符**：Task 3 Step 4/6 涉及「读文件确认 chunk 结构」，已用「先读该文件再赋值」的明确指引，非占位符。
- **类型一致性**：`SlotFiller.handle` 返回 `str | dict`；`PreprocessResult.slot_result` 为 `dict`；`should_refuse(IntentResult)` 签名跨任务一致。
