# HR Agent 拒答 + 溯源 + 槽位填充 设计方案

日期: 2026-08-13

## 概述

为 HR Agent 补齐「企业级对话能力」的三个地基能力，解决当前 demo 级实现的核心信任问题：

1. **拒答层（2.2）**：新增 `out_of_scope` 意图 + 置信度辅助，让 Agent 对「答不了的问题」明确拒答，而不是硬塞进某个意图瞎答。
2. **溯源层（2.3）**：检索结果补章节字段 + 低分拒答 + 引用溯源 prompt，做到「无来源不生成」。
3. **槽位填充（2.1）**：请假等结构化写操作从「LLM 一轮问全」升级为「确定性逐槽收集 + 二次确认」。

三者嵌入现有 `chat()` 链路，纯增量改造，不推倒现有架构。

## 现状分析

| 现状 | 问题 |
|------|------|
| 意图分类 7 类，`general_chat` 兜底 | 无 `out_of_scope`，问「订机票/医疗」会被硬分类瞎答 |
| 意图分类无置信度 | 低置信度也硬分类，无拒答/澄清机制 |
| 检索结果有 `title`（文档标题） | 缺章节字段，无法精确溯源 |
| 检索无低分拒答 | 低分结果照样给 LLM，诱发编造 |
| 请假靠 LLM 一轮问全 + 确认守卫 | 参数收集不可控，靠 LLM 自由发挥 |
| `chat()` / `chat_stream()` 逻辑重复 | 三处改动需写两遍 |

### 关键架构事实

- Agent 检索走 MCP 工具 `search_knowledge_base` → `HybridRetriever._mock_search`（关键词匹配，`score` 0~1）。真实 FAISS+BM25+RRF 在**主进程 HTTP API** `/api/knowledge/search`，MCP 子进程为响应快**故意跳过**（`_ensure_real_index` 恒返回 False）。
- 检索是 MCP 工具内部的事，executor 不直接调检索，因此「无来源拒答」分两层配合：工具层返回信号 + prompt 层约束 LLM。

## 整体架构（chat 链路改动）

```
用户输入
  │
  ▼
【2.2 拒答层】意图分类（新增 out_of_scope + 置信度辅助）
  ├─ out_of_scope → 固定拒答话术（不进 Agent）
  ├─ 低置信度 → 澄清回复
  │
  ▼
【2.1 槽位层】start_operation + leave → 槽位状态机
  ├─ 槽位未齐 → 逐槽反问（不进 Agent）
  ├─ 槽位齐 → 二次确认 → 提交（跳过确认守卫）
  │
  ▼
【2.3 溯源层】检索（MCP 工具层 + prompt 层）
  ├─ 检索低分 → 返回「未找到依据」→ LLM 据实拒答
  ├─ 有来源 → LLM 回复带引用
  │
  ▼
Agent 执行（现有逻辑：路由 + 身份守卫 + 确认守卫）
```

### 抽取公共入口

`chat()` 和 `chat_stream()` 两段重复代码抽取公共入口，避免三处改动写两遍。抽取方式：

- 将意图分类、拒答判断、槽位状态机接入、工具路由等**前置逻辑**抽为公共方法 `_preprocess(user_message, session_id, user_id, user_role) -> PreprocessResult`。
- `PreprocessResult` 携带三种结果之一：`direct_reply`（拒答/槽位反问，直接返回）、`proceed`（继续 Agent 流程，附路由后的 tools）。
- `chat()` 和 `chat_stream()` 都调用 `_preprocess`，根据结果分流，仅「最终 LLM 流式产出」部分保留差异。

---

## 2.2 拒答层

### 2.2.1 区分 `out_of_scope` 与 `general_chat`

| 意图 | 含义 | 例子 | 处理 |
|------|------|------|------|
| `general_chat` | 闲聊、打招呼 | 「你好」「谢谢」 | 正常礼貌回复 |
| `out_of_scope` | 答不了的问题 | 「订机票」「咳嗽吃什么药」 | 明确拒答 + 引导 |

