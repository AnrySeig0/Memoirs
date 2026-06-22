from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Turn:
    """A single speaker turn for text-only ingestion.

    `text` must be the verbatim wording — segmentation/normalisation happens
    later (Step 3, M2). For audio ingestion, `ts_start_ms` / `ts_end_ms` are
    populated from WhisperX alignment; text-only ingestion leaves them None.
    """

    speaker: str
    text: str
    ts_start_ms: int | None = None
    ts_end_ms: int | None = None
