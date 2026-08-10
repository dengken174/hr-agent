"""TTS utilities — sentence splitting, markdown cleaning, rate mapping, and SSE streaming endpoint."""

import asyncio
import base64
import json
import logging
import re

import edge_tts
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.middleware import get_current_user

logger = logging.getLogger("backend.routes.tts")

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


# ── Router ──────────────────────────────────────────────────────────

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
