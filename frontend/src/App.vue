<template>
  <div v-if="!userStore.isLoggedIn" class="app-plain">
    <router-view />
  </div>
  <div v-else class="app-layout">
    <aside class="sidebar">
      <div class="sidebar-header">
        <h2>HR Agent</h2>
        <span class="sidebar-subtitle">智能助手</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        router
        background-color="#1d1e2c"
        text-color="#a0a4b8"
        active-text-color="#fff"
        class="sidebar-menu"
      >
        <el-menu-item index="/chat">
          <el-icon><ChatDotRound /></el-icon>
          <span>对话</span>
        </el-menu-item>
        <el-menu-item index="/approval">
          <el-icon><Checked /></el-icon>
          <span>审批</span>
        </el-menu-item>
        <el-menu-item index="/knowledge">
          <el-icon><Document /></el-icon>
          <span>知识库</span>
        </el-menu-item>
        <el-menu-item index="/eval">
          <el-icon><DataAnalysis /></el-icon>
          <span>RAG 评测</span>
        </el-menu-item>
      </el-menu>
      <div class="sidebar-footer">
        <div class="user-info">
          <el-avatar :size="32">{{ userStore.user?.display_name?.[0] }}</el-avatar>
          <div class="user-meta">
            <span class="user-name">{{ userStore.user?.display_name }}</span>
            <span class="user-role">{{ roleLabel }}</span>
          </div>
        </div>
        <el-button text size="small" @click="handleLogout">
          <el-icon><SwitchButton /></el-icon>
        </el-button>
      </div>
    </aside>
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useUserStore } from './stores/user'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()

const activeMenu = computed(() => route.path)

const roleLabel = computed(() => {
  const map: Record<string, string> = {
    hr_admin: 'HR 管理员',
    employee: '员工',
    interviewer: '面试者',
  }
  return map[userStore.user?.role || ''] || userStore.user?.role || ''
})

function handleLogout() {
  userStore.logout()
  router.push('/login')
}
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f0f2f5;
  color: #303133;
}

.app-layout {
  display: flex;
  height: 100vh;
}

.sidebar {
  width: 220px;
  background: #1d1e2c;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.sidebar-header {
  padding: 20px 16px 12px;
  color: #fff;
}

.sidebar-header h2 {
  font-size: 18px;
  font-weight: 600;
}

.sidebar-subtitle {
  font-size: 12px;
  color: #6b6f85;
}

.sidebar-menu {
  border-right: none;
  flex: 1;
}

.sidebar-menu .el-menu-item {
  height: 46px;
  line-height: 46px;
}

.sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid #2a2b3d;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-meta {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.user-name {
  color: #e0e1ea;
  font-size: 13px;
}

.user-role {
  color: #6b6f85;
  font-size: 11px;
}

.main-content {
  flex: 1;
  overflow: auto;
  padding: 20px;
}

.app-plain {
  height: 100vh;
}
</style>
