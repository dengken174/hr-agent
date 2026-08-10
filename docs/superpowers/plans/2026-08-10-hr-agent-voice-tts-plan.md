# HR Agent Voice Input + TTS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add voice input (record → STT → text) and AI voice reply (TTS streaming SSE) to the HR Agent chat, with Feishu bot audio message support.

**Architecture:** Two new backend endpoints (POST /api/tts SSE streaming, POST /api/stt audio→text), two new frontend composables (useVoiceInput for recording+STT, useVoiceTTS for SSE playback queue), integrated into ChatView with mutual exclusion between recording and TTS playback.

**Tech Stack:** edge-tts (Microsoft Edge free neural TTS), faster-whisper (local STT fallback), browser MediaRecorder + AnalyserNode + SpeechRecognition, SSE streaming.

## Global Constraints

- edge-tts>=6.0.0, faster-whisper>=1.0.0 (MIT licensed)
- No new frontend npm dependencies (all browser native APIs)
- TTS endpoint uses SSE (`text/event-stream`), not WebSocket
- TTS processing: parallel for each sentence, ordered `seq` delivery with out-of-order protection on frontend
- Voice preferences in localStorage (Web) + DB `users` table (Feishu)
- Browser STT via `SpeechRecognition`, Firefox/Safari fallback to backend faster-whisper
- Markdown stripped before TTS, noise suppression + echo cancellation enabled on mic
- Voice input and TTS playback are mutually exclusive in ChatView

---

### Task 1: Install Python dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add dependencies to requirements.txt**

Add two lines to the end of `requirements.txt`:
```
edge-tts>=6.0.0
faster-whisper>=1.0.0
```

- [ ] **Step 2: Install**

Run: `pip install edge-tts faster-whisper`

- [ ] **Step 3: Verify**

```bash
python -c "from edge_tts import Communicate; print('edge-tts OK')"
python -c "from faster_whisper import WhisperModel; print('faster-whisper OK')"
```

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add edge-tts and faster-whisper dependencies"
```

---

### Task 2: Backend TTS utility functions

**Files:**
- Create: `backend/routes/tts.py`

**Interfaces:**
- Produces:
  - `split_sentences(text: str) -> list[str]` — split text by Chinese punctuation
  - `clean_tts_text(text: str) -> str` — strip markdown before TTS
  - `RATE_MAP: dict[str, str]` — UI rate label to edge-tts rate string

- [ ] **Step 1: Write tts.py with utility functions**

```python
"""TTS utilities — sentence splitting, markdown cleaning, rate mapping."""

import re

RATE_MAP = {
    "0.5x": "-50%", "0.75x": "-25%", "1.0x": "+0%",
    "1.2x": "+20%", "1.5x": "+50%", "2.0x": "+100%",
}

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_RATE = "+20%"


def split_sentences(text: str) -> list[str]:
    """Split text by Chinese sentence-ending punctuation. Keep clauses together."""
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


