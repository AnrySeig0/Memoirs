"""Step 1-2: Ingestion + ASR/diarization, ghi substrate bất biến.

WhisperX (ASR + forced alignment), pyannote.audio (diarization).
Text-only: vẫn phải gán char_start/char_end + speaker.

Offsets sử dụng Unicode codepoint nhất quán (Python `len(str)`).
"""
from app.ingest.text import (
    IngestResult,
    TURN_SEPARATOR,
    ingest_text_transcript,
    normalized_transcript,
)
from app.ingest.types import Turn

__all__ = [
    "IngestResult",
    "TURN_SEPARATOR",
    "Turn",
    "ingest_text_transcript",
    "normalized_transcript",
]
