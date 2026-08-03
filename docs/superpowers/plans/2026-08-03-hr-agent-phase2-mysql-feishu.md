# HR Agent Phase 2 — MySQL + 飞书 Bot 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将所有 mock 数据替换为 MySQL 持久化存储，修复 SSE 真正流式输出，接入飞书 Webhook 让 Bot 在群里可用。

**Architecture:** FastAPI + aiomysql 连接池 + raw SQL 查询，5 张表覆盖用户/对话/审批/知识库。飞书 Webhook → EventParser → EventRouter → HRAgent.chat() → FeishuClient 回复。前端切 SSE EventSource。

**Tech Stack:** Python 3, FastAPI, aiomysql, MySQL 8.0, Vue 3, Element Plus, DeepSeek API, LangChain

## Global Constraints

- MySQL 8.0 Community Server 已安装，运行在 localhost:3306
- 数据库名: `hragent`
- 表引擎: InnoDB, 字符集: utf8mb4
- 不用 ORM，raw SQL + aiomysql 连接池
- 所有路由保持现有降级逻辑（MySQL 不可用时回退 mock）
- 飞书 App ID: cli_xxx
- 飞书 open_id: ou_xxx

---

### Task 1: MySQL 建库建表 + aiomysql 安装

**Files:**
- Create: `D:/hragent/db/schema.sql`
- Create: `D:/hragent/db/seed.sql`
- Modify: `D:/hragent/requirements.txt`

**Interfaces:**
- Produces: MySQL 数据库 `hragent`，5 张表已建，种子数据已插入

- [ ] **Step 1: 安装 aiomysql**

```bash
cd D:/hragent && pip install aiomysql pymysql
```

- [ ] **Step 2: 创建数据库和表**

```sql
-- D:/hragent/db/schema.sql
CREATE DATABASE IF NOT EXISTS hragent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE hragent;

-- 用户表（替代 middleware.py 的 MOCK_USERS）
CREATE TABLE IF NOT EXISTS users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(64) NOT NULL UNIQUE,
    password    VARCHAR(256) NOT NULL,
    display_name VARCHAR(64) NOT NULL,
    role        VARCHAR(16) NOT NULL DEFAULT 'employee',
    open_id     VARCHAR(128) DEFAULT NULL,
    created_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 对话历史（替代 agent/memory.py 中 asyncpg 的 messages 表）
CREATE TABLE IF NOT EXISTS conversations (
    id          CHAR(36) PRIMARY KEY,
    session_id  VARCHAR(128) NOT NULL,
    user_id     INT NOT NULL,
    role        VARCHAR(16) NOT NULL,
    content     TEXT NOT NULL,
    intent      VARCHAR(32) DEFAULT '',
    created_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_session (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 审批请求表（替代 routes/approval.py 的 MOCK_APPROVALS + mcp_servers/approval/ 的 SQLite）
CREATE TABLE IF NOT EXISTS approval_requests (
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
    resolved_at   DATETIME(3) DEFAULT NULL,
    INDEX idx_status (status),
    INDEX idx_applicant (applicant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 审批日志
CREATE TABLE IF NOT EXISTS approval_logs (
    id            CHAR(36) PRIMARY KEY,
    request_id    CHAR(36) NOT NULL,
    from_status   VARCHAR(16),
    to_status     VARCHAR(16),
    operator_id   INT NOT NULL,
    operator_role VARCHAR(16),
    comment       TEXT,
    created_at    DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (request_id) REFERENCES approval_requests(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 知识库文档（替代 routes/knowledge.py 的 MOCK_DOCS）
CREATE TABLE IF NOT EXISTS knowledge_docs (
    id          CHAR(36) PRIMARY KEY,
    title       VARCHAR(256) NOT NULL,
    content     TEXT NOT NULL,
    category    VARCHAR(32) DEFAULT '',
    tags        JSON DEFAULT NULL,
    created_by  INT DEFAULT NULL,
    updated_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    created_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Execute:
```bash
mysql -u root -e "source D:/hragent/db/schema.sql"
```

- [ ] **Step 3: 插入种子数据**

```sql
-- D:/hragent/db/seed.sql
USE hragent;

INSERT INTO users (username, password, display_name, role) VALUES
('admin', 'admin123', 'HR Admin', 'hr_admin'),
('employee', 'emp123', '张三', 'employee'),
('interviewer', 'int123', '面试者', 'interviewer'),
('zhangsan', 'emp123', '张三', 'employee'),
('lisi', 'emp123', '李四', 'employee'),
('wangwu', 'emp123', '王五', 'employee');