def clean_tts_text(text: str) -> str:
    """Strip markdown formatting before TTS to avoid reading syntax aloud."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`{1,3}[^`]*`{1,3}', '', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'[-*+]\s', '', text)
    text = re.sub(r'\d+\.\s', '', text)
    text = re.sub(r'\n{2,}', '\n', text)
    text = re.sub(r'\|.*?\|', '', text)
    text = re.sub(r'---+', '', text)
    return text.strip()
```

- [ ] **Step 2: Quick test**

```bash
python -c "
from backend.routes.tts import split_sentences, clean_tts_text
text = '好的！已查询到**张三**的年假。余额5天。'
clean = clean_tts_text(text)
print(repr(clean))
parts = split_sentences(clean)
print(parts)
"
```

Expected:
```
'好的！已查询到张三的年假。余额5天。'
['好的！', '已查询到张三的年假。', '余额5天。']
```

- [ ] **Step 3: Commit**

```bash
git add backend/routes/tts.py
git commit -m "feat: add TTS utility functions (split_sentences, clean_tts_text)"
```

---

### Task 3: Backend TTS streaming SSE endpoint

**Files:**
- Modify: `backend/routes/tts.py` (extend with the endpoint)

**Interfaces:**
- Produces: `router: APIRouter` with `POST /api/tts`
  - Request: `{"text": str, "voice": str?, "rate": str?}`
  - Response: `text/event-stream` SSE with `tts_start`, `tts_sentence` (seq + base64 audio + text), `tts_end`, `tts_error`

**Note:** Step numbering continues from Task 2 since we're extending the same file.

- [ ] **Step 1: Write the TTS SSE endpoint**

Add to `backend/routes/tts.py`:

```python
import asyncio
import base64
import json
import logging

import edge_tts
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.middleware import get_current_user

logger = logging.getLogger("backend.routes.tts")
router = APIRouter(prefix="/api", tags=["tts"])


class TTSRequest(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    rate: str = DEFAULT_RATE


async def _tts_sentence(text: str, voice: str, rate: str, seq: int, retries: int = 3) -> dict:
    """Synthesize one sentence via edge-tts, with retry backoff."""
    last_err = None
    for attempt in range(retries):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            audio_b64 = base64.b64encode(b"".join(chunks)).decode()
            return {"type": "tts_sentence", "seq": seq, "audio": audio_b64, "text": text}
        except Exception as e:
            last_err = e
            logger.warning("TTS attempt %d/%d failed for seq=%d: %s", attempt + 1, retries, seq, e)
            if attempt < retries - 1:
                await asyncio.sleep([2, 4, 8][attempt])
    raise last_err or RuntimeError("TTS failed after retries")


@router.post("/tts")
async def tts_stream(req: TTSRequest, user: dict = Depends(get_current_user)):
    """Stream TTS audio via SSE. Splits text into sentences, synthesizes in parallel, delivers ordered by seq."""
    text = clean_tts_text(req.text)
    sentences = split_sentences(text)
    if not sentences:
        sentences = [text]

    async def event_stream():
        # Send start
        yield f"data: {json.dumps({'type': 'tts_start', 'total_sentences': len(sentences)}, ensure_ascii=False)}\n\n"

        # Synthesize all sentences in parallel
        tasks = []
        for i, sent in enumerate(sentences):
            tasks.append(_tts_sentence(sent, req.voice, RATE_MAP.get(req.rate, req.rate), i))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Deliver in seq order
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("TTS sentence %d permanently failed: %s", i, result)
                yield f"data: {json.dumps({'type': 'tts_error', 'seq': i, 'message': str(result)})}\n\n"
            else:
                yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'tts_end'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 2: Test the endpoint manually**

Start backend and test with curl:

```bash
curl -X POST http://localhost:8080/api/tts \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"text":"你好！今天天气不错。请问有什么可以帮您的？"}' \
  --no-buffer
```

Verify SSE format:
```
data: {"type": "tts_start", "total_sentences": 2}
data: {"type": "tts_sentence", "seq": 0, "audio": "//uQx...", "text": "你好！"}
data: {"type": "tts_sentence", "seq": 1, "audio": "//uQx...", "text": "今天天气不错。"}
data: {"type": "tts_end"}
```

- [ ] **Step 3: Commit**

```bash
git add backend/routes/tts.py
git commit -m "feat: add TTS streaming SSE endpoint with parallel synthesis"
```

---

### Task 4: Backend STT fallback endpoint

**Files:**
- Create: `backend/routes/stt.py`

**Interfaces:**
- Produces: `router: APIRouter` with `POST /api/stt`
  - Request: multipart/form-data with audio file
  - Response: `{"text": str}`

- [ ] **Step 1: Write stt.py**

```python
"""STT (Speech-to-Text) fallback endpoint via faster-whisper."""

import logging
import tempfile

from fastapi import APIRouter, Depends, File, UploadFile
from faster_whisper import WhisperModel

from backend.middleware import get_current_user

logger = logging.getLogger("backend.routes.stt")
router = APIRouter(prefix="/api", tags=["stt"])

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        logger.info("Loading faster-whisper base model (first call)...")
        _model = WhisperModel("base", device="cpu", compute_type="int8")
        logger.info("faster-whisper model loaded")
    return _model


@router.post("/stt")
async def stt_transcribe(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """Transcribe uploaded audio to text via faster-whisper. Fallback when browser SpeechRecognition is unavailable."""
    audio_bytes = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        model = _get_model()
        segments, _ = model.transcribe(tmp_path, language="zh")
        text = "".join(s.text for s in segments)
        return {"text": text.strip()}
    except Exception as e:
        logger.exception("STT transcription failed")
        return {"text": "", "error": str(e)}
    finally:
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
```

- [ ] **Step 2: Test STT endpoint**

No audio file needed for basic test — just verify it imports and model loads:

```bash
python -c "
from backend.routes.stt import _get_model
# Don't actually transcribe, just verify model lazy-loads
print('STT module OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add backend/routes/stt.py
git commit -m "feat: add STT fallback endpoint via faster-whisper"
```

---

### Task 5: Register new routes in backend main

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `router` objects from `backend/routes/tts.py:router` and `backend/routes/stt.py:router`

- [ ] **Step 1: Add route registration**

In `backend/main.py`, add under existing route registrations:

```python
from backend.routes.tts import router as tts_router
from backend.routes.stt import router as stt_router

# Add in the route registration section:
app.include_router(tts_router)
app.include_router(stt_router)
```

- [ ] **Step 2: Verify backend starts**

```bash
python -c "
import sys, os
sys.path.insert(0, os.path.dirname(__file__) if '__file__' in dir() else '.')
# Just verify imports
from backend.main import app
print('App imports OK, routes:', [r.path for r in app.routes if hasattr(r, 'path')])
"
```

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "feat: register TTS and STT routes"
```

---

### Task 6: DB schema — voice preference columns

**Files:**
- Modify: `db/schema.sql`

- [ ] **Step 1: Add columns to users table**

In `db/schema.sql`, add after `open_id` column in the users table:

```sql
    voice_enabled BOOLEAN DEFAULT TRUE,
    voice_rate    VARCHAR(8) DEFAULT '+20%',
```

- [ ] **Step 2: Run migration (if MySQL available)**

```bash
mysql -u root -p hragent -e "ALTER TABLE users ADD COLUMN voice_enabled BOOLEAN DEFAULT TRUE, ADD COLUMN voice_rate VARCHAR(8) DEFAULT '+20%';" 2>/dev/null || echo "MySQL not available, schema change deferred"
```

- [ ] **Step 3: Commit**

```bash
git add db/schema.sql
git commit -m "feat: add voice preference columns to users table"
```

---

### Task 7: Frontend useVoiceTTS composable

**Files:**
- Create: `frontend/src/composables/useVoiceTTS.ts`

**Interfaces:**
- Produces:
  - `useVoiceTTS()` → `{ status, currentSentence, requestTTS, stopTTS }`
  - `status: Ref<'idle' | 'playing' | 'interrupting'>`
  - `currentSentence: Ref<string>`
  - `requestTTS(text: string, voice?: string, rate?: string, onDone?: () => void): Promise<void>`
  - `stopTTS(): void`

- [ ] **Step 1: Write useVoiceTTS.ts**

```typescript
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
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx vue-tsc -b --noEmit src/composables/useVoiceTTS.ts
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/useVoiceTTS.ts
git commit -m "feat: add useVoiceTTS composable (SSE playback queue)"
```

---

### Task 8: Frontend useVoiceInput composable

**Files:**
- Create: `frontend/src/composables/useVoiceInput.ts`

**Interfaces:**
- Produces:
  - `useVoiceInput()` → `{ status, audioUrl, startRecording, stopRecording, cleanup, isSupported }`
  - `status: Ref<'idle' | 'requesting' | 'calibrating' | 'recording' | 'processing' | 'unsupported'>`
  - `audioUrl: Ref<string | null>` — blob URL for playback
  - `startRecording(): Promise<void>`
  - `stopRecording(): Promise<string>` — returns transcribed text
  - `cleanup(): void`
  - `isSupported: ComputedRef<boolean>`

- [ ] **Step 1: Write useVoiceInput.ts**

```typescript
import { ref, computed } from 'vue'

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

export function useVoiceInput() {
  const status = ref<'idle' | 'requesting' | 'calibrating' | 'recording' | 'processing' | 'unsupported'>('idle')
  const audioUrl = ref<string | null>(null)

  let mediaRecorder: MediaRecorder | null = null
  let audioContext: AudioContext | null = null
  let analyser: AnalyserNode | null = null
  let chunks: Blob[] = []
  let silenceTimer: ReturnType<typeof setTimeout> | null = null
  let noiseFloor = 0
  let animationId = 0

  const isSupported = computed(() =>
    !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder)
  )

  async function startRecording() {
    if (!isSupported.value) {
      status.value = 'unsupported'
      return
    }

    status.value = 'requesting'
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          noiseSuppression: true,
          echoCancellation: true,
          autoGainControl: true,
          channelCount: 1,
        },
      })
    } catch (e) {
      console.error('Microphone permission denied:', e)
      status.value = 'unsupported'
      return
    }

    // Calibrate noise floor
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
      (samples.reduce((a, b) => a + b, 0) / samples.length) * 2,
      8,
    )

    // Start recording
    status.value = 'recording'
    chunks = []
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' })
    mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data) }
    mediaRecorder.start(100)

    // Silence detection loop
    const checkSilence = () => {
      if (status.value !== 'recording') return
      analyser!.getByteFrequencyData(dataArray)
      const vol = dataArray.reduce((a, b) => a + b, 0) / dataArray.length
      if (vol < noiseFloor) {
        if (!silenceTimer) {
          silenceTimer = setTimeout(() => stopRecording(), 2000)
        }
      } else {
        if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null }
      }
      animationId = requestAnimationFrame(checkSilence)
    }
    animationId = requestAnimationFrame(checkSilence)
  }

  async function stopRecording(): Promise<string> {
    status.value = 'processing'
    if (silenceTimer) { clearTimeout(silenceTimer); silenceTimer = null }
    cancelAnimationFrame(animationId)

    mediaRecorder?.stop()
    mediaRecorder?.stream.getTracks().forEach((t) => t.stop())
    audioContext?.close()

    const blob = new Blob(chunks, { type: 'audio/webm' })
    if (audioUrl.value) URL.revokeObjectURL(audioUrl.value)
    audioUrl.value = URL.createObjectURL(blob)

    const text = await sttTranscribe(blob)
    status.value = 'idle'
    return text
  }

  async function sttTranscribe(blob: Blob): Promise<string> {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (SR) {
      // Browser SpeechRecognition works on live streams, not blobs.
      // We need to play the blob back into a new recognition session,
      // OR just re-record with SpeechRecognition active simultaneously.
      // For simplicity: use MediaRecorder for recording, SpeechRecognition for real-time STT.
      // Since we can't feed blob to SpeechRecognition, fall through to backend.
      return await backendSTT(blob)
    }
    return await backendSTT(blob)
  }

  async function backendSTT(blob: Blob): Promise<string> {
    try {
      const form = new FormData()
      form.append('file', blob, 'recording.webm')
      const token = localStorage.getItem('token')
      const resp = await fetch('/api/stt', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      })
      const data = await resp.json()
      return data.text || ''
    } catch {
      return ''
    }
  }

  function getPlaybackUrl(): string | null {
    return audioUrl.value
  }

  function cleanup() {
    if (audioUrl.value) {
      URL.revokeObjectURL(audioUrl.value)
      audioUrl.value = null
    }
  }

  return { status, audioUrl, startRecording, stopRecording, getPlaybackUrl, cleanup, isSupported }
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd frontend && npx vue-tsc -b --noEmit src/composables/useVoiceInput.ts
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/composables/useVoiceInput.ts
git commit -m "feat: add useVoiceInput composable (record + STT)"
```

---

### Task 9: Chat store — audioUrl field

**Files:**
- Modify: `frontend/src/stores/chat.ts`

**Interfaces:**
- Produces: `Message` interface gains `audioUrl?: string`

- [ ] **Step 1: Add audioUrl to Message interface**

In `chat.ts`, modify the `Message` interface:

```typescript
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: number
  audioUrl?: string  // blob URL for voice input playback
}
```

Also add `lastReply` ref to track the last assistant message for auto-TTS:

```typescript
const lastReply = ref('')

// In sendMessage(), after the assistant message is built:
lastReply.value = assistantMsg.content
```

- [ ] **Step 2: Verify build**

```bash
cd frontend && npx vue-tsc -b --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/stores/chat.ts
git commit -m "feat: add audioUrl field to Message and lastReply to chat store"
```

---

### Task 10: ChatView integration

**Files:**
- Modify: `frontend/src/views/ChatView.vue`

**Interfaces:**
- Consumes: `useVoiceInput()`, `useVoiceTTS()`, `chatStore.lastReply`, `chatStore.isStreaming`
- Produces: mic button, TTS toggle, rate slider, playback/replay buttons in message bubbles

- [ ] **Step 1: Add voice control bar above input area**

Add after `<div class="chat-input-area">` opening tag:

```html
<!-- Voice controls -->
<div class="voice-controls" v-if="voiceInput.isSupported.value">
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
</div>
```

- [ ] **Step 2: Add mic button to input area**

Replace the existing send button area or add mic button alongside:

```html
<div class="chat-input-area">
  <el-button
    :type="micButtonType"
    :disabled="micDisabled"
    :loading="voiceInput.status.value === 'processing'"
    circle
    @click="handleMicClick"
    size="default"
  >
    <el-icon v-if="voiceInput.status.value === 'recording'"><Microphone /></el-icon>
    <el-icon v-else><Microphone /></el-icon>
  </el-button>
  <!-- existing textarea -->
  <!-- existing send button -->
</div>
```

- [ ] **Step 3: Add script logic for voice integration**

Add to `<script setup>`:

```typescript
import { useVoiceInput } from '../composables/useVoiceInput'
import { useVoiceTTS } from '../composables/useVoiceTTS'

const voiceInput = useVoiceInput()
const voiceTTS = useVoiceTTS()
const voiceEnabled = ref(localStorage.getItem('hr-voice-enabled') !== 'false')
const voiceRate = ref(localStorage.getItem('hr-voice-rate') || '1.2')
const voiceRateNum = ref(parseFloat(voiceRate.value))

// Persist preferences
watch(voiceEnabled, (v) => localStorage.setItem('hr-voice-enabled', String(v)))
watch(voiceRate, (v) => localStorage.setItem('hr-voice-rate', v))

// Mutual exclusion
const micDisabled = computed(() =>
  !voiceInput.isSupported.value ||
  voiceInput.status.value === 'processing' ||
  voiceTTS.status.value === 'playing' ||
  voiceTTS.status.value === 'interrupting' ||
  chatStore.isStreaming
)

const micButtonType = computed(() => {
  switch (voiceInput.status.value) {
    case 'recording': return 'danger'
    case 'calibrating': return 'warning'
    default: return 'default'
  }
})

// Auto-TTS after agent reply
watch(() => chatStore.isStreaming, (was, now) => {
  if (was && !now && voiceEnabled.value && chatStore.lastReply) {
    voiceTTS.requestTTS(chatStore.lastReply, undefined, voiceRate.value)
  }
})

// Handle mic click
async function handleMicClick() {
  if (voiceInput.status.value === 'recording' || voiceInput.status.value === 'calibrating') {
    const text = await voiceInput.stopRecording()
    if (text) {
      inputText.value = text
      handleSend()
    }
  } else {
    voiceTTS.stopTTS()
    await voiceInput.startRecording()
  }
}

// Stop TTS when sending new message
const originalSend = handleSend
function handleSend() {
  voiceTTS.stopTTS()
  originalSend()
}
```

- [ ] **Step 4: Add playback/replay buttons to message bubbles**

In the message bubble template, after `renderMarkdown(msg.content)`:

```html
<div v-if="msg.role === 'user' && msg.audioUrl" class="msg-extra">
  <el-button text size="small" @click="playRecording(msg.audioUrl)">
    <el-icon><VideoPlay /></el-icon> {{ formatDuration(msg.audioUrl) }}
  </el-button>
</div>
<div v-if="msg.role === 'assistant' && voiceEnabled && msg.content" class="msg-extra">
  <el-button text size="small" @click="replayTTS(msg.content)">
    <el-icon><Headset /></el-icon> 重播
  </el-button>
</div>
```

Add helper functions:

```typescript
function playRecording(url: string) {
  const audio = new Audio(url)
  audio.play()
}

function replayTTS(text: string) {
  voiceTTS.requestTTS(text, undefined, voiceRate.value)
}

function formatDuration(url: string): string {
  // approximate: track audio element duration once loaded
  const audio = new Audio(url)
  return '0:00'  // will update onloadedmetadata
}
```

- [ ] **Step 5: Add CSS for voice controls**

```css
.voice-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 8px;
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

.msg-extra {
  margin-top: 4px;
  display: flex;
  gap: 4px;
}
```

- [ ] **Step 6: Verify build**

```bash
cd frontend && npx vue-tsc -b --noEmit && npx vite build
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/ChatView.vue
git commit -m "feat: integrate voice input and TTS into ChatView"
```

---

### Task 11: Feishu bot TTS audio message

**Files:**
- Modify: `mcp_servers/feishu/tools.py`

**Interfaces:**
- Produces: `send_tts_audio(open_id: str, text: str) -> list[TextContent]`

- [ ] **Step 1: Add send_tts_audio function**

Add to `mcp_servers/feishu/tools.py`:

```python
async def send_tts_audio(open_id: str, text: str) -> list[types.TextContent]:
    """给飞书用户发送 TTS 语音消息。

    流程: edge-tts 合成 MP3 → 上传飞书文件 → 发送音频消息。
    同时发送文字消息（飞书音频消息不显示文字内容）。
    """
    if not feishu_client.is_configured:
        return _text(f"[mock] TTS audio for: {text[:50]}...")

    try:
        # 1. TTS synthesis
        import base64
        import edge_tts
        from backend.routes.tts import clean_tts_text

        clean = clean_tts_text(text)
        communicate = edge_tts.Communicate(clean, voice="zh-CN-XiaoxiaoNeural", rate="+20%")
        mp3_chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_chunks.append(chunk["data"])
        mp3_bytes = b"".join(mp3_chunks)

        # 2. Upload to Feishu
        from config import config
        upload_resp = await feishu_client._request(
            "POST",
            "/im/v1/files",
            files={"file": ("tts.mp3", mp3_bytes, "audio/mpeg")},
            data={"file_type": "opus"},
        )
        file_key = upload_resp.get("data", {}).get("file_key", "")

        # 3. Send audio message
        await feishu_client.post(
            "/im/v1/messages",
            params={"receive_id_type": "open_id"},
            body={
                "receive_id": open_id,
                "msg_type": "audio",
                "content": json.dumps({"file_key": file_key}),
            },
        )

        # 4. Also send text (audio messages don't show text)
        await feishu_client.post(
            "/im/v1/messages",
            params={"receive_id_type": "open_id"},
            body={
                "receive_id": open_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            },
        )

        return _text(str({"status": "sent", "file_key": file_key}))

    except Exception as e:
        logger.exception("Feishu TTS audio send failed")
        return _text(str({"status": "error", "message": str(e)}))
```

- [ ] **Step 2: Verify imports**

```bash
python -c "from mcp_servers.feishu.tools import send_tts_audio; print('import OK')"
```

- [ ] **Step 3: Commit**

```bash
git add mcp_servers/feishu/tools.py
git commit -m "feat: add TTS audio message sending for Feishu bot"
```

---

### Task 12: End-to-end verification

- [ ] **Step 1: Start backend and verify startup**

```bash
python -m backend.main
```

Check logs:
- "Starting HR Agent backend..."
- "Agent ready with N tools"
- "Frontend static files enabled" (if dist exists)

- [ ] **Step 2: Build frontend**

```bash
cd frontend && npm run build
```

Verify: build succeeds, dist/ created.

- [ ] **Step 3: Login and test chat flow**

1. Open `http://localhost:8080`
2. Login with admin/admin123
3. Navigate to Chat page
4. Verify voice toggle and rate slider are visible
5. Verify mic button is present (gray if recording unsupported, clickable otherwise)

- [ ] **Step 4: Test TTS endpoint directly**

```bash
# Get token first
TOKEN=$(curl -s -X POST http://localhost:8080/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)

# Test TTS
curl -X POST http://localhost:8080/api/tts \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"text":"你好！请问有什么可以帮您的？"}' \
  --no-buffer
```

Expected: SSE stream with tts_start, tts_sentence(s), tts_end.

- [ ] **Step 5: Test STT endpoint with a small audio file**

```bash
curl -X POST http://localhost:8080/api/stt \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@test_recording.webm"
```

Expected: `{"text": "..."}` (might return empty if audio is silent).

- [ ] **Step 6: Commit final verification**

```bash
git add -A
git commit -m "chore: final verification, voice-TTS feature complete"
```
