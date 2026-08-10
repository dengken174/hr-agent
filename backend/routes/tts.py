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
