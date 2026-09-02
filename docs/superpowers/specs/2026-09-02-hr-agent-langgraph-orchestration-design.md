# HR Agent 编排层 LangGraph 改造设计

日期: 2026-09-02
版本: v2（v1 基础上并入六条大厂向优化，见 §18 变更记录）
状态: 待评审
范围: 编排层重构（Block ④）。不涉及检索/飞书卡片/STT/流式安全（后续 Block 各自处理）；不涉及模型路由到异构供应商/评测平台/长记忆摘要（见 §16 非目标）。

## 1. 背景与动机

当前编排是「手写 `_preprocess` 外层状态机 + LangChain 经典 `AgentExecutor` 内层 tool-calling 循环」（`agent/executor.py`）。结构性缺陷：

1. **二次确认不可靠**：`_pending_confirmations` 进程内 dict（`executor.py:55`），重启即失、多 worker 不共享；`_check_pending_confirmation`（`executor.py:122`）死代码从未被调。
2. **请假写操作绕过确认与审计**：槽位填齐后直调 `feishu_submit_leave_request`（`executor.py:255-262`、`337-347`），跳过确认守卫。
3. **状态迁移隐式**：`_preprocess` if/else（`executor.py:403-454`）不可观测；多轮槽位状态在进程内 dict（`slot_filler.py:55-56`）。
4. **会话状态双源**：消息历史由 `MemoryManager`（Redis `session:{id}` + MySQL `conversations`）与待引入的 LangGraph checkpointer 并存，易不同步。
5. **非结构化输出**：意图/槽位用 `re.search(r"\{.*\}")` 从自由文本抠 JSON（`intent.py:72-84`），脆弱、不可校验。
6. **无全局护栏与归属校验**：只有 read 循环 `max_iterations=6`；无每请求预算/超时/参数归属越权校验。

## 2. 目标（对项目描述第四条 + 六条优化，逐条成立）

**能力目标**：意图分类/槽位收集/二次确认/拒答/Agent 执行建模为有向图；edges 声明式路由；槽位状态托管 Checkpointer（`thread_id=session_id` 跨请求/重启存续）；二次确认用 `interrupt` 做图级 HITL；控制流与值提取解耦；敏感流程（请假/审批）参数强约束。

**优化目标（六条）**：
- O1 会话状态**单一权威源**：消息进 graph state（`messages` + `add_messages`），checkpointer 管续存；MySQL 降为 append-only 审计落档；Redis 会话缓冲退出 graph 路径。
- O2 `confirm` 恢复用**结构化 `pending_id` + 显式 action**（不靠 NLU 猜词），带**超时策略**。
- O3 意图/槽位抽取从"抠 JSON"升级为**受约束的结构化输出**（function-calling / JSON schema + 枚举/字段校验）。
- O4 **每请求预算与超时护栏**：全局步骤/时长/LLM 调用预算、单步超时、collect 硬轮数上限、超限熔断终态。
- O5 编排层**最小 golden 回归集**（路由/确认/拒答判定），摆脱随机 mock。
- O6 写流程**参数归属校验**：目标对象与调用者权限一致才放行。

已确认取舍：`agent_execute` 只读（沿用现有 AgentExecutor，但记忆来源改为 state messages）；Checkpointer env 驱动 `redis`/`memory`，本地 Redis 不可达默认 memory；旧编排路径完整保留，env `AGENT_ENGINE` 回退。

## 3. 现状关键事实（改造锚点）

- 入口 `executor.py:585-600` `_LazyHRAgent` 单例；`backend/routes/chat.py` 与飞书 `event_handler.py` 调 `HRAgent.chat / chat_stream`。
- `chat/chat_stream` 每次请求局部构建 `AgentExecutor`（`executor.py:271-294`、`357-370`）；记忆经 `MemoryManager` `load_context/save_turn`（Redis 30min + MySQL）。
- 工具三类守卫：scope（身份/权限/审计 `_apply_scope_guard`）、确认（写工具 guard `_apply_confirmation_guard`）、skill 热插拔。
- `slot_filler.py` 已泛化到任意 `slots_def=[{key,required,ask,enum}]`；`_llm_extract_slots` 通用可注入；`consume()` 硬编码映射 leave tool（`slot_filler.py:92`）需去硬编码。
- 意图 8 类（`intent.py:19`）、`should_refuse`（`:41`）、`route_tools/_entity_narrow_tools`（`router.py`）；权限规则在 `db/scope.py`（salary/team/self/other/HR）。
- `db/audit.py` AuditRepo 已用于写操作审计；`_trace_metadata` 打 LangSmith 元数据。

