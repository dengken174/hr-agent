/**
 * useVoiceTTS — 语音合成与播放 composable。
 *
 * 对接后端 POST /api/tts (SSE 流式分段音频)。
 * 播放委托给 AudioManager 单例（请求 ID 互斥 + 优先级）。
 */
import { ref } from 'vue'
import { audioManager } from './audioManager'

export function useVoiceTTS() {
  const status = ref<'idle' | 'playing' | 'error'>('idle')
  const currentSentence = ref('')
  const lastError = ref('')

  let abortController: AbortController | null = null
  let requestId = '' // current request ID for AudioManager mutual exclusion

  let _playPriority: 0 | 1 = 1  // set by requestTTS, used in SSE handler

  function handleSSEMessage(msg: any): boolean {
    switch (msg.type) {
      case 'tts_start':
        break

      case 'tts_sentence':
        audioManager.play({
          requestId: `${requestId}-${msg.seq}`,
          src: `data:audio/mpeg;base64,${msg.audio}`,
          priority: _playPriority,
          onStart: () => { currentSentence.value = msg.text },
          onEnd: () => { currentSentence.value = '' },
          onError: (e) => {
            console.warn('TTS chunk play failed:', e)
            lastError.value = `播放失败: ${e}`
          },
        })
        break

      case 'tts_end':
        lastError.value = ''
        status.value = 'idle'
        return true // done

      case 'tts_error':
        console.error('TTS sentence error:', msg.message)
        break
    }
    return false
  }

  async function requestTTS(
    text: string,
    voice?: string,
    rate?: string,
    onDone?: () => void,
    priority: 0 | 1 = 1,
  ) {
    stopTTS()
    _playPriority = priority
    requestId = `tts-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    abortController = new AbortController()
    status.value = 'playing'
    lastError.value = ''

    const token = localStorage.getItem('token')
    try {
      const resp = await fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ text, voice, rate }),
        signal: abortController.signal,
      })

      if (!resp.ok) throw new Error(`TTS server error: ${resp.status}`)

      const reader = resp.body?.getReader()
      if (!reader) throw new Error('No response body')

      const decoder = new TextDecoder()
      let buffer = ''
      let done = false

      while (!done) {
        const { done: streamDone, value } = await reader.read()
        if (streamDone) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            done = handleSSEMessage(JSON.parse(line.slice(6)))
          } catch { /* skip parse errors */ }
        }
      }

      if (done) onDone?.()
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        console.error('TTS request failed:', e)
        lastError.value = e.message || '语音合成失败'
        status.value = 'error'
      }
    }
  }

  function stopTTS() {
    if (requestId) {
      audioManager.stopAll() // stop any audio from previous request
      abortController?.abort()
    }
    status.value = 'idle'
    currentSentence.value = ''
  }

  return { status, currentSentence, lastError, requestTTS, stopTTS }
}

// Re-export unlock for page-level integration
export { audioManager }
