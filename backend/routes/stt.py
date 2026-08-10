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
