# Phase 3: 前端补全 — Dashboard + 会话管理 + Skill Manager 接入

## 目标

补齐前端缺失功能，使系统完整可演示。不改动现有 Agent/后端核心逻辑。

## 一、Dashboard 首页

### 路由
- `GET /api/stats` — 后端已有，返回 `StatsResponse`
- 前端新增 `/dashboard` 路由，替代 `/chat` 为默认首页

### 页面结构
```
┌──────────────────────────────────────────────┐
│  统计卡片行                                     │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐  │
│  │ 总对话  │ │ 活跃Skill│ │ 待审批  │ │ 知识文档 │  │
│  │   0    │ │   3    │ │   2    │ │   6    │  │
│  └────────┘ └────────┘ └────────┘ └────────┘  │
│                                                │
│  ┌──────────────────┐ ┌──────────────────┐     │
│  │ 对话趋势 (折线图) │ │ 审批分布 (饼图)   │     │
│  └──────────────────┘ └──────────────────┘     │
└──────────────────────────────────────────────┘
```

### 数据
- 统计卡片：直接消费 `/api/stats`
- 折线图：模拟近 7 天趋势（后端暂无此接口，用 `mockRecentTrend()` 生成）
- 饼图：消费 `/api/approvals/stats`（pending/approved/rejected 占比）

### 文件
- 新建 `frontend/src/views/DashboardView.vue`
- 修改 `frontend/src/router/index.ts` — 加路由 + 默认重定向改 `/dashboard`
- 修改 `frontend/src/App.vue` — sidebar 加 Dashboard 菜单项（置顶）

## 二、会话管理器

### 后端新增

`GET /api/chat/sessions` — 返回用户的所有会话列表：
```json
[
  {
    "session_id": "abc123",
    "title": "公司年假有多少天？",
    "message_count": 6,
    "updated_at": "2026-08-07T10:30:00Z"
  }
]
```

`GET /api/chat/history?session_id=xxx` — 返回指定会话的对话历史：
```json
[
  {"role": "user", "content": "你好", "created_at": "..."},
  {"role": "assistant", "content": "你好！...", "created_at": "..."}
]
```

### 数据库
- `conversations` 表已有 `session_id` 字段，后端路由从 MySQL `ConversationRepo` 查询
- 新增 `ConversationRepo.list_sessions(user_id)` 方法：GROUP BY session_id 取最近一条标题

### 前端改造 ChatView

```
┌──────┬──────────────────────────────┐
│ 会话  │  对话区域                      │
│ 列表  │                               │
│      │                               │
│ [+新]│  ┌───────────────────────┐    │
│──────│  │ 消息气泡...            │    │
│ 年假  │  └───────────────────────┘    │
│ 政策  │                               │
│ 6条   │  ┌───────────────────────┐    │
│──────│  │ 消息气泡...            │    │
│ 五险  │  └───────────────────────┘    │
│ 一金  │                               │
│ 3条   │                               │
│──────│  ┌─────────────────────────┐  │
│ 入职  │  │ 输入框            [发送] │  │
│ 流程  │  └─────────────────────────┘  │
└──────┴──────────────────────────────┘
```

### 交互
- 会话列表宽度 220px，可点击左侧箭头收起/展开
- 每条显示：标题（取第一条 user 消息前 20 字）+ 消息数 + 时间
- 点击切换 → `GET /api/chat/history` 加载历史到 `chatStore.messages`
- 「+ 新对话」→ 生成新 `session_id`（`uuid`），清空消息区
- 当前选中会话高亮
- 删除会话功能暂不做（避免误操作）

### 文件
- 修改 `backend/routes/chat.py` — 新增 2 个路由
- 修改 `db/repositories.py` — 新增 `list_sessions` 方法
- 修改 `frontend/src/views/ChatView.vue` — 加侧栏
- 修改 `frontend/src/stores/chat.ts` — 加 `sessions` 状态 + `loadHistory` action

## 三、Skill Manager 接入

### 路由
- `/skills` 路由已存在组件 `SkillManager.vue`，只需注册路由

### Sidebar
- 加「技能管理」菜单项，仅 `hr_admin` 角色可见
- 使用 `v-if="userStore.isAdmin"` 控制

### 文件
- 修改 `frontend/src/router/index.ts` — 加 `/skills` 路由
- 修改 `frontend/src/App.vue` — sidebar 加菜单项 + 权限控制

## 四、不涉及的

- 飞书 CLI MCP 整合（放到 Phase 4 飞书 Bot 阶段）
- 用户管理 / 真实 JWT（放到后续）
- 真实 RAGAS 评测（保持 mock）
- 会话删除功能
- 后端 API 的大规模重构

## 五、文件改动汇总

| 文件 | 操作 | 说明 |
|------|------|------|
| `frontend/src/views/DashboardView.vue` | **新建** | Dashboard 页面 |
| `frontend/src/views/ChatView.vue` | 修改 | 加会话列表侧栏 |
| `frontend/src/router/index.ts` | 修改 | 加 /dashboard、/skills 路由 |
| `frontend/src/App.vue` | 修改 | sidebar 菜单调整 |
| `frontend/src/stores/chat.ts` | 修改 | 加 sessions、loadHistory |
| `frontend/src/api/index.ts` | 修改 | 加 API 函数 |
| `backend/routes/chat.py` | 修改 | 加 /sessions、/history 路由 |
| `db/repositories.py` | 修改 | 加 list_sessions 方法 |
