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
          <el-button
            class="session-delete"
            text
            size="small"
            @click.stop="chatStore.deleteSession(s.session_id)"
          >
            <el-icon><Close /></el-icon>
          </el-button>
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
        <div class="header-right">
          <!-- Voice controls -->
          <div class="voice-controls">
            <el-switch
              v-model="voiceEnabled"
              active-text="语音回复"
              size="small"
            />
            <div class="rate-slider" v-if="voiceEnabled">
              <span class="rate-label">语速 {{ voiceRate }}</span>
              <el-slider
                v-model="voiceRateNum"
                :min="0.5" :max="2.0" :step="0.25"
                style="width: 100px"
                @input="voiceRate = voiceRateNum + 'x'"
              />
            </div>
            <span v-if="voiceTTS.lastError.value" class="tts-error" :title="voiceTTS.lastError.value">
              <el-icon><WarningFilled /></el-icon> 语音故障
            </span>
          </div>
          <el-button text size="small" @click="chatStore.clearMessages()">
            <el-icon><Delete /></el-icon>
            清空对话
          </el-button>
        </div>
      </div>

      <div class="chat-messages" ref="msgContainer">
        <div v-if="chatStore.messages.length === 0" class="chat-empty">
          <el-empty description="发送消息开始对话" />
          <div class="quick-prompts">
            <el-tag
              v-for="q in quickQuestions"
              :key="q"
              class="prompt-tag"
              @click="handleQuick(q)"
            >
              {{ q }}
            </el-tag>
          </div>
        </div>

        <div
          v-for="msg in chatStore.messages"
          :key="msg.id"
          class="message-row"
          :class="msg.role"
        >
          <div class="msg-avatar">
            <el-avatar :size="32" v-if="msg.role === 'user'">
              {{ userStore.user?.display_name?.[0] }}
            </el-avatar>
            <el-avatar :size="32" v-else style="background: #409eff">
              <el-icon><Service /></el-icon>
            </el-avatar>
          </div>
          <div class="msg-bubble">
            <div v-html="renderMarkdown(msg.content)"></div>
            <div v-if="msg.role === 'user' && msg.audioUrl" class="msg-extra">
              <el-button text size="small" @click="playRecording(msg.audioUrl!)">
                <el-icon><VideoPlay /></el-icon> {{ formatDuration(msg.audioUrl) }}
              </el-button>
            </div>
            <div v-if="msg.role === 'assistant' && voiceEnabled && msg.content" class="msg-extra">
              <el-button text size="small" @click="replayTTS(msg.content)">
                <el-icon><Headset /></el-icon> 重播
              </el-button>
            </div>
          </div>
        </div>

        <div v-if="chatStore.isStreaming" class="message-row assistant">
          <div class="msg-avatar">
            <el-avatar :size="32" style="background: #409eff">
              <el-icon><Service /></el-icon>
            </el-avatar>
          </div>
          <div class="msg-bubble typing">
            <span class="typing-dot" />
            <span class="typing-dot" />
            <span class="typing-dot" />
          </div>
        </div>
      </div>

      <div class="chat-input-area">
        <el-button
          :type="micButtonType"
          :disabled="micDisabled"
          :loading="voiceInput.status.value === 'processing'"
          circle
          @click="handleMicClick"
          size="default"
          class="mic-btn"
        >
          <el-icon><Microphone /></el-icon>
        </el-button>
        <el-input
          v-model="inputText"
          type="textarea"
          :rows="2"
          placeholder="输入您的问题..."
          :disabled="chatStore.isStreaming"
          @keyup.enter.exact="handleSend"
          resize="none"
        />
        <el-button
          type="primary"
          :disabled="!inputText.trim() || chatStore.isStreaming"
          :loading="chatStore.isStreaming"
          @click="handleSend"
          style="margin-left: 12px; height: 56px"
        >
          <el-icon><Promotion /></el-icon>
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue'
import MarkdownIt from 'markdown-it'
import { useChatStore } from '../stores/chat'
import { useUserStore } from '../stores/user'
import { useVoiceInput } from '../composables/useVoiceInput'
import { useVoiceTTS } from '../composables/useVoiceTTS'
import { audioManager } from '../composables/audioManager'

const chatStore = useChatStore()
const userStore = useUserStore()
const inputText = ref('')
const msgContainer = ref<HTMLElement>()

const md = new MarkdownIt({ breaks: true, linkify: true, html: false })

const quickQuestions = [
  '公司年假有多少天？',
  '五险一金怎么交？',
  '入职需要带什么材料？',
  '住房补贴标准是多少？',
]

// Voice integration
const voiceInput = useVoiceInput()
const voiceTTS = useVoiceTTS()
const voiceEnabled = ref(localStorage.getItem('hr-voice-enabled') !== 'false')
const voiceRate = ref(localStorage.getItem('hr-voice-rate') || '1.2x')
const voiceRateNum = ref(parseFloat(voiceRate.value))

// Persist voice preferences
watch(voiceEnabled, (v) => localStorage.setItem('hr-voice-enabled', String(v)))
watch(voiceRate, (v) => localStorage.setItem('hr-voice-rate', v))

// Mutual exclusion: disable mic when TTS playing or streaming
const micDisabled = computed(() =>
  !voiceInput.isSupported.value ||
  voiceInput.status.value === 'processing' ||
  voiceTTS.status.value === 'playing' ||
  chatStore.isStreaming
)

