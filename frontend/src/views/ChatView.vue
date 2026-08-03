<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { chatSync } from '../api'
import { ElMessage } from 'element-plus'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ breaks: true })

interface Message { role: 'user' | 'agent'; content: string; time: string }
const messages = ref<Message[]>([])
const input = ref('')
const loading = ref(false)
const chatRef = ref<HTMLElement>()

const scrollBottom = () => {
  nextTick(() => {
    const el = chatRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

const send = async () => {
  const text = input.value.trim()
  if (!text || loading.value) return

  messages.value.push({ role: 'user', content: text, time: now() })
  input.value = ''
  scrollBottom()
  loading.value = true

  try {
    const { data } = await chatSync({
      message: text,
      session_id: 'web-' + Date.now(),
      user_id: 2001,
      user_role: 'hr_admin',
    })
    messages.value.push({ role: 'agent', content: data.reply, time: now() })
  } catch {
    ElMessage.error('请求失败，请稍后重试')
  } finally {
    loading.value = false
    scrollBottom()
  }
}

const now = () => new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })

const clearChat = () => { messages.value = [] }

// 快速提问
const quickPrompts = ['帮我查一下我的薪资', '公司年假政策是什么', '入职需要带什么材料', '帮我查一下技术部成员']

onMounted(scrollBottom)
</script>

<template>
  <div style="display:flex;flex-direction:column;height:100vh">
    <!-- 顶部栏 -->
    <div style="height:var(--header-height);background:var(--bg-card);border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;padding:0 24px;flex-shrink:0">
      <div style="font-size:16px;font-weight:600">智能对话</div>
      <el-button text @click="clearChat">清空对话</el-button>
    </div>

    <!-- 消息区 -->
    <div ref="chatRef" style="flex:1;overflow-y:auto;padding:24px 32px">
      <!-- 空状态 -->
      <div v-if="messages.length === 0" style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:20px">
        <div style="font-size:48px;opacity:0.3">💬</div>
        <div style="font-size:18px;color:var(--text-secondary)">HR 智能助手，随时为您服务</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center;max-width:600px">
          <el-button
            v-for="p in quickPrompts" :key="p"
            size="small" round
            @click="input = p; send()"
          >{{ p }}</el-button>
        </div>
      </div>

      <!-- 消息列表 -->
      <div v-for="(m, i) in messages" :key="i" style="margin-bottom:24px">
        <!-- 用户消息 -->
        <div v-if="m.role === 'user'" style="display:flex;justify-content:flex-end">
          <div style="max-width:70%">
            <div style="background:var(--el-color-primary);color:#fff;padding:12px 16px;border-radius:12px 12px 4px 12px;line-height:1.6;white-space:pre-wrap">{{ m.content }}</div>
            <div style="text-align:right;font-size:11px;color:var(--text-secondary);margin-top:4px">{{ m.time }}</div>
          </div>
        </div>

        <!-- Agent 消息 -->
        <div v-else style="display:flex;gap:10px">
          <div style="width:32px;height:32px;background:var(--el-color-primary);border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:14px;flex-shrink:0;margin-top:4px">AI</div>
          <div style="max-width:75%">
            <div style="background:var(--bg-card);padding:16px 20px;border-radius:4px 12px 12px 12px;box-shadow:0 1px 3px rgba(0,0,0,0.06);line-height:1.7" v-html="md.render(m.content)"></div>
            <div style="font-size:11px;color:var(--text-secondary);margin-top:4px;margin-left:4px">{{ m.time }}</div>
          </div>
        </div>
      </div>

      <!-- Loading -->
      <div v-if="loading" style="display:flex;gap:10px;align-items:center;color:var(--text-secondary)">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>思考中...</span>
      </div>
    </div>

    <!-- 输入区 -->
    <div style="background:var(--bg-card);border-top:1px solid var(--border);padding:16px 24px;flex-shrink:0">
      <div style="display:flex;gap:12px;max-width:900px;margin:0 auto">
        <el-input
          v-model="input"
          placeholder="输入您的问题，按 Enter 发送..."
          size="large"
          @keyup.enter="send"
          :disabled="loading"
        />
        <el-button type="primary" size="large" @click="send" :loading="loading" :disabled="!input.trim()">
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>
