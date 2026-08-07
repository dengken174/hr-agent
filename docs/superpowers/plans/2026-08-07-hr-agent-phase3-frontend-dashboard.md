# Phase 3 Frontend Enhancement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Dashboard page, session management sidebar, wire Skill Manager into router/sidebar.

**Architecture:** Backend changes are minimal (2 new endpoints + 1 repo method). Frontend adds 1 new view (Dashboard), modifies ChatView (session sidebar), router (2 new routes), App.vue (sidebar items), chat store (session state), and API layer (2 new calls).

**Tech Stack:** FastAPI (Python), Vue 3 + Element Plus + Pinia + ECharts + TypeScript, aiomysql

## Global Constraints

- No changes to Agent core, MCP servers, or existing backend business logic
- Dashboard trend chart uses mock data (backend has no time-series stats endpoint)
- Session deletion is out of scope
- Skill Manager component already exists — only router + sidebar wiring needed
- All new API endpoints follow existing auth pattern via `get_current_user` dependency
- MySQL failure gracefully falls back to empty list (existing pattern in codebase)

---

### Task 1: ConversationRepo.list_sessions

**Files:**
- Modify: `db/repositories.py` (add method to `ConversationRepo`)

**Interfaces:**
- Consumes: `get_pool()` from `db/connection.py`
- Produces: `ConversationRepo.list_sessions(user_id: int) -> list[dict]`
  - Returns `[{"session_id": str, "title": str, "message_count": int, "updated_at": str}, ...]`

- [ ] **Step 1: Add `list_sessions` method to `ConversationRepo`**

In `db/repositories.py`, inside the `ConversationRepo` class, after the `get_history` method (after line 53), add:

```python
async def list_sessions(self, user_id: int) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("""
                SELECT
                    session_id,
                    (SELECT content FROM conversations c2
                     WHERE c2.session_id = c1.session_id AND c2.role = 'user'
                     ORDER BY c2.created_at ASC LIMIT 1
                    ) AS title,
                    COUNT(*) AS message_count,
                    MAX(created_at) AS updated_at
                FROM conversations c1
                WHERE user_id = %s
                GROUP BY session_id
                ORDER BY updated_at DESC
                LIMIT 50
            """, (user_id,))
            rows = await cur.fetchall()
            for r in rows:
                if r.get("title") and len(r["title"]) > 20:
                    r["title"] = r["title"][:20] + "..."
                if r.get("updated_at"):
                    r["updated_at"] = r["updated_at"].strftime('%Y-%m-%dT%H:%M:%S')
            return rows
```

- [ ] **Step 2: Verify the SQL is valid by checking existing imports**

Confirm `aiomysql` is already imported at the top of `db/repositories.py`. (It is — line 6: `import aiomysql`)

- [ ] **Step 3: Verify the method is reachable from routes**

The routes in `backend/routes/chat.py` already import `ConversationRepo` (line 18). No further import changes needed.

---

### Task 2: Backend Chat Session & History Endpoints

**Files:**
- Modify: `backend/routes/chat.py`

**Interfaces:**
- Consumes: `ConversationRepo.list_sessions(user_id)` from Task 1, `ConversationRepo.get_history(session_id)` (existing)
- Produces:
  - `GET /api/chat/sessions` → `list[dict]` (JSON array of session summaries)
  - `GET /api/chat/history?session_id=xxx` → `list[dict]` (JSON array of messages)

- [ ] **Step 1: Add the two new routes to `backend/routes/chat.py`**

After the existing `/api/chat` POST route (after line 85), add:

```python
@router.get("/sessions")
async def list_sessions(user: dict = Depends(get_current_user)):
    """返回当前用户的所有会话列表。"""
    try:
        sessions = await _conv_repo.list_sessions(user_id=user["user_id"])
        return sessions
    except Exception:
        logger.warning("Failed to list sessions, returning empty")
        return []


@router.get("/history")
async def get_history(session_id: str, user: dict = Depends(get_current_user)):
    """返回指定会话的对话历史。"""
    try:
        messages = await _conv_repo.get_history(session_id=session_id)
        return [
            {"role": m["role"], "content": m["content"], "created_at": str(m.get("created_at", ""))}
            for m in messages
        ]
    except Exception:
        logger.warning("Failed to get history for session %s", session_id)
        return []
```

- [ ] **Step 2: Verify route registration**