INSERT INTO knowledge_docs (id, title, content, category, tags) VALUES
('K001', '入职流程指南', '报到时间：周一至周五 9:00；地点：HR 办公室 3F。携带材料：身份证原件、学历学位证复印件、离职证明、银行卡。当天安排：签劳动合同、领取办公设备、开通公司账号、入职培训。', 'guide', '["入职","流程","报到"]'),
('K002', '年假政策', '入职满1年不满10年：5天年假；满10年不满20年：10天年假；满20年：15天年假。年假可跨年累计最多5天。未休年假按日工资的300%补偿。', 'policy', '["年假","休假","福利"]'),
('K003', '五险一金缴纳标准', '养老保险：公司16% 个人8%；医疗保险：公司8.5% 个人2%；失业保险：公司0.5% 个人0.5%；工伤保险：公司0.2%-1.9% 个人0%；生育保险：公司0.5% 个人0%；住房公积金：公司5%-12% 个人5%-12%。缴纳基数：上一年度月平均工资，每年7月调整。', 'policy', '["五险一金","社保","公积金","福利"]'),
('K004', '住房补贴政策', 'P6及以上每月1500元住房补贴。P5及以下每月800元。需提供租房合同。入职满3个月后开始发放。异地调动另有搬迁补贴。', 'benefit', '["住房补贴","福利","租房"]'),
('K005', '培训与发展', '新员工入职培训持续1周。技术培训：内部技术分享每周三下午。外部培训：年度预算5000元每人，可报销培训课程和书籍费用。晋升周期：每年2次晋升窗口（3月和9月）。', 'policy', '["培训","晋升","发展"]'),
('K006', '面试指南', '面试流程：简历筛选 → 技术笔试/作品集评审 → 技术面（2轮）→ HR面 → 终面。全程约2-3周。面试形式支持线上面试。技术面侧重算法/系统设计/项目经验。', 'guide', '["面试","招聘","流程"]');

INSERT INTO approval_requests (id, type, title, applicant_id, body, status, created_at, updated_at) VALUES
('APR-001', 'leave', '年假申请 — 3天', 1001, '{"leave_type":"年假","start_date":"2026-07-20","end_date":"2026-07-22","days":3}', 'pending', '2026-07-15T10:30:00', '2026-07-15T10:30:00'),
('APR-002', 'expense', '差旅报销 — 上海出差', 1002, '{"amount":3500,"category":"交通+住宿","trip":"上海"}', 'pending', '2026-07-14T14:20:00', '2026-07-14T14:20:00'),
('APR-003', 'benefit', '培训费用申请 — Python进阶', 2001, '{"amount":2800,"course":"Python进阶","platform":"极客时间"}', 'approved', '2026-07-10T09:00:00', '2026-07-11T16:00:00'),
('APR-004', 'leave', '事假申请 — 1天', 3001, '{"leave_type":"事假","start_date":"2026-07-18","days":1}', 'rejected', '2026-07-12T11:00:00', '2026-07-13T09:30:00');
```

Execute:
```bash
mysql -u root hragent < D:/hragent/db/seed.sql
```

- [ ] **Step 4: 更新 requirements.txt**

```
# D:/hragent/requirements.txt
aiomysql>=0.2.0
pymysql>=1.1.0
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
sse-starlette>=2.0.0
langchain>=0.3.0
langchain-openai>=0.2.0
langchain-mcp-adapters>=0.1.0
httpx>=0.27.0
redis>=5.0.0
```

- [ ] **Step 5: 验证**

```bash
mysql -u root hragent -e "SHOW TABLES; SELECT COUNT(*) AS user_count FROM users; SELECT COUNT(*) AS doc_count FROM knowledge_docs; SELECT COUNT(*) AS approval_count FROM approval_requests;"
```

Expected output: 5 tables, 6 users, 6 docs, 4 approvals

- [ ] **Step 6: Commit**

```bash
git add D:/hragent/db/ D:/hragent/requirements.txt
git commit -m "feat: add MySQL schema, seed data, and aiomysql dependency"
```

---

### Task 2: config.py 加 MySQL 连接配置

**Files:**
- Modify: `D:/hragent/config.py`

**Interfaces:**
- Produces: `config.mysql` 可用，包含 `host, port, user, password, database, pool_min, pool_max` 字段

- [ ] **Step 1: 添加 MySQLConfig 并替换 PostgresConfig**

在 `D:/hragent/config.py` 中：

```python
# 替换 PostgresConfig:
@dataclass
class MySQLConfig:
    host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    port: int = int(os.getenv("MYSQL_PORT", "3306"))
    user: str = os.getenv("MYSQL_USER", "root")
    password: str = os.getenv("MYSQL_PASSWORD", "")
    database: str = os.getenv("MYSQL_DATABASE", "hragent")
    pool_min: int = 1
    pool_max: int = 5

