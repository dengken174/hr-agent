<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getMe } from './api'

const router = useRouter()
const route = useRoute()
const activeMenu = ref('/chat')
const user = ref({ display_name: '', role: '' })

watch(() => route.path, (p) => { activeMenu.value = p })

onMounted(async () => {
  const token = localStorage.getItem('token')
  if (token) {
    try { const { data } = await getMe(); user.value = data }
    catch { localStorage.clear() }
  }
})

const roleLabel = (r: string) => ({ hr_admin: 'HR 管理员', employee: '员工', interviewer: '面试者' }[r] || r)

const navItems = [
  { path: '/chat', title: '智能对话', icon: 'ChatDotRound' },
  { path: '/skills', title: '技能管理', icon: 'SetUp' },
  { path: '/approvals', title: '审批管理', icon: 'DocumentChecked' },
  { path: '/knowledge', title: '知识库', icon: 'Collection' },
  { path: '/eval', title: 'RAG 评测', icon: 'DataAnalysis' },
]
</script>

<template>
  <el-container style="height:100vh">
    <!-- 侧边栏 -->
    <el-aside :width="'220px'" style="background:#001529;overflow:hidden">
      <div style="padding:20px 16px 12px;display:flex;align-items:center;gap:10px">
        <div style="width:36px;height:36px;background:var(--el-color-primary);border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:bold;font-size:18px">HR</div>
        <div>
          <div style="color:#fff;font-size:15px;font-weight:600;line-height:1.2">AI Assistant</div>
          <div style="color:#ffffff73;font-size:11px">智能 HR 助手</div>
        </div>
      </div>

      <el-menu
        :default-active="activeMenu"
        background-color="#001529"
        text-color="#ffffffa6"
        active-text-color="#fff"
        style="border-right:none"
        @select="(path: string) => router.push(path)"
      >
        <el-menu-item v-for="item in navItems" :key="item.path" :index="item.path" style="margin:2px 8px;border-radius:6px">
          <el-icon style="margin-right:8px"><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>

      <div style="position:absolute;bottom:16px;left:0;right:0;padding:0 16px">
        <div v-if="user.display_name" style="color:#ffffff73;font-size:12px;text-align:center;padding:8px;background:#ffffff0a;border-radius:6px">
          {{ user.display_name }} · {{ roleLabel(user.role) }}
        </div>
      </div>
    </el-aside>

    <!-- 主内容区 -->
    <el-main style="padding:0;background:var(--bg);overflow:hidden">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </el-main>
  </el-container>
</template>
