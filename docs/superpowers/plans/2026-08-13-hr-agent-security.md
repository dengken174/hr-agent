# HR Agent 安全与权限精细化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 HR Agent 补齐数据访问控制三件套：DataScope 权限判定、薪资分级脱敏、审计日志，把 executor 的 `_apply_identity_guard` 升级为 `_apply_scope_guard`。

**Architecture:** 新建 `db/scope.py`（DataScope + mask_salary + authorize，纯逻辑可测）和 `db/audit.py`（AuditRepo），加 `audit_log` 表。scope guard 在工具调用前判定权限（越权拒绝并审计）、调用后审计成功。脱敏落在 `get_team_members`（薪资区间）；审批数据权限下沉到 `ApprovalRepo.list_all`。

**Tech Stack:** Python 3.13, pytest 9.1, aiomysql, FastAPI

## Global Constraints

- 运行环境 Python 3.13.11，pytest 9.1.1，测试命令 `python -m pytest`
- **不迁数据**：薪资/员工数据在 mock `MOCK_EMPLOYEES`（内存），不在 MySQL
- **role 级权限**：`employee` 只看自己、`hr_admin` 看全部、`interviewer` 看公开；不做 org 级细分
- role 取值：`employee` / `hr_admin` / `interviewer`
- 身份绑定**保留**：非 `hr_admin` 强制 identity 参数（`employee_id`/`applicant_id`/`operator_id`/`assignee_id`）= `user_id`（现有 `_apply_identity_guard` 行为不破坏）
- 审计写入失败降级 `logger.warning`，不抛异常、不阻塞主流程
- `db/scope.py` 顶层只 import 标准库 + `dataclasses`（保证可轻量单测）
- 提交信息沿用项目风格 `feat:` / `fix:` / `docs:`

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `db/scope.py` | 新建 | `DataScope` + `mask_salary` + `authorize`（纯逻辑） |
| `db/audit.py` | 新建 | `AuditRepo`（`record` + `_build_insert`） |
| `db/schema.sql` | 修改 | 加 `audit_log` 表 |
| `agent/executor.py` | 修改 | `_apply_identity_guard` → `_apply_scope_guard` |
| `mcp_servers/hris/tools.py` | 修改 | `get_team_members` 加薪资区间 |
| `db/repositories.py` | 修改 | `ApprovalRepo.list_all` 加 scope 参数 |
| `backend/routes/approval.py` | 修改 | 构造 DataScope，移除 Python 层过滤 |
| `tests/test_scope.py` | 新建 | DataScope / mask_salary / authorize 测试 |
| `tests/test_audit.py` | 新建 | `_build_insert` 测试 |

---

## Task 1: DataScope + mask_salary + authorize（纯逻辑）

**Files:**
- Create: `db/scope.py`
- Test: `tests/test_scope.py`

**Interfaces:**
- Produces（后续任务依赖）:
  - `DataScope(user_id: int, role: str)`，方法 `can_read_salary(emp_id) -> bool`、`can_search_others() -> bool`、`can_view_approval(applicant_id, assignee_id) -> bool`
  - `mask_salary(amount: float, context: str) -> str`（context: `"self"`/`"list"`/`"export"`）
  - `authorize(scope: DataScope, tool_name: str, kwargs: dict) -> bool`

- [ ] **Step 1: 写失败测试**

Create `tests/test_scope.py`:

```python
from db.scope import DataScope, mask_salary, authorize


def test_can_read_salary_self():
    assert DataScope(1001, "employee").can_read_salary(1001) is True


def test_can_read_salary_other_denied():
    assert DataScope(1001, "employee").can_read_salary(1002) is False


def test_can_read_salary_hr_any():
    assert DataScope(2001, "hr_admin").can_read_salary(1001) is True


def test_can_search_others_employee_denied():
    assert DataScope(1001, "employee").can_search_others() is False


def test_can_search_others_hr():
    assert DataScope(2001, "hr_admin").can_search_others() is True


def test_can_view_approval_employee_own():
    assert DataScope(1001, "employee").can_view_approval(1001, None) is True


def test_can_view_approval_employee_assignee():
    assert DataScope(1001, "employee").can_view_approval(2002, 1001) is True


def test_can_view_approval_employee_other_denied():
    assert DataScope(1001, "employee").can_view_approval(2002, 3003) is False


def test_can_view_approval_hr():
    assert DataScope(2001, "hr_admin").can_view_approval(9999, 9999) is True


def test_mask_salary_list_range():
    assert mask_salary(28000, "list") == "25k-30k"


def test_mask_salary_self_plaintext():
    assert mask_salary(28000, "self") == "28000"


def test_mask_salary_export_masked():
    assert mask_salary(28000, "export") == "****"


def test_authorize_search_employee_hr_only():
    assert authorize(DataScope(2001, "hr_admin"), "hris_search_employee", {}) is True
    assert authorize(DataScope(1001, "employee"), "hris_search_employee", {}) is False


def test_authorize_get_team_members_hr_only():
    assert authorize(DataScope(1001, "employee"), "hris_get_team_members", {"dept": "技术部"}) is False


def test_authorize_public_tool_always_allowed():
    assert authorize(DataScope(1001, "employee"), "knowledge_search_knowledge_base", {}) is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/hragent && python -m pytest tests/test_scope.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'db.scope'`