# Config 类中: postgres: PostgresConfig → mysql: MySQLConfig
```

完整修改内容：
- 删除 `PostgresConfig` 类
- 添加 `MySQLConfig` 类
- 修改 `Config` 类的 `postgres` 字段为 `mysql: MySQLConfig = field(default_factory=MySQLConfig)`

- [ ] **Step 2: 验证**

```bash
cd D:/hragent && python -c "from config import config; print(config.mysql.host, config.mysql.database)"
```

Expected: `127.0.0.1 hragent`

- [ ] **Step 3: Commit**

```bash
git add D:/hragent/config.py
git commit -m "feat: replace PostgresConfig with MySQLConfig"
```

---

### Task 3: 创建 db 模块 — MySQL 连接池

**Files:**
- Create: `D:/hragent/db/__init__.py`
- Create: `D:/hragent/db/connection.py`
- Create: `D:/hragent/db/repositories.py`

**Interfaces:**
- Consumes: `config.mysql`
- Produces:
  - `get_pool() -> aiomysql.Pool` — 获取/创建连接池
  - `UserRepo`, `ConversationRepo`, `ApprovalRepo`, `KnowledgeRepo` — 四个数据访问类

- [ ] **Step 1: 创建连接池模块**

```python
# D:/hragent/db/__init__.py
from db.connection import get_pool, close_pool
from db.repositories import UserRepo, ConversationRepo, ApprovalRepo, KnowledgeRepo
```

```python
# D:/hragent/db/connection.py
import logging
import aiomysql
from config import config

logger = logging.getLogger(__name__)
_pool: aiomysql.Pool | None = None


async def get_pool() -> aiomysql.Pool:
    global _pool
    if _pool is None:
        _pool = await aiomysql.create_pool(
            host=config.mysql.host,
            port=config.mysql.port,
            user=config.mysql.user,
            password=config.mysql.password,
            db=config.mysql.database,
            minsize=config.mysql.pool_min,
            maxsize=config.mysql.pool_max,
            autocommit=True,
            charset='utf8mb4',
        )
        logger.info("MySQL pool created: %s:%s/%s", config.mysql.host, config.mysql.port, config.mysql.database)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        logger.info("MySQL pool closed")
```

- [ ] **Step 2: 创建 Repository 类**

```python
# D:/hragent/db/repositories.py
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Any

from db.connection import get_pool

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


class UserRepo:
    async def get_by_username(self, username: str) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM users WHERE username = %s", (username,))
                return await cur.fetchone()

    async def get_by_id(self, user_id: int) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                return await cur.fetchone()


import aiomysql


class ConversationRepo:
    async def save(self, session_id: str, user_id: int, role: str, content: str, intent: str = "") -> str:
        conv_id = uuid.uuid4().hex
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO conversations (id, session_id, user_id, role, content, intent) VALUES (%s, %s, %s, %s, %s, %s)",
                    (conv_id, session_id, user_id, role, content, intent),
                )
        return conv_id

    async def get_history(self, session_id: str, limit: int = 50) -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT role, content, intent, created_at FROM conversations WHERE session_id = %s ORDER BY created_at DESC LIMIT %s",
                    (session_id, limit),
                )
                rows = await cur.fetchall()
                return list(reversed(rows))


class ApprovalRepo:
    async def list_all(self, status: str = "") -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if status:
                    await cur.execute("SELECT * FROM approval_requests WHERE status = %s ORDER BY created_at DESC", (status,))
                else:
                    await cur.execute("SELECT * FROM approval_requests ORDER BY created_at DESC")
                rows = await cur.fetchall()
                for r in rows:
                    if isinstance(r.get("body"), str):
                        r["body"] = json.loads(r["body"])
                    if r.get("created_at"):
                        r["created_at"] = r["created_at"].strftime('%Y-%m-%dT%H:%M:%S')
                    if r.get("updated_at"):
                        r["updated_at"] = r["updated_at"].strftime('%Y-%m-%dT%H:%M:%S')
                return rows

    async def get_by_id(self, approval_id: str) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM approval_requests WHERE id = %s", (approval_id,))
                row = await cur.fetchone()
                if row and isinstance(row.get("body"), str):
                    row["body"] = json.loads(row["body"])
                return row

    async def update_status(self, approval_id: str, status: str, operator_id: int, comment: str = "") -> bool:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE approval_requests SET status = %s, result_reason = %s, updated_at = NOW(3), resolved_at = NOW(3) WHERE id = %s",
                    (status, comment, approval_id),
                )
                return cur.rowcount > 0

    async def get_stats(self) -> dict:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("""
                    SELECT
                        SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                        SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved,
                        SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                        COUNT(*) AS total
                    FROM approval_requests
                """)
                return await cur.fetchone()


