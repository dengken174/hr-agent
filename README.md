# HR Agent — 企业级 HR 智能助手（个人复刻项目）

> 基于 **LLM + LangGraph + MCP + RAG** 的企业 HR 智能助手，覆盖意图路由、多轮槽位填充、知识库检索问答、审批工作流、飞书集成、数据权限与脱敏、语音交互等能力。

## ⚠️ 重要声明（Disclosure）

- 本项目是**个人独立开发的复刻 / 学习项目**，用于技术学习与求职展示。
- 设计思路（意图路由、审批流、RAG 检索、权限与脱敏等）参考了我此前工作中接触到的真实企业 HR 系统，**全部代码凭理解从零重写**，未使用任何公司代码。
- 项目中的员工、薪资、组织等数据**全部为 mock 数据**，不包含任何真实企业数据或个人隐私。
- 本项目不代表任何公司的技术方案或实现，与任何公司无关。

---

## 项目简介

HR Agent 是一个面向企业 HR 场景的智能助手，后端为多智能体对话引擎，前端为管理看板。它能把「我这个月工资多少」「帮我请明天下午的假」「通过张三的请假申请」这类自然语言请求，路由到正确的意图、补齐参数、调用工具并返回结果，同时对越权查询、薪资敏感信息做权限校验和脱敏。

核心流程：

```
用户输入 → 意图识别(8类) → 槽位填充(多轮) → 图引擎编排(LangGraph)
            → MCP 工具调用(hris/feishu/approval/knowledge)
            → RAG 检索(混合检索+重排) → 权限校验/脱敏 → 回复(可溯源)
```

## 核心功能

- **多意图路由**：8 类意图识别（薪资、考勤、请假、审批、知识问答、闲聊、`out_of_scope` 拒答等），二级 `意图 + 实体` 路由。
- **多轮槽位填充**：请假等需多参数请求，通过槽位状态机收集并二次确认。
- **RAG 检索问答**：文档解析 → 分块 → 向量化 → 混合检索（FAISS + BM25 + RRF）→ 重排 → 带溯源引用回答。
- **审批工作流**：6 状态机审批流，飞书消息通知，支持申请/审批/驳回/撤销。
- **飞书集成**：文档、日历、任务、邮件、考勤、通知等 18 个 MCP 工具。
- **安全三件套**：`DataScope` 角色级数据权限、薪资分级脱敏、审计日志。
- **语音交互**：TTS（edge-tts）/ STT（faster-whisper）。
- **LangGraph 编排**：读写通道、检查点会话恢复、预算守卫。
- **可观测性**：LangSmith 全链路追踪。
- **Web 前端**：Vue3 聊天、数据看板、审批、知识库、技能管理、评测。

## 技术栈

| 分类 | 技术 |
|---|---|
| Agent 编排 | LangGraph、LangChain、MCP（langchain-mcp-adapters） |
| 大模型 | DeepSeek（OpenAI 兼容） |
| 向量 / 检索 | FAISS、Milvus、Elasticsearch(BM25)、sentence-transformers(BGE-M3/MiniLM) |
| 存储 | MySQL、Redis |
| 后端 | FastAPI、SSE、JWT + bcrypt、RBAC |
| 前端 | Vue3 + Element Plus + Pinia |
| 语音 | edge-tts、faster-whisper |
| 文档解析 | markitdown、PyMuPDF、PaddleOCR |
| 观测 | LangSmith |

## 目录结构

```
agent/          Agent 核心：意图/路由/记忆/槽位/图引擎(graph_engine)
backend/        FastAPI 后端 + 路由(chat/approval/knowledge/skills/tts/stt/eval)
db/             文档解析/分块/向量/检索/重排/数据权限/审计
mcp_servers/    MCP 工具服务：hris / feishu / approval / knowledge
frontend/       Vue3 前端（7 个视图）
docs/           设计文档（spec / plan）
tests/          pytest 单元测试
```

## 快速开始

```bash
# 1. 安装依赖（Python 3.10+）
pip install -r requirements.txt

# 2. 配置环境变量（详见 config.py）
export DEEPSEEK_API_KEY=...
export MYSQL_HOST=127.0.0.1 MYSQL_USER=root MYSQL_PASSWORD=... MYSQL_DATABASE=hragent
export REDIS_URL=redis://localhost:6379/0
# 飞书相关（可选，未配置时走 mock 降级）
export FEISHU_APP_ID=... FEISHU_APP_SECRET=...

# 3. CLI 快速体验
python main.py            # 交互式对话（/hr 切管理员、/emp 切员工）
python main.py --quick    # 意图分类快速测试

# 4. 启动 Web 服务（API + 前端）
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

---

## 开发方式：AI 辅助的 Vibe Coding

这个项目是我用 **Vibe Coding** 的方式、借助 AI 编码助手（Claude Code 等）从零构建的。

**工作流：**

1. 先用自然语言描述需求和设计意图，沉淀为 spec 设计文档（见 `docs/superpowers/specs/`）。
2. AI 根据 spec 生成实现代码和单元测试。
3. 我 review 每一处改动、跑测试、修 bug、做集成联调。
4. 发现问题 → 回到第 2 步迭代。

**分工：**

| 我负责 | AI 负责 |
|---|---|
| 系统架构与模块划分 | 脚手架 / 样板代码 |
| 技术选型（LangGraph / MCP / RAG 等） | 单元测试生成 |
| 代码 review 与安全加固 | 调试与报错定位 |
| 集成联调、数据流梳理 | 文档与注释 |

**原则：**

- 把 AI 当「结对编程的同事」，而不是「黑盒代码生成器」。
- 每个方向先写 spec / plan，再 TDD（先测试后实现）。
- AI 生成的代码全部经过人工 review，并由 `tests/` 下的 pytest 兜底验证。
- 目标是快速验证架构想法，同时保持代码可读、可维护、可追溯。

---

## License

MIT（仅供学习交流；如引用请注明出处）。
