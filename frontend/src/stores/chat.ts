import { defineStore } from 'pinia'
import { ref } from 'vue'
import client from '../api/client'

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: number
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const isStreaming = ref(false)
  const sessionId = ref('default')

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
    }
  }

  function clearMessages() {
    messages.value = []
  }

  return { messages, isStreaming, sessionId, addMessage, sendMessage, clearMessages }
})