## 4. 目标架构总览

```
外层（新，LangGraph 图 = 控制流 + 状态）
  intent → 路由 → [写通道: collect → confirm(interrupt) → validate(含归属校验) → execute_write] |
                   [读通道: agent_execute = 现有 AgentExecutor(仅只读工具, 记忆吃 state.messages)]
内层（沿用，只读）
  create_tool_calling_agent + AgentExecutor + state 消息裁剪窗口 + scope/audit
```

- 写流程：参数确定性收集（值提取，结构化输出）→ `confirm` interrupt → `validate`（schema + 归属）→ 直调写工具。
- 读流程：`agent_execute` 内 AgentExecutor 只见只读工具，LLM 自由选读工具；对话历史来自 `state.messages`。
- **单一状态源**：`state.messages` 是会话历史权威；每次调用终态后追加写 MySQL（append-only，审计/冷备份）；Redis 会话缓冲不再被 graph 路径读写。
- 写工具从读通道 LLM 可见工具集**结构性剔除**（fail-closed）。

## 5. 图 State（可序列化，Checkpointer 持久化）

```python
class HRGraphState(TypedDict):
    # 每轮输入
    user_input: str
    # 会话历史（单一权威源；reducer 裁剪至窗口上限）
    messages: Annotated[list[BaseMessage], add_messages_window]
    # 工作流状态
    collecting: bool
    collect_tool: str | None
    slots: dict
    pending_id: str | None        # 每次 confirm 生成 uuid，恢复时回传
    pending_tool: str | None
    pending_args: dict
    # 预算 / 护栏
    started_at: float | None
    llm_calls: int                # 已用 LLM 调用数
    steps: int                    # 已走节点步数（reducer 自增）
    collect_rounds: int
    # 本轮输出
    reply: str
    final_node: str               # END / refuse / general / execute_write / budget_exceeded ...
```

**说明**：
- `messages` 用自定义 reducer `add_messages_window` = `add_messages` + 裁剪到最近 `max_messages`（默认 20 条，防 state 无限膨胀；更早历史落 MySQL 冷档，未来长记忆摘要属非目标 §16）。写通道需上下文时直接读 `state.messages` 末尾摘录。
- 所有字段 JSON 可序列化，兼容 `MemorySaver`/Redis checkpointer；不存工具/LLM 对象。
- `started_at/llm_calls/steps` 由入口与节点写入，支撑 §11 预算与超时护栏。

## 6. 节点与边

所有节点按请求构建（闭包捕获该请求 llm/tools/scope/audit），图结构恒定，运行时靠 state 分支——保持"每请求局部构建"的并发隔离。

| 节点 | 职责 | 复用 |
|---|---|---|
| `intent` | `collecting` 则直通 `collect`；否则分类 + `should_refuse`；解析目标写工具 | `intent.py`、`router.py` |
| `refuse` | 拒答直答 → END | `should_refuse` 文案 |
| `general_reply` | general_chat/无工具直接 LLM 回复 | `_direct_reply*` |
| `collect` | 槽位收集（确定性值提取）；缺槽→ask 并 END（state 续存）；满→填 `pending_*` | `slot_filler.py` 泛化 |
| `confirm` | 生成 `pending_id`，`interrupt({"pending_id", "tool", "args"})`；恢复后按 action 分支 | 新 |
| `validate` | 参数 schema/枚举/类型校验 + **归属校验 O6**；非法→回 `collect`/纠错 | 新 + `db/scope.py` |
| `execute_write` | 直调写工具（注入调用者身份），审计 | `executor.py:255-262` |
| `agent_execute` | 只读：AgentExecutor（state 消息窗口），`save_turn` 追加 MySQL | `executor.py` 记忆改造 |
| `budget_gate` | 每个可分支节点前检查预算/超时，超限 → `budget_exceeded` 终态 | 新（§11） |

条件边：
```
START → intent
intent ─refuse→ refuse → END
intent ─general/no-tools→ general_reply → END
intent ─write(需槽)→ collect ─missing→ (ask) END ─ready→ confirm
intent ─write(args 已足)→ confirm
  confirm ─interrupt 挂起─→ 返回（等 resume 带 pending_id）
  confirm 恢复 confirm → validate → execute_write → END
  confirm 恢复 cancel → 回复"已取消" → END
intent ─read→ agent_execute → END
（所有内部跳转前过 budget_gate）
```

## 7. 写流程（脱离 LLM 循环）