### 2.2.2 意图分类改动（`agent/intent.py` + `agent/prompts.py`）

```python
# intent.py
INTENT_LABELS = frozenset({
    ..., "general_chat",
    "out_of_scope",     # 新增
})

@dataclass
class IntentResult:
    intent: str
    entity: dict = field(default_factory=dict)
    confidence: float = 1.0   # 新增，LLM 自评，仅辅助
```

prompt 加定义 + few-shot（few-shot 要给足否则判不准）：

```
- out_of_scope: 与 HR 无关、超出助手能力的问题（医疗、法律、订票、点外卖等）
## 示例 8
用户: "帮我订张明天去北京的机票"
输出: {"intent": "out_of_scope", "entity": {}, "confidence": 0.95}
## 示例 9
用户: "我最近咳嗽，吃什么药好"
输出: {"intent": "out_of_scope", "entity": {}, "confidence": 0.9}
```

### 2.2.3 拒答判定逻辑（`agent/executor.py` 插入点）

意图分类后立刻加：

```python
if intent.intent == "out_of_scope":
    return OUT_OF_SCOPE_REPLY
if intent.confidence < 0.6 and intent.intent != "general_chat":
    return await self._clarify_reply(user_message, user_role)
```

`_clarify_reply` 行为：低置信度时让 LLM 生成澄清问句（如「您是指查询年假余额吗？」），引导用户更明确表达，而不是硬答。

### 2.2.4 拒答话术（固定模板）

```python
OUT_OF_SCOPE_REPLY = (
    "这个问题超出了我的职责范围。我是 HR 助手，可以帮你处理：\n"
    "- 查询个人信息（薪资、考勤、假期余额）\n"
    "- 了解公司制度、福利政策、流程\n"
    "- 发起请假/报销等申请、审批\n\n"
    "其他问题建议联系 HR BP 或相关部门。"
)
```

固定模板好处：不额外花 LLM 调用，话术可控。

### 2.2.5 置信度定位（设计取舍）

LLM few-shot 分类的自评置信度不可靠，故定位为**辅助信号**（阈值宽松 0.6，只拦明显乱分）。**主信号**是：

1. `out_of_scope` 意图（明确判成答不了）
2. 2.3 的检索 `relevance` 阈值（检索不到就不生成）

### 2.2.6 代码改动清单

| 文件 | 改动 |
|------|------|
| `agent/intent.py` | `INTENT_LABELS` + `out_of_scope`；`IntentResult` + `confidence` |
| `agent/prompts.py` | 意图 prompt 加定义 + 2~3 个 few-shot |
| `agent/router.py` | `INTENT_TOOL_MAP["out_of_scope"] = []`（fail closed） |
| `agent/executor.py` | 意图分类后加拒答判断（抽公共入口时一并处理） |

---

## 2.3 溯源层

### 2.3.1 补「章节」字段（source 补全）

chunker 已算章节标题，入库时保留：

```python
# mcp_servers/knowledge/chunker.py 产出 chunk 时
c["metadata"] = {
    "title": doc["title"],        # 文档标题（已有）
    "section": section_title,     # 章节标题（新增，如"3.2 年假天数"）
    "chunk_index": idx,           # 序号（新增）
}
```

检索结果格式化透传，最终给 LLM 的结果从：

```json
{"title": "年假政策", "content": "...", "relevance": 0.8}
```

变为：

```json
{"title": "年假政策", "section": "3.2 年假天数", "content": "...", "relevance": 0.8}
```

### 2.3.2 低分拒答（工具层）

`search_knowledge_base` 最高分低于阈值直接返回「未找到」：

```python
async def search_knowledge_base(query: str):
    results = await hybrid_retriever.search(query)
    if not results or results[0].get("score", 0) < LOW_RELEVANCE_THRESHOLD:  # 0.3
        return [TextContent(text="未找到与问题相关的明确制度依据")]
    ...
```

