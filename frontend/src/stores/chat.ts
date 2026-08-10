import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: number
  audioUrl?: string // blob URL for voice input playback
}

export interface Session {
  session_id: string
  title: string
  message_count: number
  updated_at: string
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const isStreaming = ref(false)
  const lastReply = ref('')
  const sessionId = ref('default')
  const sessions = ref<Session[]>([])
  const sidebarCollapsed = ref(false)

  function addMessage(role: Message['role'], content: string) {
    messages.value.push({
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      role,
      content,
      timestamp: Date.now(),
    })
  }

  async function sendMessage(text: string) {
    addMessage('user', text)
    isStreaming.value = true
    const assistantMsg: Message = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      role: 'assistant',
      content: '',
      timestamp: Date.now(),
    }
    messages.value.push(assistantMsg)

    try {
      const resp = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('token')}`,
        },
        body: JSON.stringify({
          message: text,
          session_id: sessionId.value,
        }),
      })

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

      const reader = resp.body?.getReader()
      if (!reader) throw new Error('No reader')

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          try {
            const data = JSON.parse(line.slice(5).trim())
            if (data.text) {
              assistantMsg.content += data.text
            }
          } catch {
            // skip parse errors
          }
        }
      }
    } catch (e) {
      assistantMsg.content = `请求失败: ${(e as Error).message}`
    } finally {
      isStreaming.value = false
      lastReply.value = assistantMsg.content
    }
  }

  function clearMessages() {
    messages.value = []
  }

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

  return { messages, isStreaming, lastReply, sessionId, sessions, sidebarCollapsed, addMessage, sendMessage, clearMessages, fetchSessions, loadHistory, newSession }
})