- [ ] **Step 3: 写实现**

Create `db/scope.py`:

```python
"""数据访问控制：DataScope 权限判定 + 薪资脱敏 + 工具授权。纯逻辑，可单测。"""

from dataclasses import dataclass


@dataclass
class DataScope:
    user_id: int
    role: str   # "employee" | "hr_admin" | "interviewer"

    def can_read_salary(self, emp_id: int) -> bool:
        if self.role == "hr_admin":
            return True
        return emp_id == self.user_id

    def can_search_others(self) -> bool:
        return self.role == "hr_admin"

    def can_view_approval(self, applicant_id: int, assignee_id: int | None) -> bool:
        if self.role == "hr_admin":
            return True
        return self.user_id in (applicant_id, assignee_id)


def mask_salary(amount: float, context: str) -> str:
    if context == "self":
        return f"{amount:.0f}"
    if context == "list":
        lower = int(amount // 5000) * 5000
        return f"{lower//1000}k-{(lower+5000)//1000}k"
    return "****"


# 查别人类工具：仅 HR 可用（其余工具靠身份绑定 + 工具内部逻辑）
_OTHERS_TOOLS = {"hris_search_employee", "hris_get_team_members"}


def authorize(scope: DataScope, tool_name: str, kwargs: dict) -> bool:
    if tool_name in _OTHERS_TOOLS:
        return scope.can_search_others()
    return True
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd /d/hragent && python -m pytest tests/test_scope.py -v`
Expected: PASS（15 passed）

- [ ] **Step 5: Commit**

```bash
git add db/scope.py tests/test_scope.py
git commit -m "feat: DataScope permission + salary masking + tool authorization"
```

---

## Task 2: audit_log 表 + AuditRepo

**Files:**
- Modify: `db/schema.sql`
- Create: `db/audit.py`
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: `db/connection.get_pool`（现有）
- Produces:
  - `AuditRepo.record(user_id, user_role, action, result, detail=None, resource_type="", resource_id="")` — async，内部 try/except 降级
  - `AuditRepo._build_insert(...) -> tuple[str, tuple]` — 同步，构造 SQL 与参数（可测）
  - 模块级单例 `audit_repo`

- [ ] **Step 1: 写失败测试**

Create `tests/test_audit.py`:

```python
from db.audit import AuditRepo


def test_build_insert_contains_columns():
    sql, params = AuditRepo._build_insert(
        1001, "employee", "read_salary", "denied", {"employee_id": 1002},
        "salary", "1002",
    )
    assert "INSERT INTO audit_log" in sql
    assert params[0] == 1001          # user_id
    assert params[1] == "employee"    # user_role
    assert params[2] == "read_salary" # action
    assert params[3] == "salary"      # resource_type
    assert params[4] == "1002"        # resource_id
    assert params[6] == "denied"      # result


def test_build_insert_detail_json():
    sql, params = AuditRepo._build_insert(
        1001, "employee", "read_salary", "success", {"employee_id": 1001},
        "salary", "1001",
    )
    # detail 是 JSON 字符串（index 5）
    import json
    assert json.loads(params[5]) == {"employee_id": 1001}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd /d/hragent && python -m pytest tests/test_audit.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'db.audit'`

- [ ] **Step 3: 加 audit_log 表**

Modify `db/schema.sql`，在文件末尾追加：

```sql
-- 审计日志（通用数据访问审计）
CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT NOT NULL,
    user_role     VARCHAR(16) NOT NULL,
    action        VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50) DEFAULT '',
    resource_id   VARCHAR(50) DEFAULT '',
    detail        JSON DEFAULT NULL,
    result        ENUM('success','denied','masked') NOT NULL,
    created_at    DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 4: 写 AuditRepo**

Create `db/audit.py`:

```python
"""审计日志仓库 — 记录数据访问行为（成功/拒绝/脱敏）。失败降级，不阻塞主流程。"""

