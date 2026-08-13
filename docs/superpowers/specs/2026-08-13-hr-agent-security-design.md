# HR Agent 安全与权限精细化 设计方案

日期: 2026-08-13

## 概述

为 HR Agent 补齐「数据访问控制」三件套：数据权限（DataScope）、敏感信息脱敏、审计日志。核心是把 executor 现有零散的 `_apply_identity_guard` 升级为统一的 scope guard，在工具调用层一处完成「权限判定 + 脱敏 + 审计」。

范围决策（已与用户确认）：
- **不迁数据**：薪资/员工数据保持在 mock HRIS 内存，审批数据在 MySQL。
- **role 级权限**：employee 只看自己、hr_admin 看全部、interviewer 看公开，不做 org 级细分。

## 现状分析

| 现状 | 问题 |
|------|------|
| 权限只有 `_apply_identity_guard`（executor 层，强制 `employee_id=user_id`） | 「强制改参数」而非「判定拒绝」，权限逻辑散落、不可单测 |
| `get_my_salary` 本人明文、`search_employee`/`get_team_members` 不含薪资 | 缺「列表给区间」这一档分级脱敏 |
| 无通用审计日志（只有审批专用的 `approval_logs`） | 薪资/员工查询无留痕，越权尝试不记录 |
| `ApprovalRepo.list_all`（MySQL）无过滤，`routes/approval.py` 在 Python 层过滤 employee | 应用层过滤而非数据层强制 |

### 关键架构事实

- 薪资/员工数据在 `mcp_servers/hris/tools.py` 的 `MOCK_EMPLOYEES`（内存），不在 MySQL。
- 审批有两条路径：MCP 工具走 `approval_sm`（SQLite，已按 applicant/assignee 过滤）；API 走 `ApprovalRepo`（MySQL，`list_all` 无过滤，routes 层 Python 过滤）。
- `get_current_user` 返回 `{"user_id": int, "role": str}`，role 取值 `employee` / `hr_admin` / `interviewer`。
- executor 的 `chat(user_message, session_id, user_id, user_role)` 已携带 `user_id` + `user_role`，可直接构造 DataScope。
- executor 在主进程（backend），可访问 MySQL（`db/connection.get_pool`），因此 scope guard 内可 `await` 审计写入。

## 整体架构

```
chat(user_id, user_role)
  → DataScope(user_id, role)
  → 工具路由
  → _apply_scope_guard(tools, scope)   # 升级 _apply_identity_guard
      调用前：scope 判定 → 越权则审计(denied) + 拒绝
      调用后：审计(success/masked)
  → Agent 执行
```

## 1. DataScope（数据权限核心）

新建 `db/scope.py`（纯逻辑，可单测）：

```python
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
```

**与现有 identity guard 的关系**：现有 `_apply_identity_guard` 是「非 admin 强制 employee_id=user_id」，方向 4 升级为 scope guard——**改为「调用前用 DataScope 判定，越权直接拒绝并审计」**，权限逻辑集中到 `DataScope` 一处。

## 2. 薪资脱敏（分级）

### 脱敏函数（`db/scope.py` 内，纯逻辑）

```python
def mask_salary(amount: float, context: str) -> str:
    # context: "self" 详情明文 | "list" 列表区间 | "export" 导出掩码
    if context == "self":
        return f"{amount:.0f}"
    if context == "list":
        lower = int(amount // 5000) * 5000
        return f"{lower//1000}k-{(lower+5000)//1000}k"   # 28000 → "25k-30k"
    return "****"
```

### 落点：按工具语义脱敏，不依赖 role

| 工具 | 场景 | 薪资形态 |
|------|------|---------|
| `get_my_salary` | 详情（本人） | 明文（scope guard 已保证是本人） |
| `get_team_members` | 列表（HR 查部门） | 区间（新增 `salary_range` 字段） |

「employee 调不了 HR 工具」由 scope guard 保证，脱敏函数本身不需要猜 role。

## 3. 审计日志

### 表设计（`db/schema.sql` 新增）

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT NOT NULL,
    user_role     VARCHAR(16) NOT NULL,
    action        VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50),
    resource_id   VARCHAR(50),
    detail        JSON,
    result        ENUM('success','denied','masked') NOT NULL,
    created_at    DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 落点：scope guard 层

