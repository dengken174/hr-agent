# 语音输入 + TTS 回复 设计方案 V2

## 概述

为 HR Agent 新增语音交互闭环：
- Web 端：语音输入（录音 → STT → 填入输入框）→ Agent 处理 → TTS 流式语音回复
- 飞书端：Agent 回复后生成语音消息发送给用户
- 用户可随时开关语音回复，可调节 TTS 语速

## 功能清单

| 功能 | 说明 | 端 |
|------|------|-----|
| 语音输入 | 点击麦克风录音，静音2秒自动停止，转文字填入输入框 | Web |
| 语音回放 | 点击播放按钮回放自己录的语音 | Web |
| AI 语音回复（流式分段） | Agent 回复逐句 TTS，边合成边播放，首句秒出 | Web |
| AI 语音回复（飞书） | Agent 回复 → 全文 TTS → 飞书音频消息 | 飞书 |
| 语音开关 | 全局开关，关闭后纯文字交互 | Web + 飞书 |
| TTS 语速调节 | 滑块调节 0.5x ~ 2.0x，默认 1.2x | Web |
| STT 双引擎降级 | 浏览器 SpeechRecognition → 后端 faster-whisper 兜底 | Web |
| 静音自动停止 | AnalyserNode 监测音量，2 秒无声自动结束录音 | Web |
| Markdown 清洗 | TTS 前剥离所有 markdown 语法 | 后端 |
| 播放容错 | 3 次退避重试 + 5s 播放超时保护 | Web |

## 架构

```
Web (Vue3)                             Backend (FastAPI)
─────────                              ─────────
                                       
┌─ useVoiceInput.ts ──────────┐        ┌─ POST /api/stt ────────────┐
│ MediaRecorder (webm/opus)   │  audio │ faster-whisper base        │
│ AnalyserNode 静音检测       │ ────→ │ (浏览器STT失败时兜底)        │
│ SpeechRecognition 转文字    │        └────────────────────────────┘
│ blob URL 回放               │        
└─────────────────────────────┘        ┌─ POST /api/tts (SSE) ──────┐
                                       │ edge-tts (逐句流式)          │
┌─ useVoiceTTS.ts ────────────┐  fetch │ 按标点断句 → 顺序TTS        │
│ SSE 解析 (data: JSON)       │ ←──── │ seq 编号 + 3次退避重试      │
│ Promise 顺序播放队列         │  SSE  │ markdown 清洗               │
│ seq 校验 + 5s 超时           │        └────────────────────────────┘
│ Audio 播放管理               │        
└─────────────────────────────┘        ┌─ 飞书音频消息 ──────────────┐
                                       │ edge-tts → MP3              │
┌─ ChatView.vue ──────────────┐        │ 上传飞书 → msg_type=audio   │
│ 麦克风按钮 + 静音中动画      │        └────────────────────────────┘
│ ▶ 回放按钮                  │        
│ 🔊 TTS 播放/重播按钮         │        
│ 语音开关 + 语速滑块          │        
└─────────────────────────────┘
```

## 详细设计

## 状态机设计

### 语音输入状态机 (useVoiceInput)

```
                    ┌─────────┐
           click    │  idle   │  初始/结束
         ┌─────────│         │──────────┐
         │          └─────────┘          │
         ▼                               │
   ┌──────────┐                          │
   │requesting│  请求麦克风权限            │  权限被拒/浏览器不支持
   │          │                          │
   └────┬─────┘                          ▼
        │ 通过                       ┌──────────┐
        ▼                            │unsupport-│
   ┌──────────┐                      │   ed     │
   │calibrat- │  环境噪音采样 1.5s    │tooltip提示│
   │  ing     │                      └──────────┘
   └────┬─────┘
        │ 校准完成
        ▼
   ┌──────────┐     静音 ≥ 2秒
   │recording │ ─────────────────────────┐
   │  🔴脉冲   │  或手动点击               │
   └────┬─────┘                          │
        │ 停止录音                         │
        ▼                                │
   ┌──────────┐                          │
   │process-  │  STT 转文字中             │
   │  sing    │  ⏳ 旋转                  │
   └────┬─────┘                          │
        │                                │
   ┌────┴────┐                           │
   ▼         ▼                           ▼
success    fail                        idle
文字填入  toast 重试
输入框
   │         │
   └────┬────┘
        ▼
      idle
```