class KnowledgeRepo:
    async def list_all(self, category: str = "") -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if category:
                    await cur.execute("SELECT * FROM knowledge_docs WHERE category = %s ORDER BY updated_at DESC", (category,))
                else:
                    await cur.execute("SELECT * FROM knowledge_docs ORDER BY updated_at DESC")
                rows = await cur.fetchall()
                for r in rows:
                    if isinstance(r.get("tags"), str):
                        r["tags"] = json.loads(r["tags"])
                    if r.get("updated_at"):
                        r["updated_at"] = r["updated_at"].strftime('%Y-%m-%dT%H:%M:%S')
                    if r.get("created_at"):
                        r["created_at"] = r["created_at"].strftime('%Y-%m-%dT%H:%M:%S')
                return rows

    async def get_categories(self) -> list[str]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT DISTINCT category FROM knowledge_docs ORDER BY category")
                rows = await cur.fetchall()
                return [r[0] for r in rows]

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                like = f"%{query}%"
                await cur.execute(
                    "SELECT id, title, content, category FROM knowledge_docs WHERE title LIKE %s OR content LIKE %s LIMIT %s",
                    (like, like, top_k),
                )
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    results.append({
                        "title": r["title"],
                        "content": r["content"][:200],
                        "relevance": 0.9 if query in r["title"] else 0.5,
                        "doc_id": r["id"],
                    })
                return results

    async def create(self, data: dict) -> dict:
        doc_id = data.get("id") or f"K{uuid.uuid4().hex[:6].upper()}"
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO knowledge_docs (id, title, content, category, tags, created_by) VALUES (%s, %s, %s, %s, %s, %s)",
                    (doc_id, data["title"], data["content"], data.get("category", ""), json.dumps(data.get("tags", []), ensure_ascii=False), data.get("created_by")),
                )
        data["id"] = doc_id
        return data

    async def update(self, doc_id: str, data: dict) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE knowledge_docs SET title=%s, content=%s, category=%s, tags=%s, updated_at=NOW(3) WHERE id=%s",
                    (data["title"], data["content"], data.get("category", ""), json.dumps(data.get("tags", []), ensure_ascii=False), doc_id),
                )
                if cur.rowcount == 0:
                    return None
        return await self.get_by_id(doc_id)

    async def get_by_id(self, doc_id: str) -> dict | None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SELECT * FROM knowledge_docs WHERE id = %s", (doc_id,))
                return await cur.fetchone()

    async def delete(self, doc_id: str) -> bool:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM knowledge_docs WHERE id = %s", (doc_id,))
                return cur.rowcount > 0
```

- [ ] **Step 3: 验证连接**

```bash
cd D:/hragent && python -c "import asyncio; from db.connection import get_pool; asyncio.run(get_pool()); print('MySQL connected OK')"
```

- [ ] **Step 4: Commit**

```bash
git add D:/hragent/db/
git commit -m "feat: add MySQL connection pool and repository classes"
```

---

### Task 4: Routes 从 mock 改为 MySQL 读写

**Files:**
- Modify: `D:/hragent/backend/routes/auth_feishu.py`
- Modify: `D:/hragent/backend/routes/approval.py`
- Modify: `D:/hragent/backend/routes/knowledge.py`
- Modify: `D:/hragent/backend/routes/chat.py`

**Interfaces:**
- Consumes: `db.repositories.UserRepo`, `ConversationRepo`, `ApprovalRepo`, `KnowledgeRepo`
- Produces: 所有 API 端点从 MySQL 读写，失败时回退 mock

- [ ] **Step 1: 改 auth_feishu.py — 登录从 MySQL 查用户**

修改 `/api/auth/login` 端点，从 `MOCK_USERS` 字典改为 `UserRepo`：

```python
# D:/hragent/backend/routes/auth_feishu.py
# 在 login 函数中：

from db.repositories import UserRepo

_user_repo = UserRepo()

