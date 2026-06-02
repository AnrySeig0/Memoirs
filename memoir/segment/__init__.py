"""Step 3: Segmentation — cắt theo lượt nói + ngữ cảnh, giữ nguyên offset.

Lỗi âm thầm hay gặp: chunking làm lệch offset → mất truy vết.
Dùng Unicode codepoint nhất quán giữa lưu trữ và hiển thị.

M2 ships identity segmentation (1 utterance = 1 segment). A
`segment_by_turn_window(max_chars=…)` glue policy is the natural next
step but doesn't change the M2 grounding contract — `Segment` already
carries the list of source `utterance_ids`.
"""
from memoir.segment.turn import segment_by_utterance
from memoir.segment.types import Segment

__all__ = ["Segment", "segment_by_utterance"]