| 状态 | UI | 触发 |
|------|----|------|
| `idle` | 🎤 灰色 | 初始 / 完成后复位 |
| `requesting` | 🎤 黄色 "请求权限..." | 首次点击 |
| `calibrating` | 🎤 黄色脉冲 "校准中..." | 权限通过后自动 |
| `recording` | 🔴 红色脉冲 + 实时波形 | 校准完成自动开始 |
| `processing` | ⏳ 旋转 "识别中..." | 录音停止自动进入 |
| `unsupported` | 🎤 灰色 + tooltip | 浏览器不兼容 |

### TTS 播放状态机 (useVoiceTTS)

```
              requestTTS(text)
   ┌────────┐ ──────────────────────► ┌──────────┐
   │  idle  │                         │ playing  │
   │ 🔊 灰色 │ ◄──── tts_end ──────── │ 🔊 脉冲   │
   └────────┘    或全部 seq 播完       │ 当前句字幕 │
        ▲                             └────┬─────┘
        │          stopTTS()               │
        │   用户点停止 / 发新消息 / 开始录音   │
        │  ┌───────────────────────────────┘
        │  ▼
   ┌──────────┐
   │interrupt- │  fadeout → 清理队列 → idle
   │   ing     │  ~100ms 过渡态
   └──────────┘
```

3 个状态，`interrupting` 是过渡态，执行：
- `audio.pause()` 停当前播放
- `abortController.abort()` 断 SSE
- `pendingChunks.clear()` 清缓存

### ChatView 互斥约束

```
              TTS:  idle   playing  interrupting
STT:
idle             ✅      ❌        ❌
requesting       ✅      ❌        ❌
calibrating      ✅      ❌        ❌
recording        ❌      ❌        ❌
processing       ✅      ❌        ❌
unsupported      ✅      ❌        ❌

❌ = 对方活跃时本方禁用
✅ = 可用
```

核心规则：
- 录音时禁止 TTS（麦克风采集和扬声器播放在同一设备上会冲突）
- TTS 播放时禁止录音（反之亦然）
- Agent 流式回复时两者都禁用，等待回复完成后自动播 TTS

---

### 1. 后端: TTS 流式 SSE 端点

端点：`POST /api/tts`

请求（JSON）：
```json
{"text": "Agent 回复全文", "voice": "zh-CN-XiaoxiaoNeural", "rate": "+20%"}
```

响应：`Content-Type: text/event-stream`（SSE 流式分段）：
```
data: {"type": "tts_start", "total_sentences": 5}

data: {"type": "tts_sentence", "seq": 0, "audio": "<base64 MP3>", "text": "好的，已为您查询到"}

data: {"type": "tts_sentence", "seq": 1, "audio": "<base64 MP3>", "text": "张三的年假余额为5天"}

data: {"type": "tts_end"}

data: {"type": "tts_error", "message": "TTS 合成失败"}
```

处理流程（流水线，非并行）：
1. Agent 返回完整回复文本
2. 后端按 `。！？\n` 断句（`；，：、` 作为从句，不单独断句）
3. 串行逐句调用 edge-tts（避免微软限流），合成完一句立即 SSE 推送
4. 不等待前端播完，直接合成下一句（流水线）
5. 每句分配 `seq`，按序推送。因串行合成，99.9% 情况下不会乱序
6. TTS 失败时重试 3 次（退避 2s/4s/8s）

断句逻辑：
```python
import re

def split_sentences(text: str) -> list[str]:
    # 按句末标点断句，保留从句完整性
    parts = re.split(r'(?<=[。！？\n])', text)
    sentences = []
    buf = ""
    for p in parts:
        buf += p
        if re.search(r'[。！？\n]$', p) or len(buf) > 80:
            sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    return [s for s in sentences if s]
```

Markdown 清洗：
```python
import re

def clean_tts_text(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)       # 加粗
    text = re.sub(r'\*(.+?)\*', r'\1', text)           # 斜体
    text = re.sub(r'`{1,3}[^`]*`{1,3}', '', text)     # 行内代码/代码块
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)    # 链接
    text = re.sub(r'#{1,6}\s*', '', text)              # 标题
    text = re.sub(r'[-*+]\s', '', text)                # 无序列表标记
    text = re.sub(r'\d+\.\s', '', text)                # 有序列表标记
    text = re.sub(r'\n{2,}', '\n', text)               # 多余空行
    text = re.sub(r'\|.*?\|', '', text)                # 表格
    text = re.sub(r'---+', '', text)                   # 分隔线
    return text.strip()