const micButtonType = computed(() => {
  switch (voiceInput.status.value) {
    case 'recording': return 'danger'
    case 'calibrating': return 'warning'
    default: return 'default'
  }
})

// Auto-TTS after agent finishes streaming (priority=0, low)
watch(() => chatStore.isStreaming, (newVal, oldVal) => {
  if (!newVal && oldVal && voiceEnabled.value && chatStore.lastReply) {
    voiceTTS.requestTTS(chatStore.lastReply, undefined, voiceRate.value, undefined, 0)
  }
})

// Handle mic click: toggle recording, on stop fill input and send
async function handleMicClick() {
  if (voiceInput.status.value === 'recording' || voiceInput.status.value === 'calibrating') {
    const text = await voiceInput.stopRecording()
    if (text) {
      inputText.value = text
      handleSend()
    }
  } else {
    voiceTTS.stopTTS()  // interrupt any playing TTS before recording
    await voiceInput.startRecording()
  }
}

// Playback helpers
function playRecording(url: string) {
  const audio = new Audio(url)
  audio.play().catch(() => { /* user interaction may be needed */ })
}

function replayTTS(text: string) {
  voiceTTS.requestTTS(text, undefined, voiceRate.value, undefined, 1)
}

function formatDuration(_url: string | undefined): string {
  // Duration is loaded asynchronously; show placeholder until metadata loads
  return '0:00'
}

function renderMarkdown(text: string): string {
  if (!text) return ''
  return md.render(text)
}

function scrollToBottom() {
  nextTick(() => {
    if (msgContainer.value) {
      msgContainer.value.scrollTop = msgContainer.value.scrollHeight
    }
  })
}

watch(() => chatStore.messages.length, scrollToBottom)

function formatSessionTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  if (diff < 86400000) return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

onMounted(() => {
  chatStore.fetchSessions()
  // Unlock audio on first user click (browser autoplay policy)
  const unlockOnce = () => { audioManager.unlock(); document.removeEventListener('click', unlockOnce) }
  document.addEventListener('click', unlockOnce, { once: true })
})

onUnmounted(() => {
  voiceInput.cleanup()
})

async function handleSend() {
  voiceTTS.stopTTS()  // interrupt any playing TTS when user sends new message
  const text = inputText.value.trim()
  if (!text || chatStore.isStreaming) return
  inputText.value = ''
  await chatStore.sendMessage(text)
  scrollToBottom()
  if (chatStore.messages.length <= 2) {
    chatStore.fetchSessions()
  }
}

function handleQuick(q: string) {
  inputText.value = q
  handleSend()
}
</script>

<style scoped>
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

.session-item {
  position: relative;
}

.session-delete {
  position: absolute;
  top: 4px;
  right: 4px;
  opacity: 0;
  transition: opacity 0.15s;
  color: #909399;
}

.session-item:hover .session-delete {
  opacity: 1;
}

.session-delete:hover {
  color: #f56c6c;
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

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 0 12px;
  border-bottom: 1px solid #e4e7ed;
  margin-bottom: 12px;
}

.chat-header h3 {
  font-size: 16px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.voice-controls {
  display: flex;
  align-items: center;
  gap: 12px;
}

.rate-slider {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rate-label {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

.chat-empty {
  padding-top: 60px;
}

.quick-prompts {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 12px;
}

.prompt-tag {
  cursor: pointer;
  transition: all 0.2s;
}

.prompt-tag:hover {
  background: #409eff;
  color: #fff;
  border-color: #409eff;
}

.message-row {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
  max-width: 85%;
}

.message-row.user {
  margin-left: auto;
  flex-direction: row-reverse;
}

.msg-avatar {
  flex-shrink: 0;
}

.msg-bubble {
  background: #fff;
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.7;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

.message-row.user .msg-bubble {
  background: #409eff;
  color: #fff;
}

.msg-bubble :deep(p) {
  margin: 0 0 4px;
}

.msg-bubble :deep(pre) {
  background: #f5f7fa;
  padding: 10px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}

.message-row.user .msg-bubble :deep(pre) {
  background: rgba(255, 255, 255, 0.15);
}

.msg-bubble :deep(code) {
  font-size: 13px;
}

.msg-bubble :deep(ul), .msg-bubble :deep(ol) {
  padding-left: 18px;
  margin: 4px 0;
}

.msg-extra {
  margin-top: 4px;
  display: flex;
  gap: 4px;
}

.typing {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 14px 18px;
}

.typing-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #909399;
  animation: typing 1.4s infinite ease-in-out both;
}

.typing-dot:nth-child(1) { animation-delay: 0s; }
.typing-dot:nth-child(2) { animation-delay: 0.2s; }
.typing-dot:nth-child(3) { animation-delay: 0.4s; }

@keyframes typing {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
  40% { transform: scale(1); opacity: 1; }
}

.chat-input-area {
  display: flex;
  align-items: flex-end;
  padding: 12px 0 0;
  border-top: 1px solid #e4e7ed;
  margin-top: 12px;
}

.mic-btn {
  margin-right: 8px;
  height: 40px;
  width: 40px;
  flex-shrink: 0;
}

.tts-error {
  font-size: 12px;
  color: #f56c6c;
  cursor: help;
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
</style>
