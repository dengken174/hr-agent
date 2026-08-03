# HR Agent Phase 2 — MySQL 落地 + 飞书 Bot 上线

> 版本 v1.0 | 2026-08-03 | 数据库切换 + 端到端闭环

## 1. 背景

Phase 1 已完成 Agent 核心、4 个 MCP Server、飞书 API 对接。但所有后端数据均为 Python mock，重启丢失。需要接入真实数据库，并让飞书 Bot 真正可用。

## 2. 数据库切换：PostgreSQL → MySQL 8.0

### 2.1 原因

用户本地已有 MySQL 8.0（Community Server），无需额外安装。

### 2.2 变更对照

| 原设计 (PG) | 新设计 (MySQL) |
|-------------|---------------|
| `asyncpg` 驱动 | `aiomysql` 驱动 |
| `UUID` 主键 | `CHAR(36)` 主键 |
| `JSONB` 字段 | `JSON` 字段 |
| `TIMESTAMPTZ` | `DATETIME(3)` |
| `gen_random_uuid()` | Python `uuid.uuid4()` |

### 2.3 建表

```sql
CREATE TABLE users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(64) NOT NULL UNIQUE,
    password    VARCHAR(256) NOT NULL,
    display_name VARCHAR(64) NOT NULL,
    role        VARCHAR(16) NOT NULL DEFAULT 'employee',  -- employee | hr_admin | interviewer
    open_id     VARCHAR(128) DEFAULT NULL,                 -- 飞书 open_id 绑定
    created_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
);

CREATE TABLE conversations (
    id          CHAR(36) PRIMARY KEY,
    session_id  VARCHAR(128) NOT NULL,
    user_id     INT NOT NULL,
    role        VARCHAR(16) NOT NULL,   -- user | assistant
    content     TEXT NOT NULL,
    intent      VARCHAR(32) DEFAULT '',
    created_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_session (session_id, created_at)
);

CREATE TABLE approval_requests (
    id            CHAR(36) PRIMARY KEY,
    type          VARCHAR(32) NOT NULL,
    title         VARCHAR(256) NOT NULL,
    applicant_id  INT NOT NULL,
    body          JSON NOT NULL,
    status        VARCHAR(16) DEFAULT 'draft',
    assignee_id   INT DEFAULT NULL,
    result_reason TEXT,
    created_at    DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    updated_at    DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    resolved_at   DATETIME(3) DEFAULT NULL
);

CREATE TABLE approval_logs (
    id            CHAR(36) PRIMARY KEY,
    request_id    CHAR(36) NOT NULL,
    from_status   VARCHAR(16),
    to_status     VARCHAR(16),
    operator_id   INT NOT NULL,
    operator_role VARCHAR(16),
    comment       TEXT,
    created_at    DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (request_id) REFERENCES approval_requests(id)
);

CREATE TABLE knowledge_docs (
    id          CHAR(36) PRIMARY KEY,
    title       VARCHAR(256) NOT NULL,
    content     TEXT NOT NULL,
    category    VARCHAR(32) DEFAULT '',
    tags        JSON DEFAULT NULL,
    created_by  INT DEFAULT NULL,
    updated_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    created_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
);
```

### 2.4 改造清单

所有 `backend/routes/*.py` 中硬编码的 `MOCK_*` 列表替换为 MySQL 读写：
- `routes/auth_feishu.py` — MOCK_USERS → users 表查询
- `routes/chat.py` — 对话持久化 → conversations 表
- `routes/approval.py` — MOCK_APPROVALS → approval_requests 表
- `routes/knowledge.py` — MOCK_DOCS → knowledge_docs 表
- `mcp_servers/approval/` — SQLite 状态机 → MySQL 统一存储

## 3. SSE 流式修复

当前 `agent/executor.py` 的 `chat_stream` 并非真正流式，内部走 `ainvoke` 一次性返回后 `yield`。需要改为：
- AgentExecutor 使用 `astream_events` 逐 token 产出
- FastAPI SSE 端逐 token 推送
- 前端从 `chatSync` (POST) 切到 `EventSource` (SSE)

## 4. 飞书 Webhook

### 4.1 当前状态

- `backend/routes/auth_feishu.py` 的 `/feishu/webhook` 是占位实现
- `mcp_servers/feishu/` 已有 Channel SDK event handler
- 飞书 App 已配置 10/10 API 验证通过

### 4.2 接入方案

```
飞书服务器 → POST /feishu/webhook
  ├─ URL 验证 (challenge) → 返回 challenge
  ├─ im.message.receive_v1 → 提取文本 → HRAgent.chat() → 飞书回复 API
  └─ 其他事件 → 日志记录
```

### 4.3 事件处理

- 群聊：提取 `mention_text`（@Bot 后的内容），去掉 @ 部分，送入 Agent
- 单聊：直接提取消息文本送入 Agent
- 回复：调用飞书 `reply_message` API 将 Agent 回复发回会话

## 5. 前端

- 新增登录页 (`LoginView.vue`)，调用 `/api/auth/login`
- ChatView 从 POST 同步切为 SSE EventSource
- 其余页面 (Approval/Knowledge/Eval/Skill) 对接真实 API

## 6. 实现步骤

| # | 任务 | 产出 |
|---|------|------|
| 1 | 安装 aiomysql，创建 MySQL 数据库和表 | 数据库就绪 |
| 2 | 改造 config.py 加 MySQL 连接配置 | 配置就绪 |
| 3 | 改造 routes 从 mock → MySQL 读写 | 数据持久化 |
| 4 | 修复 SSE 流式 | 前端打字机效果 |
| 5 | 飞书 Webhook 接入 Agent | 飞书 Bot 可用 |
| 6 | 前端 SSE + 登录页 | Web 端完整 |

## 7. 关键决策

- 不用 Alembic（当前表结构稳定，直接手写建表 SQL）
- 不用 SQLAlchemy ORM，改用 raw SQL + aiomysql（用户习惯 MyBatis 风格）
- 审批状态机从 SQLite 迁到 MySQL
- 保持现有 mock 降级逻辑：MySQL 不可用时回退 mock，不中断服务