The router is already included at `backend/main.py:63` as `app.include_router(chat_router)`. New routes are automatically registered.

---

### Task 3: Frontend API Functions

**Files:**
- Modify: `frontend/src/api/index.ts`

**Interfaces:**
- Produces:
  - `getSessions() => client.get('/chat/sessions')`
  - `getChatHistory(sessionId: string) => client.get('/chat/history', { params: { session_id: sessionId } })`

- [ ] **Step 1: Add the two API functions**

In `frontend/src/api/index.ts`, after the existing `chatSync` export (after line 26), add:

```typescript
// Chat sessions
export const getSessions = () => api.get('/api/chat/sessions')
export const getChatHistory = (sessionId: string) =>
  api.get('/api/chat/history', { params: { session_id: sessionId } })
```

Note: This file uses `api` (not `client`) — it imports `axios` directly. See line 1-3 of the existing file.

- [ ] **Step 2: Verify the file uses correct base instance**

The existing file import is `import axios from 'axios'` with `const api = axios.create({ baseURL: '', timeout: 60000 })` — the baseURL is empty, so paths must include `/api/` prefix (matching the existing pattern on lines 20-52).

---

### Task 4: Chat Store — Sessions + History Loading

**Files:**
- Modify: `frontend/src/stores/chat.ts`

**Interfaces:**
- Consumes: `getSessions`, `getChatHistory` from Task 3
- Produces:
  - `chatStore.sessions: Ref<Session[]>` — session list
  - `chatStore.currentSessionId: Ref<string>` — active session
  - `chatStore.loadHistory(sessionId: string): Promise<void>`
  - `chatStore.fetchSessions(): Promise<void>`
  - `chatStore.newSession(): void`
  - `chatStore.sidebarCollapsed: Ref<boolean>`

- [ ] **Step 1: Add types and state**

At the top of `frontend/src/stores/chat.ts`, after the `Message` interface (after line 10), add:

```typescript
export interface Session {
  session_id: string
  title: string
  message_count: number
  updated_at: string
}
```

Inside the store function (after `const sessionId = ref('default')` on line 14), add:

```typescript
const sessions = ref<Session[]>([])
const currentSessionId = ref<string>('default')
const sidebarCollapsed = ref(false)
```

- [ ] **Step 2: Add actions**

After the `clearMessages` function (after line 86), add:

```typescript
async function fetchSessions() {
  try {
    const { data } = await import('../api/index').then(m => m.getSessions())
    sessions.value = data
  } catch {
    // silently fail
  }
}

async function loadHistory(sessionId: string) {
  currentSessionId.value = sessionId
  sessionId_legacy.value = sessionId
  messages.value = []
  try {
    const { data } = await import('../api/index').then(m => m.getChatHistory(sessionId))
    for (const msg of data) {
      addMessage(msg.role as Message['role'], msg.content)
    }
  } catch {
    // silently fail, messages stay empty
  }
}

function newSession() {
  const id = crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  currentSessionId.value = id
  sessionId_legacy.value = id
  messages.value = []
  sessions.value.unshift({
    session_id: id,
    title: '新对话',
    message_count: 0,
    updated_at: new Date().toISOString(),
  })
}
```

Wait — the existing code uses `sessionId` as a `ref('default')`. The `sendMessage` function references `sessionId.value`. We need to keep backward compatibility. Let me re-read the existing code one more time.

Looking at the existing store: it has `sessionId` ref used in `sendMessage`. We should rename the existing `sessionId` to keep it working, or better: keep `sessionId` as the active session ID that both the sidebar and sendMessage use. Let me update the plan accordingly.

- [ ] **Step 1 (revised): Add state alongside existing `sessionId`**

After the existing `const sessionId = ref('default')` (line 14), add:

```typescript
const sessions = ref<Session[]>([])
const sidebarCollapsed = ref(false)
```

No need for `currentSessionId` — reuse the existing `sessionId` ref. Update it to a more descriptive name by keeping it as-is.

- [ ] **Step 2 (revised): Add actions after `clearMessages` (after line 86)**

