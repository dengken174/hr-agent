<template>
  <div class="chat-view">
    <div class="chat-header">
      <h3>智能对话</h3>
      <el-button text size="small" @click="chatStore.clearMessages()">
        <el-icon><Delete /></el-icon>
        清空对话
      </el-button>
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
        <div class="msg-bubble" v-html="renderMarkdown(msg.content)" />
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
</template>

<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import { useChatStore } from '../stores/chat'
import { useUserStore } from '../stores/user'

const chatStore = useChatStore()
const userStore = useUserStore()
const inputText = ref('')
const msgContainer = ref<HTMLElement>()

const md = new MarkdownIt({ breaks: true, linkify: true })

const quickQuestions = [
  '公司年假有多少天？',
  '五险一金怎么交？',
  '入职需要带什么材料？',
  '住房补贴标准是多少？',
]

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

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || chatStore.isStreaming) return
  inputText.value = ''
  await chatStore.sendMessage(text)
  scrollToBottom()
}

function handleQuick(q: string) {
  inputText.value = q
  handleSend()
}
</script>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 40px);
  max-width: 900px;
  margin: 0 auto;
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
</style>