```

语速映射：
```python
RATE_MAP = {
    "0.5x": "-50%", "0.75x": "-25%", "1.0x": "+0%",
    "1.2x": "+20%", "1.5x": "+50%", "2.0x": "+100%",
}
```

### 2. 后端: STT 降级端点

端点：`POST /api/stt`

仅在浏览器 SpeechRecognition 不可用时调用。

```python
from faster_whisper import WhisperModel

# 模块级单例（首次加载模型 10-15s，之后复用）
_model: WhisperModel | None = None

async def stt_whisper(audio_bytes: bytes) -> str:
    global _model
    if _model is None:
        _model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = _model.transcribe(audio_bytes, language="zh")
    return "".join(s.text for s in segments)
```

请求：
```
POST /api/stt
Content-Type: multipart/form-data
file: <audio/webm blob>
```

响应：
```json
{"text": "帮我查一下年假余额"}
```

### 3. 前端: useVoiceInput.ts

```ts
function useVoiceInput() {
  const status = ref<'idle' | 'requesting' | 'calibrating' | 'recording' | 'processing' | 'unsupported'>('idle')
  const audioUrl: Ref<string | null> = ref(null)

  let mediaRecorder: MediaRecorder
  let audioContext: AudioContext
  let analyser: AnalyserNode
  let chunks: Blob[] = []
  let silenceTimer: ReturnType<typeof setTimeout> | null = null
  let noiseFloor: number = 0

  // 浏览器兼容检测
  const isSupported = computed(() =>
    !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder)
  )

  async function startRecording() {
    if (!isSupported.value) { status.value = 'unsupported'; return }
    status.value = 'requesting'

    // 1. 请求麦克风权限（浏览器内置降噪）
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        noiseSuppression: true,       // 环境降噪（空调/风扇）
        echoCancellation: true,       // 回声消除
        autoGainControl: true,        // 自动增益
        channelCount: 1,              // 单声道（STT 不需要立体声）
      }
    })

    // 2. 静音校准 1.5s → 计算 noiseFloor → 阈值 = noiseFloor * 2 (最低 8)
    status.value = 'calibrating'
    audioContext = new AudioContext()
    analyser = audioContext.createAnalyser()
    analyser.fftSize = 256
    audioContext.createMediaStreamSource(stream).connect(analyser)
    const samples: number[] = []
    const dataArray = new Uint8Array(analyser.frequencyBinCount)
    const calStart = Date.now()
    while (Date.now() - calStart < 1500) {
      analyser.getByteFrequencyData(dataArray)
      samples.push(dataArray.reduce((a, b) => a + b, 0) / dataArray.length)
      await sleep(100)
    }
    noiseFloor = Math.max(
      samples.reduce((a, b) => a + b, 0) / samples.length * 2,
      8
    )

    // 3. 启动录音 + 静音检测
    status.value = 'recording'
    chunks = []
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' })
    mediaRecorder.ondataavailable = (e) => chunks.push(e.data)
    mediaRecorder.start(100)  // 100ms 切片

    // 每 100ms 检测静音
    const checkSilence = () => {
      if (status.value !== 'recording') return
      analyser.getByteFrequencyData(dataArray)
      const vol = dataArray.reduce((a, b) => a + b, 0) / dataArray.length
      if (vol < noiseFloor) {
        if (!silenceTimer) silenceTimer = setTimeout(() => stopRecording(), 2000)
      } else {
        if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null }
      }
      requestAnimationFrame(checkSilence)
    }
    requestAnimationFrame(checkSilence)
  }

  async function stopRecording(): Promise<string> {
    status.value = 'processing'
    if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null }
    mediaRecorder?.stop()
    mediaRecorder?.stream.getTracks().forEach(t => t.stop())
    audioContext?.close()

    // 生成 blob URL 用于回放
    const blob = new Blob(chunks, { type: 'audio/webm' })
    if (audioUrl.value) URL.revokeObjectURL(audioUrl.value)
    audioUrl.value = URL.createObjectURL(blob)

    // STT: 优先浏览器 SpeechRecognition，不支持则后端 faster-whisper
    const text = await sttTranscribe(blob)

    status.value = 'idle'
    return text
  }

  async function sttTranscribe(blob: Blob): Promise<string> {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (SR) {
      return new Promise((resolve) => {
        const sr = new SR()
        sr.lang = 'zh-CN'
        sr.interimResults = false
        sr.onresult = (e) => resolve(e.results[0][0].transcript)
        sr.onerror = () => resolve('')  // 静默失败，不阻塞
        sr.start()
      })
    }
    // 后端兜底
    const form = new FormData()
    form.append('file', blob)
    const { data } = await client.post('/api/stt', form)
    return data.text || ''
  }

  function getPlaybackUrl(): string | null { return audioUrl.value }

  function cleanup() {
    if (audioUrl.value) URL.revokeObjectURL(audioUrl.value)
  }

  return { status, audioUrl, startRecording, stopRecording, getPlaybackUrl, cleanup, isSupported }
}
```

### 4. 前端: useVoiceTTS.ts

SSE fetch + Promise 顺序播放队列。即使网络导致片段乱序到达，前端也严格按 seq 顺序播放。

```ts
function useVoiceTTS() {
  // 状态：'idle' | 'playing' | 'interrupting'
  const status = ref<'idle' | 'playing' | 'interrupting'>('idle')
  const currentSentence = ref('')
  let abortController: AbortController | null = null
  let onDoneCallback: (() => void) | null = null  // 全部播完回调

  // 播放队列：Promise 链保证严格串行
  let playQueue: Promise<void> = Promise.resolve()
  let nextExpectedSeq = 0
  const pendingChunks: Map<number, { audio: string; text: string }> = new Map()

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
          const msg = JSON.parse(line.slice(6))
          handleSSEMessage(msg)
        } catch { /* skip parse errors */ }
      }
    }
  }

  function handleSSEMessage(msg: any) {
    switch (msg.type) {
      case 'tts_start':
        nextExpectedSeq = 0; pendingChunks.clear(); break
      case 'tts_sentence':
        // 乱序保护：提前到达的 seq 缓存，按序播放
        if (msg.seq === nextExpectedSeq) {
          playNext(msg)
        } else {
          pendingChunks.set(msg.seq, msg)
        }
        break
      case 'tts_end':
        // 等所有 pending chunk 播完
        playQueue = playQueue.then(() => {
          status.value = 'idle'
          onDoneCallback?.()
        })
        break
      case 'tts_error':
        console.error('TTS error:', msg.message)
        status.value = 'idle'
        break
    }
  }

  function playNext(msg: { seq: number; audio: string; text: string }) {
    playQueue = playQueue
      .then(() => playAudioChunk(msg))
      .then(() => {
        nextExpectedSeq++
        const next = pendingChunks.get(nextExpectedSeq)
        if (next) {
          pendingChunks.delete(nextExpectedSeq)
          playNext(next)  // 递归播下一个
        }
      })
  }

  async function playAudioChunk(msg: { audio: string; text: string }): Promise<void> {
    currentSentence.value = msg.text
    const audio = new Audio(`data:audio/mpeg;base64,${msg.audio}`)
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        await playWithTimeout(audio, 5000)  // 5s 超时，超时抛错跳过
        return
      } catch {
        if (attempt < 2) await sleep([2000, 4000][attempt])
      }
    }
  }

  function stopTTS() {
    if (status.value !== 'playing') return
    status.value = 'interrupting'
    abortController?.abort()
    pendingChunks.clear()
    playQueue = playQueue.then(() => {
      status.value = 'idle'
    })
  }

  return { status, currentSentence, requestTTS, stopTTS }
}
```

播放队列时序示意（考虑网络乱序）：

```
后端 SSE 推送:         前端 receive:           前端 play:

seq=0 ─────────────► enqueueAudio(0) ─────► [▶ 播 seq=0]
seq=1 ─────────────► enqueueAudio(1) ──┐     (seq=1 排后面)
seq=3 ──(早到!)───► pendingChunks[3]    │     (缓存等 2)
seq=2 ─────────────► enqueueAudio(2) ──┤     (排在 1 后面)
                                       │
                  playQueue:           ▼
                  0.then(play) ─► done ─► 1.then(play) ─► done
                  ─► 检查 pending[2] ✓ ─► 2.then(play) ─► done
                  ─► 检查 pending[3] ✓ ─► 3.then(play) ─► done ─► tts_end → idle
```

### 5. 前端: ChatView 集成

麦克风按钮状态和互斥逻辑参考上面状态机。关键集成点：

```ts
// ChatView.vue script setup
const voiceInput = useVoiceInput()
const voiceTTS = useVoiceTTS()
const voiceEnabled = useStorage('hr-voice-enabled', true)
const voiceRate = useStorage('hr-voice-rate', '1.2')

// 互斥：录音时禁用 TTS，TTS 时禁用麦克风
const micDisabled = computed(() =>
  !voiceInput.isSupported.value ||
  voiceInput.status.value === 'processing' ||
  voiceTTS.status.value === 'playing' ||
  voiceTTS.status.value === 'interrupting' ||
  chatStore.isStreaming
)