### 2.3.3 引用溯源 prompt（prompt 层）

`HR_SYSTEM_PROMPT` 加硬规则：

```
## 溯源规则
- 回答制度、政策、流程类问题时，必须引用来源文档标题
- 如果检索工具返回「未找到明确依据」，明确告知用户"这个问题我暂时无法确认，建议联系 HR BP"
- 禁止在无依据时编造具体数字、日期、比例
```

### 2.3.4 无来源拒答的两层设计（关键取舍）

检索发生在 MCP 子进程内部，executor 不直接调检索，故拒答分两层配合：

```
工具层：score < 阈值 → 返回「未找到」信号
prompt 层：LLM 看到「未找到」→ 据实拒答，不编造
```

不放在 executor 层做拒答判断的原因：executor 拿不到检索分数（检索在 MCP 子进程内部），跨进程传分数成本高。让工具返回明确信号 + prompt 约束是最自然的做法。

### 2.3.5 代码改动清单

| 文件 | 改动 |
|------|------|
| `mcp_servers/knowledge/chunker.py` | chunk metadata 保留 `section` + `chunk_index` |
| `mcp_servers/knowledge/tools.py` | `search_knowledge_base` 低分拒答 + 透传 section |
| `agent/prompts.py` | `HR_SYSTEM_PROMPT` 加溯源规则 |
| `db/vector_store.py` + `db/chunker.py` | 真实索引链路（HTTP API）同样补 source，保持两条链路一致 |

### 2.3.6 低分拒答阈值

初始值 `0.3`（mock 检索的 `score` 是关键词命中比例，低于 0.3 基本等于没命中）。后续可调。

---

## 2.1 槽位状态机

### 2.1.1 槽位定义（先做请假一个场景）

请假工具参数直接映射槽位：

```python
LEAVE_SLOTS = [
    {"key": "leave_type", "required": True, "ask": "请假类型是？（年假/事假/病假）", "enum": ["年假","事假","病假"]},
    {"key": "start_date", "required": True, "ask": "从哪天开始？（如 8月18日）"},
    {"key": "end_date",   "required": True, "ask": "到哪天结束？"},
    {"key": "reason",     "required": False, "ask": "事由（可不填）"},
]
```

### 2.1.2 状态机流程

```
用户「帮我请个假」
  → 意图 start_operation + entity(leave)
  → 槽位状态机启动（会话级）
  → 缺 leave_type → 反问「请假类型？」
用户「年假」
  → LLM 提取 leave_type=年假
  → 缺 start_date → 反问「从哪天开始？」
用户「下周一」
  → LLM 提取 + 解析 → start_date=2026-08-18
  → 缺 end_date → 反问「到哪天？」
用户「到下周三」
  → 提取 end_date=2026-08-20
  → 全齐 → 二次确认「年假 3 天（8/18~8/20），提交吗？」
用户「确认」
  → 执行 submit_leave_request
```

### 2.1.3 实现方式（方案 B：状态机 + LLM 提取）

状态机控流程（确定性），LLM 只做「槽位提取」这一件事：

```python
class SlotFillingState:
    intent: str            # leave_request
    slots: dict            # {"leave_type": "年假", ...}
    missing: list          # ["start_date", "end_date"]

    async def handle(self, user_msg: str) -> str:
        # 1. LLM 只做提取：从用户话里抽出能填的槽位
        extracted = await llm_extract_slots(self.intent, user_msg, self.missing)
        # 2. 状态机决定下一步（确定性，不经 LLM）
        self.slots.update(extracted)
        self.missing = [k for k in self.missing if k not in self.slots]
        if self.missing:
            return next_ask(self.missing[0])   # 逐个问
        return confirm_summary(self.slots)      # 全齐 → 二次确认
```

### 2.1.4 与现有确认守卫的整合（核心决策）

避免双重确认：