**7.1 写工具注册表** `WRITE_SLOT_DEFS: {tool: [slots_def]}` 覆盖 `_WRITE_TOOLS` 9 个；请假沿用 `LEAVE_SLOTS`。缺 id（如 `approval_id`）由抽取器从 `state.messages` 摘录补。

**7.2 collect 行为**
- 每缺一槽产 ask 并 END（状态续存）；下轮 `intent` 见 `collecting=True` 直通 `collect`。
- `collect_rounds` 超过 `config.collect_max_rounds`（默认 5）→ 强制放弃并提示（防死循环，O4）。

**7.3 confirm（interrupt HITL，O2）**
```python
pending_id = uuid4().hex
action = interrupt({"pending_id": pending_id, "tool": pending_tool, "args": pending_args})
```
- 恢复入口统一：聊天文本 / 飞书卡片回调（Block⑥ 接）都走 `graph.invoke(Command(resume={"pending_id": ..., "action": "confirm"|"cancel"}), config={thread_id})`。
- **不靠关键词猜**：service 恢复时校验回传 `pending_id` 与挂起一致；文本层只做宽泛映射到 confirm/cancel，含 pending_id 的回调走精确匹配。
- **超时策略**：`pending_at` 记录；超过 `config.confirm_timeout`（默认 24h）→ 自动按 cancel 结算 + 通知（memory 后端惰性清理，redis 后端可 TTL）。

**7.4 validate（O3 + O6 + 强约束）**
- schema 校验：必填、enum、类型/日期；请假强制绑定调用者 `employee_id`。
- **归属校验（O6）** `authorize_params(scope, tool, args)`：目标对象（请假本人、被查员工、审批单属主等）与调用者 `DataScope` 一致才放行；复用 `db/scope.py` 规则与 `audit_repo`。
- 通过才 `execute_write`；不通过给纠错问句回 `collect` 或拒答。此即"控制流与值提取解耦"落点。

## 8. 结构化输出（O3）

- `IntentClassifier.classify` 改走受约束结构化：DeepSeek `response_format={"type":"json_object"}` + 显式 schema 约束字段（`intent` 限于 `INTENT_LABELS` 枚举、`entity.type` 白名单、`confidence` 数值区间）；供应商不支持时**兜底**用现有 regex（`intent.py:88-96` 保留为 fallback）。
- 槽位抽取 `_llm_extract_slots` 同理：以 `slots_def` 的 key/enum 约束输出字段；解析后按 enum/类型强校验，非法值不采纳并计入缺槽。
- 保留注入点（classifier/extractor 可 mock），单测不依赖真实 LLM。

## 9. Checkpointer（thread_id=session_id）

- 工厂 `get_checkpointer()`：`AGENT_CHECKPOINTER=redis` → `RedisSaver`（`langgraph-checkpoint-redis`，连 `config.redis.url`，导入/连接失败告警回落 memory）；默认 `memory` → `MemorySaver`。
- 本地现状：Redis 6379 不可达、包未装 → 默认 memory；启动 Redis / 配 `REDIS_URL` / 装包后置 env 即真跨重启。
- 线程清理沿用 `config.redis.session_ttl`（1800s）语义：`session_id → last_active`；超时新会话。memory 后端惰性清理；redis 后端可接 TTL。
- `state.messages` 承载会话历史 → 大 payload；由 `add_messages_window` 限窗，防 checkpoint 膨胀（O1/O4）。

## 10. 消息与状态源原则（O1）

- **权威**：graph `state.messages`（checkpointer 续存）。`load_context` 不再从 Redis 灌入 read 路径。
- **agent_execute 记忆改造**：现 `ConversationBufferWindowMemory` 从"自管 Redis"改为由 `state.messages` 末尾 K 条构建，每次 `invoke` 前刷新；运行产生的 AI 消息经 reducer 并入 state。
- **MySQL**：保留 append-only 写入（历史/审计/冷档），不承担会话热状态。
- **Redis**：会话缓冲退出 graph 路径（legacy 路径经其原 `MemoryManager` 仍用，见 §13 并行；graph 路径的 checkpointer 是否落在 Redis 见 §9，二者独立）。
- 迁移期以 env 并行验证，不一致时以 `state.messages` 为准。

## 11. 预算与超时护栏（O4）

- 每请求预算：`config.request_budget = {max_steps: 30, max_llm_calls: 12, max_seconds: 60}`（可调）。`budget_gate` 在每节点入口核 `steps/llm_calls/started_at`；超限 → `final_node="budget_exceeded"` + 可读说明 → END。
- 单步超时：对 LLM/tool 调用套 `asyncio.wait_for`，超时按该步失败处理（read 由 AgentExecutor 重试语义兜底；write 返回纠错）。
- `collect_rounds` 硬上限（§7.2）。confirm 超时（§7.3）。