import json
import logging

from db.connection import get_pool

logger = logging.getLogger(__name__)


class AuditRepo:
    @staticmethod
    def _build_insert(user_id, user_role, action, result, detail=None,
                      resource_type="", resource_id=""):
        sql = (
            "INSERT INTO audit_log "
            "(user_id, user_role, action, resource_type, resource_id, detail, result) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)"
        )
        params = (user_id, user_role, action, resource_type, resource_id,
                  json.dumps(detail or {}, ensure_ascii=False), result)
        return sql, params

    async def record(self, user_id, user_role, action, result, detail=None,
                     resource_type="", resource_id=""):
        try:
            sql, params = self._build_insert(
                user_id, user_role, action, result, detail, resource_type, resource_id)
            pool = await get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, params)
        except Exception:
            logger.warning("Audit record failed: %s %s user=%s", action, result, user_id)


audit_repo = AuditRepo()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd /d/hragent && python -m pytest tests/test_audit.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: 冒烟 import**

Run: `cd /d/hragent && python -c "from db.audit import AuditRepo, audit_repo; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 7: Commit**

```bash
git add db/audit.py db/schema.sql tests/test_audit.py
git commit -m "feat: audit log table + AuditRepo"
```

---

## Task 3: 脱敏落点（get_team_members 薪资区间）

**Files:**
- Modify: `mcp_servers/hris/tools.py`

**Interfaces:**
- Consumes: `mask_salary(amount, context)`（Task 1）
- Produces: `get_team_members` 返回列表每项含 `salary_range` 字段

- [ ] **Step 1: 修改 get_team_members**

Modify `mcp_servers/hris/tools.py`，在文件顶部 import 加 `from db.scope import mask_salary`，并把 `get_team_members` 改为：

```python
async def get_team_members(dept: str) -> list[types.TextContent]:
    """HR 查看管辖范围内的员工列表（薪资以区间脱敏展示）。"""
    members = [{
        "id": e["id"], "name": e["name"], "title": e["title"],
        "onboard_date": e["onboard_date"], "level": e["level"],
        "salary_range": mask_salary(e["salary"], "list"),
    } for e in MOCK_EMPLOYEES if dept in e["dept"]]
    return [types.TextContent(type="text", text=str(members))]
```

- [ ] **Step 2: 冒烟 import**

Run: `cd /d/hragent && python -c "from mcp_servers.hris.tools import get_team_members; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 3: 运行相关测试确认无回归**

Run: `cd /d/hragent && python -m pytest tests/test_scope.py -v`
Expected: PASS（mask_salary 仍 15 passed）

- [ ] **Step 4: Commit**

```bash
git add mcp_servers/hris/tools.py
git commit -m "feat: mask salary as range in team member list"
```

---

## Task 4: scope guard 升级（executor.py）

**Files:**
- Modify: `agent/executor.py`

**Interfaces:**
- Consumes: `DataScope`、`authorize`（Task 1）、`audit_repo`（Task 2）
- Produces: `_apply_scope_guard(tools, scope, audit) -> list`（替代 `_apply_identity_guard`）

- [ ] **Step 1: 修改 import 与 guard**

Modify `agent/executor.py`，顶部 import 加：

```python
from db.scope import DataScope, authorize
from db.audit import audit_repo
```

把 `_apply_identity_guard` 函数整体替换为 `_apply_scope_guard`（保留身份绑定逻辑，新增权限判定 + 审计）：

```python
def _apply_scope_guard(tools: list[BaseTool], scope: DataScope, audit) -> list[BaseTool]:
    """身份绑定（保留）+ 权限判定（新增）+ 审计（新增）。"""
    wrapped = []
    for t in tools:
        _orig_arun = t._arun

        async def _guarded(**kwargs: Any) -> Any:
            # 1. 身份绑定（保留现有行为）：非 admin 强制 identity 参数 = user_id
            if scope.role != "hr_admin":
                for param in _IDENTITY_PARAMS:
                    if param in kwargs:
                        kwargs[param] = scope.user_id
            # 2. 权限判定：查别人工具，越权拒绝 + 审计
            action = t.name.split("_", 1)[-1] if "_" in t.name else t.name
            if not authorize(scope, t.name, kwargs):
                await audit.record(scope.user_id, scope.role, action, "denied", dict(kwargs))
                return "⚠️ 你没有权限执行此操作"
            # 3. 执行 + 审计成功
            result = await _orig_arun(**kwargs)
            await audit.record(scope.user_id, scope.role, action, "success", dict(kwargs))
            return result

        t._arun = _guarded
        wrapped.append(t)
    return wrapped
```