| 机制 | 职责 | 问题 |
|------|------|------|
| 现有确认守卫 | 写工具首次调用 → 确认提示 | 参数靠 LLM 一轮问全，不可控 |
| 槽位状态机 | 逐槽收集 + 二次确认 | 和确认守卫的「确认」重叠 |

整合方案：

```
start_operation + leave  → 槽位状态机（内部含二次确认）→ 直接执行（跳过确认守卫）
其他写操作              → 现有确认守卫（不变）
```

槽位状态机是确认守卫在「结构化写操作」上的升级版，两者不冲突。

### 2.1.5 状态存储

内存 dict（`session_id → SlotState`），与现有 `_pending_confirmations` 一致：

```python
class SlotFiller:
    def __init__(self):
        self._sessions: dict[str, SlotState] = {}
```

**已知限制**：单进程有效，重启丢失、多进程不共享（与现有确认守卫同一问题）。后续 Redis 启动后，`_pending_confirmations` 和槽位状态一起迁 Redis。

### 2.1.6 日期解析

「下周一」「明天」等自然语言，LLM 槽位提取时直接输出 ISO 日期（`2026-08-18`），后端用 `dateparser` 兜底（LLM 未解析时）。避免自然语言日期直接传给工具导致报错。

### 2.1.7 代码改动清单

| 文件 | 改动 |
|------|------|
| `agent/slot_filler.py`（新） | 槽位定义 + 状态机 + LLM 槽位提取 |
| `agent/executor.py` | chat 链路加接入点：`start_operation`+`leave` 走槽位状态机，跳过确认守卫 |
| `agent/prompts.py` | 槽位提取 few-shot prompt |
| `requirements.txt` | + `dateparser` |

---

## 涉及文件清单（汇总）

| 文件 | 改动 | 对应子项 |
|------|------|---------|
| `agent/intent.py` | `out_of_scope` + `confidence` | 2.2 |
| `agent/prompts.py` | 意图/溯源/槽位 prompt | 2.2 + 2.3 + 2.1 |
| `agent/router.py` | `out_of_scope` 空工具集 | 2.2 |
| `agent/executor.py` | 抽公共入口 + 拒答判断 + 槽位接入 | 2.2 + 2.1 |
| `agent/slot_filler.py`（新） | 槽位状态机 | 2.1 |
| `mcp_servers/knowledge/chunker.py` | chunk 补 section/chunk_index | 2.3 |
| `mcp_servers/knowledge/tools.py` | 低分拒答 + 透传 section | 2.3 |
| `db/vector_store.py` + `db/chunker.py` | 真实索引链路补 source | 2.3 |
| `requirements.txt` | + `dateparser` | 2.1 |

## 测试计划

| 场景 | 输入 | 预期 |
|------|------|------|
| 拒答-领域外 | 「帮我订张机票」 | 返回 `OUT_OF_SCOPE_REPLY` |
| 拒答-能力外 | 「咳嗽吃什么药」 | 返回 `OUT_OF_SCOPE_REPLY` |
| 拒答-闲聊 | 「你好」 | 正常礼貌回复（不拒答） |
| 溯源-低分 | 检索无命中 | 工具返回「未找到」，LLM 据实拒答 |
| 溯源-有来源 | 「年假有几天」 | 回复引用《年假政策》 |
| 槽位-完整流程 | 「帮我请个假」→逐槽 | 逐槽反问 → 二次确认 → 执行 |
| 槽位-日期解析 | 「下周一」 | 解析为 ISO 日期 |

## 已知限制

1. 槽位状态与确认状态均为内存存储，重启丢失、多进程不共享，待 Redis 启动后迁移。
2. LLM 自评置信度不可靠，仅作辅助信号。
3. mock 检索（MCP 工具）与真实混合检索（HTTP API）两条链路需同时补 source 字段，保持一致性。
4. 槽位状态机本次仅覆盖请假场景，其他结构化写操作（报销等）后续推广。
