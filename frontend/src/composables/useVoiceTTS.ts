import { ref } from 'vue'

interface TTSChunk {
  seq: number
  audio: string
  text: string
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

function playWithTimeout(audio: HTMLAudioElement, timeoutMs: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      audio.pause()
      reject(new Error('playback timeout'))
    }, timeoutMs)
    audio.onended = () => { clearTimeout(timer); resolve() }
    audio.onerror = () => { clearTimeout(timer); reject(new Error('audio error')) }
    audio.play().catch(() => { clearTimeout(timer); reject(new Error('play() failed')) })
  })
}

export function useVoiceTTS() {
  const status = ref<'idle' | 'playing' | 'interrupting'>('idle')
  const currentSentence = ref('')
  let abortController: AbortController | null = null
  let onDoneCallback: (() => void) | null = null

  let playQueue: Promise<void> = Promise.resolve()
  let nextExpectedSeq = 0
  const pendingChunks = new Map<number, TTSChunk>()

  let currentAudio: HTMLAudioElement | null = null

  function handleSSEMessage(msg: any) {
    switch (msg.type) {
      case 'tts_start':
        nextExpectedSeq = 0
        pendingChunks.clear()
        break
      case 'tts_sentence':
        if (msg.seq === nextExpectedSeq) {
          playNext({ seq: msg.seq, audio: msg.audio, text: msg.text })
        } else {
          pendingChunks.set(msg.seq, { seq: msg.seq, audio: msg.audio, text: msg.text })
        }
        break
      case 'tts_end':
        playQueue = playQueue.then(() => {
          status.value = 'idle'
          onDoneCallback?.()
        })
        break
      case 'tts_error':
        console.error('TTS sentence error:', msg.message)
        // Skip this sentence, try next
        playQueue = playQueue.then(() => {
          nextExpectedSeq++
          const next = pendingChunks.get(nextExpectedSeq)
          if (next) { pendingChunks.delete(nextExpectedSeq); playNext(next) }
        })
        break
    }
  }

  function playNext(msg: TTSChunk) {
    playQueue = playQueue
      .then(() => playAudioChunk(msg))
      .then(() => {
        nextExpectedSeq++
        const next = pendingChunks.get(nextExpectedSeq)
        if (next) {
          pendingChunks.delete(nextExpectedSeq)
          playNext(next)
        }
      })
  }

  async function playAudioChunk(msg: TTSChunk): Promise<void> {
    currentSentence.value = msg.text
    currentAudio = new Audio(`data:audio/mpeg;base64,${msg.audio}`)
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        await playWithTimeout(currentAudio, 5000)
        return
      } catch {
        if (attempt < 2) await sleep([2000, 4000][attempt])
      }
    }
  }

  async function requestTTS(
    text: string,
    voice?: string,
    rate?: string,
    onDone?: () => void,
  ) {
    stopTTS()
    abortController = new AbortController()
    nextExpectedSeq = 0
    pendingChunks.clear()
    status.value = 'playing'
    onDoneCallback = onDone || null

    const token = localStorage.getItem('token')
    try {
      const resp = await fetch('/api/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ text, voice, rate }),
        signal: abortController.signal,
      })

      const reader = resp.body?.getReader()
      if (!reader) return
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            handleSSEMessage(JSON.parse(line.slice(6)))
          } catch { /* skip */ }
        }
      }
    } catch (e: any) {
      if (e.name !== 'AbortError') {
        console.error('TTS request failed:', e)
        status.value = 'idle'
      }
    }
  }

  function stopTTS() {
    if (status.value !== 'playing') return
    status.value = 'interrupting'
    abortController?.abort()
    currentAudio?.pause()
    currentAudio = null
    pendingChunks.clear()
    playQueue = playQueue.then(() => { status.value = 'idle' })
  }

  return { status, currentSentence, requestTTS, stopTTS }
}
