import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Segment:
    """A contiguous chunk of a session transcript ready for extraction.

    For M2, segmentation is identity (1 utterance = 1 segment) so the
    surface stays trivial and offset-preserving. A later iteration can
    add `segment_by_turn_window(max_chars)` that glues consecutive
    utterances; consumers only need to know that `text == transcript
    [char_start:char_end]` for the session in question.

    `utterance_ids` lists every utterance backing this segment, in
    document order. Extractors copy this list into the
    `source_utterance_ids` of any claim they emit — that's the link
    that makes a claim grounded.
    """

    session_id: uuid.UUID
    speaker: str
    text: str
    char_start: int
    char_end: int
    utterance_ids: tuple[uuid.UUID, ...]