- [ ] **Step 2: 更新调用点**

Modify `agent/executor.py` 的 `chat()` 与 `chat_stream()`，把两处：

```python
tools = _apply_identity_guard(tools, user_id, user_role)
```

替换为：

```python
scope = DataScope(user_id=user_id, role=user_role)
tools = _apply_scope_guard(tools, scope, audit_repo)
```

（两个方法各替换一次；`_apply_identity_guard` 函数定义删除，避免死代码。）

- [ ] **Step 3: 冒烟 import**

Run: `cd /d/hragent && python -c "from agent.executor import HRAgent, _apply_scope_guard; from db.scope import DataScope; print('import ok')"`
Expected: 输出 `import ok`

- [ ] **Step 4: 运行全套单测确认无回归**

Run: `cd /d/hragent && python -m pytest tests/ -v`
Expected: PASS（scope 15 + audit 2 + slot_filler 13 + refusal 6 + tracing 4 = 40 passed）

- [ ] **Step 5: Commit**

```bash
git add agent/executor.py
git commit -m "feat: scope guard with permission check + audit"
```

---

## Task 5: ApprovalRepo 数据权限下沉

**Files:**
- Modify: `db/repositories.py`
- Modify: `backend/routes/approval.py`

**Interfaces:**
- Consumes: `DataScope`（Task 1）
- Produces: `ApprovalRepo.list_all(status="", scope=None)` 支持按 scope 过滤

- [ ] **Step 1: 修改 ApprovalRepo.list_all**

Modify `db/repositories.py`，顶部 import 加 `from db.scope import DataScope`，`list_all` 改为：

```python
    async def list_all(self, status: str = "", scope: DataScope | None = None) -> list[dict]:
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                where = []
                params = []
                if status:
                    where.append("status = %s")
                    params.append(status)
                if scope and scope.role == "employee":
                    where.append("applicant_id = %s")
                    params.append(scope.user_id)
                where_clause = (" WHERE " + " AND ".join(where)) if where else ""
                await cur.execute(
                    f"SELECT * FROM approval_requests{where_clause} ORDER BY created_at DESC",
                    tuple(params),
                )
                rows = await cur.fetchall()
                for r in rows:
                    if isinstance(r.get("body"), str):
                        r["body"] = json.loads(r["body"])
                    if r.get("created_at"):
                        r["created_at"] = r["created_at"].strftime('%Y-%m-%dT%H:%M:%S')
                    if r.get("updated_at"):
                        r["updated_at"] = r["updated_at"].strftime('%Y-%m-%dT%H:%M:%S')
                return rows
```

- [ ] **Step 2: 修改 routes 构造 DataScope**

Modify `backend/routes/approval.py`，顶部 import 加 `from db.scope import DataScope`，并把 `list_approvals` 里的两处改为：

```python
    scope = DataScope(user_id=user["user_id"], role=user["role"])
    rows = await _approval_repo.list_all(status, scope)
```

并移除 `list_approvals` 末尾的 Python 层过滤：

```python
    if user["role"] == "employee":
        items = [a for a in items if a["applicant_id"] == user["user_id"]]
```

（`pending_approvals` 已要求 hr_admin，无需 scope 过滤，保持原样。）

- [ ] **Step 3: 冒烟 import**

Run: `cd /d/hragent && python -c "from backend.routes.approval import router; from db.repositories import ApprovalRepo; print('import ok')"`
Expected: 输出 `import ok`

- [ ] **Step 4: 运行全套单测确认无回归**

Run: `cd /d/hragent && python -m pytest tests/ -v`
Expected: PASS（40 passed）

- [ ] **Step 5: Commit**

```bash
git add db/repositories.py backend/routes/approval.py
git commit -m "feat: push approval data scope down to repository layer"
```

---

## 自审记录

- **Spec 覆盖**：DataScope（Task 1）、脱敏（Task 1 mask_salary + Task 3 落点）、审计（Task 2 + Task 4）、ApprovalRepo 下沉（Task 5）均有用任务覆盖；身份绑定保留（Task 4 明确）。
- **占位符**：无 TBD/TODO，所有步骤含实际代码。
- **类型一致性**：`DataScope` / `mask_salary` / `authorize` 签名在 Task 1 定义，Task 3/4/5 引用一致；`AuditRepo._build_insert` 参数顺序在 Task 2 定义，测试按此断言；`audit_repo` 单例在 Task 2 定义，Task 4 引用一致。