```typescript
async function fetchSessions() {
  try {
    const { getSessions } = await import('../api/index')
    const { data } = await getSessions()
    sessions.value = data
  } catch {
    // silently fail
  }
}

async function loadHistory(sid: string) {
  sessionId.value = sid
  messages.value = []
  try {
    const { getChatHistory } = await import('../api/index')
    const { data } = await getChatHistory(sid)
    for (const msg of data) {
      addMessage(msg.role as Message['role'], msg.content)
    }
  } catch {
    // silently fail
  }
}

function newSession() {
  const id = crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  sessionId.value = id
  messages.value = []
  sessions.value.unshift({
    session_id: id,
    title: '新对话',
    message_count: 0,
    updated_at: new Date().toISOString(),
  })
}
```

- [ ] **Step 3: Update the return statement**

Add the new state and actions to the return object on the last line:

```typescript
return { messages, isStreaming, sessionId, sessions, sidebarCollapsed, addMessage, sendMessage, clearMessages, fetchSessions, loadHistory, newSession }
```

---

### Task 5: Dashboard View

**Files:**
- Create: `frontend/src/views/DashboardView.vue`

**Interfaces:**
- Consumes: `client` from `../api/client`, `useUserStore` (existing)
- Produces: Dashboard page component

- [ ] **Step 1: Create `DashboardView.vue`**

Create `frontend/src/views/DashboardView.vue` with full template, script, and style:

```vue
<template>
  <div class="dashboard">
    <div class="page-header">
      <h3>仪表盘</h3>
    </div>

    <el-row :gutter="16" class="stat-cards">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background: #e6f4ff"><el-icon :size="24" color="#409eff"><ChatDotRound /></el-icon></div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.total_conversations }}</div>
            <div class="stat-label">总对话数</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background: #e6fffb"><el-icon :size="24" color="#13c2c2"><MagicStick /></el-icon></div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.active_skills }} / {{ stats.total_skills }}</div>
            <div class="stat-label">活跃 Skill</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background: #fff7e6"><el-icon :size="24" color="#fa8c16"><Checked /></el-icon></div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.pending_approvals }}</div>
            <div class="stat-label">待审批</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-icon" style="background: #f6ffed"><el-icon :size="24" color="#52c41a"><Document /></el-icon></div>
          <div class="stat-body">
            <div class="stat-value">{{ stats.knowledge_docs }}</div>
            <div class="stat-label">知识文档</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="chart-row">
      <el-col :span="12">
        <el-card>
          <template #header>对话趋势 (近7天)</template>
          <div ref="trendChartRef" style="height: 300px" />
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <template #header>审批分布</template>
          <div ref="approvalChartRef" style="height: 300px" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import client from '../api/client'

interface Stats {
  total_conversations: number
  total_skills: number
  active_skills: number
  pending_approvals: number
  knowledge_docs: number
}

const stats = ref<Stats>({
  total_conversations: 0,
  total_skills: 0,
  active_skills: 0,
  pending_approvals: 0,
  knowledge_docs: 0,
})

const trendChartRef = ref<HTMLElement>()
const approvalChartRef = ref<HTMLElement>()

function mockRecentTrend() {
  const days = ['8/1', '8/2', '8/3', '8/4', '8/5', '8/6', '8/7']
  return days.map(d => ({
    date: d,
    count: Math.floor(Math.random() * 30) + 10,
  }))
}

async function loadStats() {
  try {
    const { data } = await client.get('/stats')
    stats.value = data
  } catch { /* ignore */ }
}

function renderTrendChart() {
  if (!trendChartRef.value) return
  const chart = echarts.init(trendChartRef.value)
  const data = mockRecentTrend()
  chart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: data.map(d => d.date) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      name: '对话数',
      type: 'line',
      data: data.map(d => d.count),
      smooth: true,
      lineStyle: { color: '#409eff' },
      itemStyle: { color: '#409eff' },
      areaStyle: { color: 'rgba(64,158,255,0.1)' },
    }],
  })
}

async function renderApprovalChart() {
  if (!approvalChartRef.value) return
  const chart = echarts.init(approvalChartRef.value)
  try {
    const { data } = await client.get('/approvals/stats')
    chart.setOption({
      tooltip: { trigger: 'item' },
      legend: { bottom: 0 },
      series: [{
        name: '审批分布',
        type: 'pie',
        radius: ['45%', '70%'],
        avoidLabelOverlap: false,
        itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
        label: { show: true, formatter: '{b}: {c}' },
        data: [
          { value: data.pending || 0, name: '待审批', itemStyle: { color: '#fa8c16' } },
          { value: data.approved || 0, name: '已通过', itemStyle: { color: '#52c41a' } },
          { value: data.rejected || 0, name: '已驳回', itemStyle: { color: '#f56c6c' } },
        ],
      }],
    })
  } catch {
    chart.setOption({
      series: [{ type: 'pie', data: [] }],
    })
  }
}

onMounted(async () => {
  await loadStats()
  await nextTick(() => {
    renderTrendChart()
    renderApprovalChart()
  })
})
</script>

<style scoped>
.dashboard {
  max-width: 1200px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 16px;
}
.page-header h3 {
  font-size: 16px;
  font-weight: 600;
}
.stat-card {
  display: flex;
  align-items: center;
}
.stat-card :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
}
.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: #303133;
}
.stat-label {
  font-size: 13px;
  color: #909399;
  margin-top: 2px;
}
.chart-row {
  margin-top: 16px;
}
</style>
```