const ttsDisabled = computed(() =>
  voiceInput.status.value === 'recording' ||
  voiceInput.status.value === 'calibrating'
)

// Agent 回复完成后自动 TTS
watch(() => chatStore.isStreaming, (was, now) => {
  if (was && !now && voiceEnabled.value && chatStore.lastReply) {
    voiceTTS.requestTTS(chatStore.lastReply)
  }
})

// 发送消息时打断 TTS
function handleSend() {
  voiceTTS.stopTTS()
  // ... 原有发送逻辑
}
```

### 6. 飞书 Bot TTS 回复

`mcp_servers/feishu/tools.py` 新增函数：

```python
async def send_tts_audio(open_id: str, text: str, voice: str = "zh-CN-XiaoxiaoNeural"):
    """发送 TTS 语音消息到飞书用户。"""
    # 1. edge-tts 全文合成 MP3
    # 2. POST /im/v1/files → 上传音频 (file_type=opus)
    # 3. POST /im/v1/messages → msg_type=audio, content={"file_key":"..."}
    # 4. 同时发送文字消息（飞书音频消息不显示文字）
```

Agent 回复后判断用户偏好：
- `voice_enabled=true` → 发文字 + 音频两条消息
- `voice_enabled=false` → 只发文字（现状）

### 7. 语音偏好持久化

| 存储 | Key | 默认值 |
|------|-----|--------|
| Web localStorage | `hr-voice-enabled` | `true` |
| Web localStorage | `hr-voice-rate` | `1.2` |
| Web localStorage | `hr-voice-voice` | `zh-CN-XiaoxiaoNeural` |
| DB users 表 | `voice_enabled BOOLEAN` | `TRUE` |
| DB users 表 | `voice_rate VARCHAR(8)` | `+20%` |

### 8. 可配语音

| Voice ID | 性别 | 风格 |
|----------|------|------|
| zh-CN-XiaoxiaoNeural | 女 | 温柔自然（默认） |
| zh-CN-YunxiNeural | 男 | 沉稳专业 |
| zh-CN-XiaoyiNeural | 女 | 活泼 |
| zh-CN-YunjianNeural | 男 | 新闻播报 |

### 9. 兼容性矩阵

| 浏览器 | 语音输入 | STT 引擎 | TTS 播放 |
|--------|---------|----------|----------|
| Chrome 80+ | ✅ 全功能 | SpeechRecognition | ✅ |
| Edge 80+ | ✅ 全功能 | SpeechRecognition | ✅ |
| Firefox | ⚠️ 无 STT | 后端 faster-whisper | ✅ |
| Safari | ⚠️ 无 STT | 后端 faster-whisper | ✅ |

### 10. 安全性

- TTS 端点需要 Bearer token 鉴权（复用现有 auth 中间件）
- STT 上传音频不持久化，转文字后即删
- 飞书音频通过 tenant_access_token 鉴权
- edge-tts 调用微软公开接口，无 API key 泄露风险

## 涉及文件

| 文件 | 操作 | 估算行数 |
|------|------|---------|
| `frontend/src/composables/useVoiceInput.ts` | 新增 | ~130 行 |
| `frontend/src/composables/useVoiceTTS.ts` | 新增 | ~100 行 |
| `frontend/src/views/ChatView.vue` | 修改 | +50 行 |
| `frontend/src/stores/chat.ts` | 修改 | +5 行 (audioUrl) |
| `backend/routes/tts.py` | 新增 | ~120 行 |
| `backend/routes/stt.py` | 新增 | ~40 行 |
| `backend/main.py` | 修改 | +3 行 (注册路由) |
| `mcp_servers/feishu/tools.py` | 修改 | +45 行 |
| `db/schema.sql` | 修改 | +2 行 (users 表加列) |
| `requirements.txt` | 修改 | +2 (edge-tts, faster-whisper) |

总计 ~500 行，纯增量。

## 依赖

- `edge-tts>=6.0.0` — Microsoft Edge 免费 TTS（MIT）
- `faster-whisper>=1.0.0` — 本地 STT 引擎（MIT，CTranslate2 运行时）
- 浏览器原生 API：`MediaRecorder`, `AudioContext`, `AnalyserNode`, `SpeechRecognition`, `Audio`
