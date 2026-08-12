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
    """Strip markdown formatting before TTS. Keeps content, removes syntax."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`{1,3}[^`]*`{1,3}', '', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'[-*+]\s', '', text)
    text = re.sub(r'\d+\.\s', '', text)
    text = re.sub(r'---+', '', text)
    text = re.sub(r'\n{2,}', '\n', text)

    # Tables: strip | characters, keep content between them
    # "| 满1年不满10年 | 5天 |" → "满1年不满10年, 5天"
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        if '|' in line:
            cells = [c.strip() for c in line.split('|') if c.strip()]
            if cells:
                # Skip pure-separator rows like "---|---"
                if all(re.match(r'^-+$', c) for c in cells):
                    continue
                cleaned_lines.append(', '.join(cells))
        else:
            cleaned_lines.append(line)
    text = '\n'.join(cleaned_lines)

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

    # Normalize rate: accept "1.2", "1.2x", "+20%" formats
    rate = RATE_MAP.get(req.rate, req.rate)
    if rate not in ("-50%", "-25%", "+0%", "+20%", "+50%", "+100%"):
        rate = DEFAULT_RATE  # fallback to default on unrecognized format

    async def event_stream():
        # Send start
        yield f"data: {json.dumps({'type': 'tts_start', 'total_sentences': len(sentences)}, ensure_ascii=False)}\n\n"

        # Synthesize in parallel, deliver each as it completes (not wait for all)
        tasks = {i: asyncio.ensure_future(_tts_sentence(sent, req.voice, rate, i)) for i, sent in enumerate(sentences)}

        for coro in asyncio.as_completed(tasks.values()):
            try:
                result = await coro
                yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
            except Exception:
                # Find which seq failed by inspecting all completed tasks
                for seq, t in tasks.items():
                    if t.done() and t.exception() is not None:
                        err = t.exception()
                        logger.error("TTS sentence %d permanently failed: %s", seq, err)
                        yield f"data: {json.dumps({'type': 'tts_error', 'seq': seq, 'message': str(err)})}\n\n"

        yield f"data: {json.dumps({'type': 'tts_end'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