- [ ] **Step 2: Verify echarts is already in dependencies**

Check `frontend/package.json` — echarts is `^6.1.0` on line 14. Confirmed.

---

### Task 6: Router + Sidebar — Dashboard & Skills

**Files:**
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/App.vue`

**Interfaces:**
- Router: `/dashboard` (new), `/skills` (new), default redirect → `/dashboard`
- App.vue sidebar: Dashboard menu item (top), Skills menu item (hr_admin only)

- [ ] **Step 1: Add routes to `router/index.ts`**

In `frontend/src/router/index.ts`, modify the routes array:

```typescript
routes: [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { noAuth: true },
  },
  {
    path: '/',
    redirect: '/dashboard',
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('../views/DashboardView.vue'),
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('../views/ChatView.vue'),
  },
  {
    path: '/skills',
    name: 'skills',
    component: () => import('../views/SkillManager.vue'),
  },
  // ... existing approval, knowledge, eval routes unchanged
],
```

- [ ] **Step 2: Update App.vue sidebar menu**

In `frontend/src/App.vue`, inside the `<el-menu>` (after line 14), add before the existing Chat menu item:

```vue
<el-menu-item index="/dashboard">
  <el-icon><DataBoard /></el-icon>
  <span>仪表盘</span>
</el-menu-item>
```

After the existing Eval menu item (after line 34), add:

```vue
<el-menu-item index="/skills" v-if="userStore.isAdmin">
  <el-icon><MagicStick /></el-icon>
  <span>技能管理</span>
</el-menu-item>
```

- [ ] **Step 3: Verify icons exist**

`DataBoard` and `MagicStick` are from `@element-plus/icons-vue`, which is globally registered in `main.ts:17-19` via `for (const [key, component] of Object.entries(ElementPlusIconsVue))`. Confirmed available.

---

### Task 7: ChatView — Session Sidebar

**Files:**
- Modify: `frontend/src/views/ChatView.vue`

**Interfaces:**
- Consumes: `chatStore.sessions`, `chatStore.sidebarCollapsed`, `chatStore.fetchSessions()`, `chatStore.loadHistory(sid)`, `chatStore.newSession()`
- Produces: Session sidebar in ChatView

- [ ] **Step 1: Add session sidebar to ChatView template**

Modify the `<template>` in `ChatView.vue`. Wrap the existing chat view in a flex container with the sidebar. Replace lines 2-7 (the outer `<div class="chat-view">` and chat-header) with:

```vue
<template>
  <div class="chat-layout">
    <aside class="session-sidebar" :class="{ collapsed: chatStore.sidebarCollapsed }">
      <div class="sidebar-top">
        <el-button type="primary" size="small" @click="chatStore.newSession()" style="width: 100%">
          <el-icon><Plus /></el-icon>
          新对话
        </el-button>
      </div>
      <div class="session-list">
        <div
          v-for="s in chatStore.sessions"
          :key="s.session_id"
          class="session-item"
          :class="{ active: s.session_id === chatStore.sessionId }"
          @click="chatStore.loadHistory(s.session_id)"
        >
          <div class="session-title">{{ s.title || '新对话' }}</div>
          <div class="session-meta">
            <span>{{ s.message_count }} 条</span>
            <span>{{ formatSessionTime(s.updated_at) }}</span>
          </div>
        </div>
        <el-empty v-if="chatStore.sessions.length === 0" description="暂无对话" :image-size="48" />
      </div>
    </aside>

    <div class="chat-main">
      <div class="chat-header">
        <el-button text size="small" @click="chatStore.sidebarCollapsed = !chatStore.sidebarCollapsed">
          <el-icon><Fold /></el-icon>
        </el-button>
        <h3>智能对话</h3>
        <el-button text size="small" @click="chatStore.clearMessages()">
          <el-icon><Delete /></el-icon>
          清空对话
        </el-button>
      </div>

      <!-- rest of existing chat-messages + chat-input-area unchanged -->
      <div class="chat-messages" ref="msgContainer">
        <!-- ... exactly as before, no changes ... -->
