/**
 * AudioManager — 单例，管理全局音频播放。
 *
 * 设计原则（业内主流）:
 *   1. 单一 <audio> 元素复用，不反复 new Audio()
 *   2. 请求 ID 互斥 — 每个 play() 调用带 requestId，只播最新请求
 *   3. 优先级队列 — 高优先级抢占低优先级
 *   4. 无 retry — 网络音频重试意义不大，失败直接报错
 */

type Priority = 0 | 1 | 2 // 0=auto-TTS 1=manual replay 2=system

interface PlayRequest {
  requestId: string
  src: string
  priority: Priority
  onStart?: () => void
  onEnd?: () => void
  onError?: (msg: string) => void
}

interface AudioManagerState {
  activeId: string | null
  activePriority: Priority
  playing: boolean
}

type StateListener = (state: AudioManagerState) => void

class AudioManager {
  private _audio: HTMLAudioElement | null = null
  private _activeId: string | null = null
  private _activePriority: Priority = 0
  private _listeners: Set<StateListener> = new Set()
  private _unlockAttempted = false

  /** 确保浏览器音频子系统已解锁（首次用户交互时调用） */
  unlock(): void {
    if (this._unlockAttempted) return
    this._unlockAttempted = true
    try {
      const a = this._getAudio()
      a.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA='
      a.play().then(() => { a.pause(); a.currentTime = 0; a.src = '' }).catch(() => {})
    } catch { /* best effort */ }
  }

  /** 播放音频。如果当前有低优先级请求，会被抢占 */
  play(req: PlayRequest): void {
    // 更高优先级抢占
    if (this._activeId && req.priority >= this._activePriority) {
      this._abortCurrent()
    }
    // 同优先级，先到先得（后面的忽略）
    if (this._activeId && req.priority < this._activePriority) {
      return
    }

    this._activeId = req.requestId
    this._activePriority = req.priority

    const audio = this._getAudio()
    audio.src = req.src

    const onLoaded = () => {
      if (this._activeId !== req.requestId) return
      req.onStart?.()
      this._notify()
    }

    const onEnded = () => {
      if (this._activeId !== req.requestId) { audio.removeEventListener('loadeddata', onLoaded); return }
      audio.removeEventListener('loadeddata', onLoaded)
      this._activeId = null
      this._activePriority = 0
      req.onEnd?.()
      this._notify()
    }

    const onError = () => {
      if (this._activeId !== req.requestId) { audio.removeEventListener('loadeddata', onLoaded); return }
      audio.removeEventListener('loadeddata', onLoaded)
      const msg = audio.error ? `code=${audio.error.code}` : 'unknown'
      this._activeId = null
      this._activePriority = 0
      req.onError?.(msg)
      this._notify()
      // Clean up src to release memory
      audio.removeAttribute('src')
    }

    audio.addEventListener('loadeddata', onLoaded, { once: true })
    audio.addEventListener('ended', onEnded, { once: true })
    audio.addEventListener('error', onError, { once: true })

    audio.play().catch(() => {
      if (this._activeId === req.requestId) {
        // Clear active state so future requests aren't blocked
        audio.removeEventListener('ended', onEnded)
        audio.removeEventListener('error', onError)
        this._activeId = null
        this._activePriority = 0
        audio.removeAttribute('src')
        req.onError?.('play() rejected by browser')
        this._notify()
      }
    })
  }

  /** 停止指定请求（不触发回调） */
  stop(requestId: string): void {
    if (this._activeId !== requestId) return
    this._abortCurrent()
  }

  /** 停止所有播放 */
  stopAll(): void {
    this._abortCurrent()
  }

  subscribe(fn: StateListener): () => void {
    this._listeners.add(fn)
    return () => { this._listeners.delete(fn) }
  }

  get state(): AudioManagerState {
    return {
      activeId: this._activeId,
      activePriority: this._activePriority,
      playing: this._activeId !== null,
    }
  }

  // ── private ──

  private _getAudio(): HTMLAudioElement {
    if (!this._audio) {
      this._audio = new Audio()
      this._audio.preload = 'auto'
    }
    return this._audio
  }

  private _abortCurrent(): void {
    if (!this._audio || !this._activeId) return
    this._audio.pause()
    this._audio.removeAttribute('src')
    // Remove all listeners by cloning node (simplest way)
    const newAudio = this._audio.cloneNode() as HTMLAudioElement
    newAudio.preload = 'auto'
    if (this._audio.parentNode) {
      this._audio.parentNode.replaceChild(newAudio, this._audio)
    }
    this._audio = newAudio
    this._activeId = null
    this._activePriority = 0
    this._notify()
  }

  private _notify(): void {
    const s = this.state
    for (const fn of this._listeners) fn(s)
  }
}

/** 全局单例 */
export const audioManager = new AudioManager()