## 12. 错误处理 / 审计 / 并发

- 并发：每请求闭包构建图，隔离并发。
- 审计：写工具执行前后走 `audit_repo`；`final_node` 落 trace 便于排障。
- 异常：execute 抛错 → 可读错误终态；LangSmith metadata 保留 `_trace_metadata`。
- 流式：`agent_execute` 读通道续用 `astream_events`；refuse/general/写结果首版整句输出，流式化列为可选增强。

## 13. 与旧路径并行（env 开关）

- 新引擎 `agent/graph_engine/service.py` 暴露与 `HRAgent` 同名 `chat()/chat_stream()`。
- `executor.py:_LazyHRAgent` 按 `AGENT_ENGINE`（默认 `graph`，`legacy` 回退经典）返回；`chat.py`/飞书 handler 零改动。
- legacy 路径保留现有 `MemoryManager`/Redis/MySQL 用法不动；graph 路径按 §10。两路径以 env 隔离，便于灰度与回滚。

## 14. 测试策略

- 既有 34 个纯逻辑测试保持绿（`tests/test_slot_filler.py` 断言随 `consume()` 去硬编码同步）。
- 新增：
  - `tests/test_graph_routing.py`：refuse/general/collect/confirm/read 各分支到正确终态；`thread_id` 续存多轮槽位；预算超限 → `budget_exceeded`。
  - `tests/test_write_channel.py`：补槽→confirm(`pending_id`)→resume(confirm/cancel/超时)→validate(含归属校验)→execute_write；非法参数回 collect。
  - `tests/test_structured_output.py`：schema 约束下 intent/槽位解析；供应商不支持时 regex 兜底仍可用。
  - `tests/test_orchestration_golden.py`（O5）：**最小 golden 集 10~15 条**（该拒答/该确认/该走槽/该直读），注入 fake classifier/extractor 断言终态与 `final_node`；作为编排回归基线。
- 全部 mock LLM/Redis/MySQL，确定性、可离线跑。

## 15. 边界与非目标（本 Block 明确不做）

- 不改检索管道、飞书卡片真实化、STT worker、流式安全、北森/websearch（后续 Block）。
- 不改项目描述文字（用户选择只补代码不动文字）。
- **异构模型路由**（分类用小模型/难任务切 reasoner）、**评测平台与数据飞轮**（线上轨迹回捞/LLM-as-judge 大盘）、**长记忆分层摘要** —— 记为未来 Block，不在 ④ 实现，仅 O5 在 ④ 内做最小 golden 种子。

## 16. 风险与回滚

- 写流程行为从"LLM 自由调用+工具 guard"→"确定性收集+interrupt"；长尾写命令可能转多轮补参。缓解：兜底提示 + `AGENT_ENGINE=legacy` 一键回退。
- 记忆单一源化改变 read 路径上下文来源（state 而非 Redis）：以 golden 回归 + legacy 并行验证防回归。
- `consume()`/`IntentClassifier` 签名变更影响存量测试与调用方，同步夹具。

## 17. 文件变更清单

- 新增 `agent/graph_engine/{__init__,state,write_slots,structured,checkpointer,graph,budget,service}.py`
- 改 `agent/executor.py`（`_LazyHRAgent` 引擎选择；经典代码不动）
- 改 `agent/slot_filler.py`（`consume` 去硬编码、SlotState 支持 tool、缺槽/超轮处理）
- 改 `agent/intent.py`（结构化输出 + regex 兜底）
- 改 `config.py`（`request_budget`/`collect_max_rounds`/`confirm_timeout` 等新配置）
- 改 `requirements.txt`（+ `langgraph`；`langgraph-checkpoint-redis` 可选注释）
- 新增测试：`test_graph_routing.py`、`test_write_channel.py`、`test_structured_output.py`、`test_orchestration_golden.py`；同步 `tests/test_slot_filler.py`

## 18. 变更记录（v1 → v2）

v2 按"大厂差异与优化"评审并入六条：O1 消息单一权威源（state.messages + MySQL 降级 append-only + Redis 退出 graph 路径）、O2 confirm 结构化 pending_id + 超时、O3 意图/槽位结构化输出（JSON schema/function-calling，regex 仅兜底）、O4 每请求预算与超时护栏 + collect 硬轮数、O5 编排层最小 golden 回归集、O6 写流程参数归属校验。相应新增 state 字段（messages/llm_calls/steps/pending_id/started_at 等）、节点（budget_gate）、文件（structured.py/budget.py）、配置与测试。