```

Full template structure:
1. `.chat-layout` flex container wrapping everything
2. `aside.session-sidebar` with session list
3. `.chat-main` containing the original chat-header + chat-messages + chat-input-area

- [ ] **Step 2: Add session helper functions in `<script setup>`**

At the top of `<script setup>` (after the `const md = ...` line), add:

```typescript
import { onMounted } from 'vue'

function formatSessionTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 86400000) return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
```

- [ ] **Step 3: Add `onMounted` to load sessions and update sendMessage**

After the `formatSessionTime` function, add:

```typescript
onMounted(() => {
  chatStore.fetchSessions()
})
```

Modify the `sendMessage` function: in the `sendMessage` function (around line 116), add session refresh after a successful message send. After `scrollToBottom()`, add:

```typescript
// Refresh session list after new messages (debounced: only on first message per session)
if (chatStore.messages.length <= 2) {
  chatStore.fetchSessions()
}
```

- [ ] **Step 4: Add sidebar styles**

Replace the existing `<style scoped>` block. Keep all existing `.chat-view`, `.chat-messages`, `.message-row`, `.msg-bubble`, `.typing`, `.chat-input-area` styles, but replace `.chat-view` with the new layout styles. Add at the top of `<style scoped>`:

```css
.chat-layout {
  display: flex;
  height: calc(100vh - 40px);
  max-width: 1100px;
  margin: 0 auto;
}

.session-sidebar {
  width: 220px;
  border-right: 1px solid #e4e7ed;
  background: #fafafa;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: width 0.2s, padding 0.2s;
  overflow: hidden;
}

.session-sidebar.collapsed {
  width: 0;
  border-right: none;
}

.sidebar-top {
  padding: 12px;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 8px;
}

.session-item {
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 0.15s;
}

.session-item:hover {
  background: #e8eaed;
}

.session-item.active {
  background: #d9ecff;
}

.session-title {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-meta {
  font-size: 11px;
  color: #909399;
  margin-top: 2px;
  display: flex;
  gap: 8px;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* Keep all existing chat-view styles but rename .chat-view to .chat-main */
.chat-main {
  display: flex;
  flex-direction: column;
  height: 100%;
}

/* ... rest of existing styles unchanged, .chat-view → .chat-main */
```

- [ ] **Step 5: Ensure existing scroll behavior still works**

The `msgContainer` ref and scroll-to-bottom logic are unchanged. The chat messages area is still inside `.chat-main`, so scroll behavior is unaffected.

---

### Task 8: Verification

No automated test suite exists for this project. Verify manually:

- [ ] **Step 1: Start backend**

```bash
cd D:\hragent
python -m backend.main
```

Browse `http://localhost:8080/api/health` → should return `{"status": "ok"}`

- [ ] **Step 2: Test new endpoints**

```bash
# Login to get token
curl -X POST http://localhost:8080/api/auth/login -H "Content-Type: application/json" -d "{\"username\":\"admin\",\"password\":\"admin123\"}"

# Test sessions (use token from above)
curl http://localhost:8080/api/chat/sessions -H "Authorization: Bearer <token>"

# Test history
curl "http://localhost:8080/api/chat/history?session_id=default" -H "Authorization: Bearer <token>"
```

- [ ] **Step 3: Start frontend**

```bash
cd D:\hragent\frontend
npm run dev
```

Browse to the Vite dev server URL. Verify:
1. `/dashboard` loads with 4 stat cards + 2 charts
2. `/chat` shows session sidebar, can create new session, switch sessions
3. `/skills` appears in sidebar for admin user, page renders with existing skills
4. Send a message in chat → session appears in sidebar after refresh
5. Login as employee → Skills menu item is hidden