```python
async def _guarded(**kwargs):
    action = _tool_action(t.name)               # 工具名 → action 类型（read_salary/search_employee/approve...）
    if not _authorize(scope, t.name, kwargs):   # 工具名 → DataScope 具体方法（见下）
        await audit.record(user_id, role, action, result="denied", detail=kwargs)
        return "⚠️ 你没有权限执行此操作"
    result = await _orig_arun(**kwargs)
    await audit.record(user_id, role, action, result="success", detail=kwargs)
    return result
```

`_authorize` 是「工具名 → DataScope 方法」的映射（实现细节，落在 scope guard 内）：

```python
def _authorize(scope, tool_name, kwargs) -> bool:
    if tool_name == "hris_get_my_salary":
        return scope.can_read_salary(kwargs.get("employee_id", -1))
    if tool_name in ("hris_search_employee", "hris_get_team_members"):
        return scope.can_search_others()
    if tool_name.startswith("approval_"):
        return scope.can_view_approval(
            kwargs.get("applicant_id"), kwargs.get("assignee_id"))
    return True   # 公开查询（知识库、公司简介）不限制
```

### 三个关键点

1. **记录「拒绝」** —— 越权尝试本身就是高危信号，`result='denied'` 必须落库。
2. **审计范围**：薪资查询、员工搜索、审批操作每次必记；公开查询（公司简介）不记。
3. **审计不阻塞主流程**：`await` 写入，失败降级 `logger.warning`。

## 4. ApprovalRepo 数据权限下沉（附带修复）

现状 `routes/approval.py` 在 Python 层过滤 employee（先 `list_all()` 查全部，再 `[a for a in items if ...]`）。方向 4 下沉到数据层：

```python
# db/repositories.py
async def list_all(self, status: str = "", scope: DataScope | None = None) -> list[dict]:
    where = []
    params = []
    if status:
        where.append("status = %s"); params.append(status)
    if scope and scope.role == "employee":
        where.append("applicant_id = %s"); params.append(scope.user_id)
    # hr_admin 不加过滤；interviewer 无审批权限
```

`routes/approval.py` 构造 `DataScope(user["user_id"], user["role"])` 传入，移除 Python 层过滤。

## 涉及文件

| 文件 | 操作 | 职责 |
|------|------|------|
| `db/scope.py` | 新建 | `DataScope` + `mask_salary`（纯逻辑） |
| `db/audit.py` | 新建 | `AuditRepo` 记录审计 |
| `db/schema.sql` | 修改 | 加 `audit_log` 表 |
| `agent/executor.py` | 修改 | `_apply_identity_guard` → `_apply_scope_guard` |
| `mcp_servers/hris/tools.py` | 修改 | `get_team_members` 加薪资区间 |
| `db/repositories.py` | 修改 | `ApprovalRepo.list_all` 加 scope 参数 |
| `backend/routes/approval.py` | 修改 | 构造 DataScope 传入，移除 Python 过滤 |
| `tests/` | 新建 | `test_scope.py` / `test_audit.py` |

## 测试计划

| 场景 | 输入 | 预期 |
|------|------|------|
| 权限-员工查自己薪资 | `scope=DataScope(1001, "employee").can_read_salary(1001)` | True |
| 权限-员工查他人薪资 | `can_read_salary(1002)` | False |
| 权限-HR 查任意薪资 | `scope=DataScope(2001, "hr_admin").can_read_salary(1001)` | True |
| 脱敏-列表区间 | `mask_salary(28000, "list")` | `"25k-30k"` |
| 脱敏-本人明文 | `mask_salary(28000, "self")` | `"28000"` |
| 脱敏-导出掩码 | `mask_salary(28000, "export")` | `"****"` |
| 审计-越权记录 | scope guard 拒绝 → `result='denied'` 落库 | audit_log 有 denied 记录 |
| 审批-员工过滤 | `list_all(scope=employee)` | 只返回 applicant_id=me 的行 |

## 已知限制

1. 薪资数据仍在 mock 内存，权限/脱敏是「逻辑真实、数据 mock」，迁移 MySQL 后逻辑可复用。
2. role 级权限，未做 org 级细分（hr_admin 看全部）。
3. 审计日志表无外键约束，`user_id`/`resource_id` 为弱引用（demo 简化）。