@router.post("/api/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """登录 — 优先 MySQL，失败回退 mock。"""
    # 尝试 MySQL
    try:
        user = await _user_repo.get_by_username(req.username)
        if user and user["password"] == req.password:
            token = create_token(req.username)
            return TokenResponse(
                access_token=token,
                user_id=user["id"],
                user_role=user["role"],
                display_name=user["display_name"],
            )
    except Exception:
        logger.warning("MySQL unavailable for login, falling back to mock")

    # fallback 到 mock
    user = MOCK_USERS.get(req.username)
    if not user or user["password"] != req.password:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_token(req.username)
    return TokenResponse(
        access_token=token,
        user_id=user["user_id"],
        user_role=user["role"],
        display_name=user["display_name"],
    )
```

- [ ] **Step 2: 改 approval.py — 从 ApprovalRepo 读写**

```python
# D:/hragent/backend/routes/approval.py
# 在文件顶部加:
from db.repositories import ApprovalRepo

_approval_repo = ApprovalRepo()

# list_approvals 改为:
@router.get("", response_model=list[ApprovalItem])
async def list_approvals(status: str = "", user: dict = Depends(get_current_user)):
    try:
        items = await _approval_repo.list_all(status)
    except Exception:
        logger.warning("MySQL unavailable, falling back to mock")
        items = MOCK_APPROVALS
        if status:
            items = [a for a in items if a["status"] == status]
    if user["role"] == "employee":
        items = [a for a in items if a.get("applicant_id") == user["user_id"]]
    return [ApprovalItem(**a) for a in items]

# pending_approvals 改为:
@router.get("/pending", response_model=list[ApprovalItem])
async def pending_approvals(user: dict = Depends(get_current_user)):
    if user["role"] not in ("hr_admin",):
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    try:
        items = await _approval_repo.list_all("pending")
    except Exception:
        items = [a for a in MOCK_APPROVALS if a["status"] == "pending"]
    return [ApprovalItem(**a) for a in items]

# action_approval 改为:
@router.post("/{approval_id}/action", response_model=ApprovalItem)
async def action_approval(approval_id: str, action: ApprovalAction, user: dict = Depends(get_current_user)):
    if user["role"] != "hr_admin":
        raise HTTPException(status_code=403, detail="仅 HR 管理员")
    new_status = "approved" if action.action == "approve" else "rejected"
    try:
        ok = await _approval_repo.update_status(approval_id, new_status, user["user_id"], action.comment)
        if ok:
            item = await _approval_repo.get_by_id(approval_id)
            return ApprovalItem(**item)
    except Exception:
        logger.warning("MySQL unavailable for approval action")
    # fallback mock
    for a in MOCK_APPROVALS:
        if a["id"] == approval_id:
            if a["status"] != "pending":
                raise HTTPException(status_code=400, detail="该审批已处理")
            a["status"] = new_status
            a["updated_at"] = datetime.now(timezone.utc).isoformat()
            return ApprovalItem(**a)
    raise HTTPException(status_code=404, detail=f"审批 {approval_id} 不存在")

# approval_stats 改为:
@router.get("/stats")
async def approval_stats(user: dict = Depends(get_current_user)):
    try:
        stats = await _approval_repo.get_stats()
        return stats
    except Exception:
        return {
            "pending": sum(1 for a in MOCK_APPROVALS if a["status"] == "pending"),
            "approved": sum(1 for a in MOCK_APPROVALS if a["status"] == "approved"),
            "rejected": sum(1 for a in MOCK_APPROVALS if a["status"] == "rejected"),
            "total": len(MOCK_APPROVALS),
        }
```

- [ ] **Step 3: 改 knowledge.py — 从 KnowledgeRepo 读写**

同样的 try/except 模式，优先 MySQL 失败回退 MOCK_DOCS。所有 6 个端点（list、categories、search、create、update、delete）改为 KnowledgeRepo。

- [ ] **Step 4: 改 chat.py — 对话持久化到 conversations 表**

在 `chat.py` 的 `chat_stream` 和 `chat_sync` 函数中，回复完成后调用 `ConversationRepo.save()`：

```python
from db.repositories import ConversationRepo

_conv_repo = ConversationRepo()

# 在两个函数中，reply/stream 完成后:
try:
    await _conv_repo.save(session_id=req.session_id, user_id=user_id, role="user", content=req.message)
    await _conv_repo.save(session_id=req.session_id, user_id=user_id, role="assistant", content=reply)
except Exception:
    logger.warning("Failed to save conversation to MySQL")
```

- [ ] **Step 5: 启动 FastAPI 测试**

```bash
cd D:/hragent && python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080 &
sleep 3
curl -s -X POST http://localhost:8080/api/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}'
```

Expected: 返回 token + user 信息

- [ ] **Step 6: 测试审批和知识库 API**

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/api/auth/login -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s http://localhost:8080/api/approvals -H "Authorization: Bearer $TOKEN" | python -m json.tool
curl -s http://localhost:8080/api/knowledge -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

- [ ] **Step 7: Commit**

```bash
git add D:/hragent/backend/routes/
git commit -m "feat: replace mock data with MySQL reads, with mock fallback"
```

---

### Task 5: Agent memory 模块 asyncpg → aiomysql

**Files:**
- Modify: `D:/hragent/agent/memory.py`

**Interfaces:**
- Consumes: `db.connection.get_pool`, `config.mysql`
- Produces: `MemoryManager` 使用 aiomysql 替代 asyncpg，接口不变

- [ ] **Step 1: 改 PostgresChatHistory → MySQLChatHistory**

```python
# D:/hragent/agent/memory.py
# 将 PostgresChatHistory 改为 MySQLChatHistory:

import aiomysql

class MySQLChatHistory:
    """MySQL 对话历史持久化，连接失败时静默降级。"""

    def __init__(self):
        self._available = True

    async def _get_pool(self):
        from db.connection import get_pool
        try:
            return await get_pool()
        except Exception:
            self._available = False
            return None

    async def save_message(self, session_id: str, user_id: int, role: str, content: str, intent: str = "", tool_calls: str = ""):
        if not self._available:
            return
        try:
            import uuid
            pool = await self._get_pool()
            if not pool:
                return
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "INSERT INTO conversations (id, session_id, user_id, role, content, intent) VALUES (%s, %s, %s, %s, %s, %s)",
                        (uuid.uuid4().hex, session_id, user_id, role, content, intent),
                    )
        except Exception:
            self._available = False
            logger.warning("MySQL unavailable, chat history not persisted")

    async def get_history(self, session_id: str, limit: int = 50) -> list[dict]:
        if not self._available:
            return []
        try:
            pool = await self._get_pool()
            if not pool:
                return []
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        "SELECT role, content, intent, created_at FROM conversations WHERE session_id = %s ORDER BY created_at DESC LIMIT %s",
                        (session_id, limit),
                    )
                    rows = await cur.fetchall()
                    return [dict(r) for r in reversed(rows)]
        except Exception:
            self._available = False
            return []

    async def close(self):
        pass  # pool 由 db.connection 管理
```

- [ ] **Step 2: 改 MemoryManager — 替换 PostgresChatHistory**

```python
# 在 MemoryManager.__init__ 中:
# self._chat_history = PostgresChatHistory(pg_dsn)   # 删除
# self._chat_history = MySQLChatHistory()             # 新增
```

- [ ] **Step 3: 删除不再使用的导入**

移除 `import asyncpg` 相关代码。

- [ ] **Step 4: 测试**

```bash
cd D:/hragent && python -c "
import asyncio
from agent.memory import memory_manager
from langchain_classic.memory import ConversationBufferWindowMemory

async def test():
    mem = memory_manager.create_summary_memory()
    await memory_manager.load_context('test-session', mem)
    print('Memory loaded OK')
    await memory_manager.save_turn('test-session', 1001, '你好', '你好！有什么可以帮你？')
    print('Memory saved OK')
    await memory_manager.close()

asyncio.run(test())
"
```

- [ ] **Step 5: Commit**

```bash
git add D:/hragent/agent/memory.py
git commit -m "refactor: switch agent memory from asyncpg to aiomysql"
```

---

### Task 6: SSE 真正流式输出

**Files:**
- Modify: `D:/hragent/agent/executor.py`
- Modify: `D:/hragent/backend/routes/chat.py`

**Interfaces:**
- Consumes: `HRAgent.chat_stream` 签名不变
- Produces: 前端收到逐 token 推送的 SSE 事件

- [ ] **Step 1: 修复 executor.py 的 chat_stream**

将 `chat_stream` 从一次性 ainvoke 改为真正的 token 级流式：

```python
# D:/hragent/agent/executor.py

async def chat_stream(
    self,
    user_message: str,
    session_id: str = "default",
    user_id: int = 0,
    user_role: str = "employee",
) -> AsyncIterator[str]:
    """流式对话 — 逐 token 产出回复。"""
    intent = await intent_classifier.classify(user_message)
    tools = route_tools(intent, self._all_tools)

    if intent.intent == "general_chat" or not tools:
        async for chunk in self._direct_reply_stream(user_message, user_role):
            yield chunk
        return

    # 构建 memory + prompt
    self._memory = self._memory_mgr.create_summary_memory()
    await self._memory_mgr.load_context(session_id, self._memory)

    agent = create_tool_calling_agent(
        llm=self._llm,
        tools=tools,
        prompt=self._build_prompt(user_role),
    )
    self._agent_executor = AgentExecutor(
        agent=agent,
        tools=tools,
        memory=self._memory,
        max_iterations=config.agent_max_iterations,
        verbose=True,
        handle_parsing_errors=True,
    )

    # 使用 astream_events 实现真正的 token 级流式
    full_output = ""
    async for event in self._agent_executor.astream_events(
        {"input": user_message},
        version="v2",
    ):
        kind = event.get("event", "")
        if kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            if chunk and hasattr(chunk, "content") and chunk.content:
                full_output += chunk.content
                yield chunk.content

    if not full_output:
        result = await self._agent_executor.ainvoke({"input": user_message})
        full_output = result.get("output", "")
        yield full_output

    # 持久化
    await self._memory_mgr.save_turn(
        session_id=session_id,
        user_id=user_id,
        user_message=user_message,
        assistant_message=full_output,
        intent=intent.intent,
    )
```

- [ ] **Step 2: 确认后端 SSE 路由正常**

`backend/routes/chat.py` 的 `/api/chat/stream` 端点不需改动——`EventSourceResponse` 包裹的 `event_generator` 已经是逐 token yield 的 SSE 推送。

- [ ] **Step 3: 测试流式响应**

```bash
cd D:/hragent && curl -s -X POST http://localhost:8080/api/chat/stream \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message":"公司年假有多少天","session_id":"test-stream","user_id":2001,"user_role":"hr_admin"}' \
  --no-buffer
```

- [ ] **Step 4: Commit**

```bash
git add D:/hragent/agent/executor.py
git commit -m "feat: real SSE token streaming via astream_events"
```

---

### Task 7: 飞书 Webhook 接入 Agent

**Files:**
- Modify: `D:/hragent/mcp_servers/feishu/event_handler.py`
- Modify: `D:/hragent/backend/routes/auth_feishu.py`

**Interfaces:**
- Consumes: `HRAgent.chat()`, `FeishuClient.send_message()`
- Produces: POST /feishu/webhook 收到 @Bot 消息 → Agent 回复 → 飞书 API 发送

- [ ] **Step 1: 改造 event_handler.py — 接入真实 Agent**

修改 `EventRouter._handle_message` 调用真实 `HRAgent.chat()`：

```python
# D:/hragent/mcp_servers/feishu/event_handler.py

async def _handle_message(self, event: FeishuEvent) -> FeishuReply | None:
    """处理文本消息 — 调用 Agent 真实对话。"""
    if not event.content.strip():
        return None

    # 调用 Agent
    from agent.executor import hr_agent
    if hr_agent._mcp_client is None:
        await hr_agent.start()

    # 用 sender_id 映射 user
    role = "employee"
    reply_text = await hr_agent.chat(
        user_message=event.content,
        session_id=f"feishu-{event.chat_id}",
        user_id=hash(event.sender_id) % 10000,
        user_role=role,
    )

    return FeishuReply(
        chat_id=event.chat_id,
        message_id=event.message_id,
        content=reply_text,
        content_type="text",
        at_sender=event.source_type == "group_chat",
    )
```

- [ ] **Step 2: 实现 _send_reply_via_api — 真正调飞书发消息**

修改 `event_handler.py` 末尾的 `_send_reply_via_api`：

```python
async def _send_reply_via_api(reply: FeishuReply):
    """通过飞书 API 发送回复消息。"""
    from mcp_servers.feishu.client import feishu_client

    if not feishu_client.is_configured:
        logger.warning("Feishu client not configured, reply not sent: %s", reply.content[:80])
        return

    body = {
        "content": json.dumps({"text": reply.content}, ensure_ascii=False),
        "msg_type": "text",
    }

    if reply.reply_to_message_id:
        # 回复指定消息
        await feishu_client.post(
            f"/im/v1/messages/{reply.reply_to_message_id}/reply",
            body=body,
        )
    else:
        # 发送到群聊/单聊
        await feishu_client.post(
            f"/im/v1/messages?receive_id_type=chat_id",
            body={**body, "receive_id": reply.chat_id},
        )
    logger.info("Feishu reply sent to chat=%s", reply.chat_id)
```

- [ ] **Step 3: 改造 auth_feishu.py 的 webhook 端点**

将占位实现改为调用 event_handler：

```python
# D:/hragent/backend/routes/auth_feishu.py

@router.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """飞书事件订阅 Webhook 端点。"""
    from mcp_servers.feishu.event_handler import handle_webhook

    try:
        body = await request.json()
        headers = dict(request.headers)
        result = await handle_webhook(body, headers)
        return result
    except Exception as e:
        logger.exception("Feishu webhook error")
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 4: 测试 Webhook 本地模拟**

```bash
# 模拟飞书 URL 验证
curl -s -X POST http://localhost:8080/feishu/webhook \
  -H "Content-Type: application/json" \
  -d '{"type":"url_verification","challenge":"test123"}'
# Expected: {"challenge":"test123"}

# 模拟消息事件
curl -s -X POST http://localhost:8080/feishu/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "header": {"event_type":"im.message.receive_v1","tenant_key":"test"},
    "event": {
      "sender":{"sender_id":{"open_id":"ou_test","name":"测试用户"}},
      "message":{
        "message_id":"msg_test",
        "chat_id":"oc_test",
        "chat_type":"single",
        "content":"{\"text\":\"公司年假有多少天\"}"
      }
    }
  }'
```

- [ ] **Step 5: Commit**

```bash
git add D:/hragent/mcp_servers/feishu/event_handler.py D:/hragent/backend/routes/auth_feishu.py
git commit -m "feat: wire feishu webhook to real Agent and send reply via API"
```

---

### Task 8: 前端 — 登录页 + SSE 流式对接

**Files:**
- Create: `D:/hragent/frontend/src/views/LoginView.vue`
- Modify: `D:/hragent/frontend/src/router/index.ts`
- Modify: `D:/hragent/frontend/src/views/ChatView.vue`
- Modify: `D:/hragent/frontend/src/App.vue`
- Modify: `D:/hragent/frontend/src/api/index.ts`

**Interfaces:**
- Consumes: `/api/auth/login`, `/api/chat/stream` (SSE)
- Produces: 用户可登录 → 进入对话页 → SSE 流式打字机效果

- [ ] **Step 1: 创建登录页**

```vue
<!-- D:/hragent/frontend/src/views/LoginView.vue -->
<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../api'
import { ElMessage } from 'element-plus'

const router = useRouter()
const username = ref('admin')
const password = ref('admin123')
const loading = ref(false)

const doLogin = async () => {
  loading.value = true
  try {
    const { data } = await login(username.value, password.value)
    localStorage.setItem('token', data.access_token)
    localStorage.setItem('user', JSON.stringify({ display_name: data.display_name, role: data.user_role, user_id: data.user_id }))
    ElMessage.success(`欢迎，${data.display_name}`)
    router.push('/chat')
  } catch {
    ElMessage.error('登录失败，请检查用户名和密码')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div style="display:flex;align-items:center;justify-content:center;height:100vh;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%)">
    <div style="width:400px;background:#fff;border-radius:12px;padding:40px;box-shadow:0 20px 60px rgba(0,0,0,0.15)">
      <div style="text-align:center;margin-bottom:32px">
        <div style="font-size:28px;font-weight:700;color:#303133">HR Agent</div>
        <div style="font-size:14px;color:#909399;margin-top:8px">智能 HR 助手 · 请登录</div>
      </div>
      <el-form @submit.prevent="doLogin">
        <el-form-item>
          <el-input v-model="username" placeholder="用户名" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="password" type="password" placeholder="密码" size="large" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" size="large" style="width:100%" :loading="loading" @click="doLogin">
            登 录
          </el-button>
        </el-form-item>
      </el-form>
      <div style="text-align:center;font-size:12px;color:#c0c4cc;margin-top:16px">
        测试账号: admin / admin123
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 2: 添加登录路由**

```typescript
// D:/hragent/frontend/src/router/index.ts
// 在 routes 数组最前面加:
{ path: '/login', name: 'Login', component: () => import('../views/LoginView.vue'), meta: { title: '登录', hideNav: true } },
```

添加路由守卫：无 token 时跳转登录页。

```typescript
// 在 createRouter 之后加入:
router.beforeEach((to, _from) => {
  const token = localStorage.getItem('token')
  if (!token && to.path !== '/login') {
    return '/login'
  }
  if (token && to.path === '/login') {
    return '/chat'
  }
})
```

- [ ] **Step 3: 前端 API 加 SSE 流式函数**

```typescript
// D:/hragent/frontend/src/api/index.ts
// 添加:
export const chatStream = (
  message: string,
  sessionId: string,
  onToken: (text: string) => void,
  onDone: () => void,
  onError: (err: string) => void
) => {
  const token = localStorage.getItem('token')
  fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
    body: JSON.stringify({ message, session_id: sessionId, user_id: 2001, user_role: 'hr_admin' }),
  }).then(async (res) => {
    const reader = res.body?.getReader()
    if (!reader) return onError('No response body')
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('data:')) {
          try {
            const data = JSON.parse(line.slice(5).trim())
            if (data.text) onToken(data.text)
          } catch {}
        }
        if (line.startsWith('event:done')) onDone()
      }
    }
  }).catch((err) => onError(err.message))
}
```

- [ ] **Step 4: 改 ChatView.vue — 使用 SSE 流式**

将 `send()` 函数从 `chatSync` POST 改为 `chatStream` SSE：

```typescript
// 关键改动: send 函数中用 chatStream 替换 chatSync
const send = async () => {
  const text = input.value.trim()
  if (!text || loading.value) return

  messages.value.push({ role: 'user', content: text, time: now() })
  input.value = ''
  scrollBottom()
  loading.value = true

  const agentMsg: Message = { role: 'agent', content: '', time: now() }
  messages.value.push(agentMsg)
  const idx = messages.value.length - 1

  chatStream(
    text,
    'web-' + Date.now(),
    (token: string) => {
      messages.value[idx].content += token
      scrollBottom()
    },
    () => {
      loading.value = false
      scrollBottom()
    },
    (err: string) => {
      messages.value[idx].content = `请求失败: ${err}`
      loading.value = false
    }
  )
}
```

- [ ] **Step 5: 改 App.vue — 支持退出登录**

在侧边栏底部用户信息区域增加退出按钮：

```vue
<!-- 在 user info div 中加: -->
<div style="margin-top:4px;cursor:pointer;color:var(--el-color-primary)" @click="logout">退出登录</div>
```

添加 logout 方法：
```typescript
const logout = () => {
  localStorage.clear()
  router.push('/login')
}
```

- [ ] **Step 6: 测试前端**

```bash
cd D:/hragent/frontend && npm run dev
```

浏览器打开后验证: 自动跳转登录页 → 登录 → 进对话页 → 提问 → 打字机效果逐字输出。

- [ ] **Step 7: Commit**

```bash
git add D:/hragent/frontend/src/
git commit -m "feat: add login page, SSE streaming chat, logout, auth guard"
```

---

### Task 9: 端到端验证

**Files:**
- None (验证任务)

- [ ] **Step 1: 启动完整服务**

```bash
cd D:/hragent
# 确保 MySQL 运行
mysql -u root -e "SELECT 1"

# 启动后端
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080 &

# 启动前端
cd frontend && npm run dev &
```

- [ ] **Step 2: 验证清单**

| 测试项 | 方法 | 预期 |
|--------|------|------|
| 登录 | curl POST /api/auth/login | 返回 token |
| 对话(同步) | curl POST /api/chat | 返回完整回复 |
| 对话(流式) | curl POST /api/chat/stream | SSE 逐 token |
| 审批列表 | curl GET /api/approvals | 4条审批数据 |
| 审批操作 | curl POST /api/approvals/APR-001/action | 状态变更 |
| 知识库 | curl GET /api/knowledge | 6篇文档 |
| 飞书 Webhook 验证 | curl POST /feishu/webhook with challenge | 返回 challenge |
| 飞书 Webhook 消息 | curl POST /feishu/webhook with message event | Agent 回复 |
| 前端登录 | 浏览器访问 → 登录 | 进入对话页 |
| 前端对话(SSE) | 输入问题发送 | 打字机流式输出 |

- [ ] **Step 3: Commit final state**

```bash
git add -A
git commit -m "chore: end-to-end verification passed, Phase 2 complete"
```
