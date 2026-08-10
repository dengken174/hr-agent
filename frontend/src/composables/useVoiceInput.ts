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
    typeof navigator.mediaDevices?.getUserMedia === 'function' &&
    typeof window.MediaRecorder === 'function'
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
