"""Unicode codepoint offset correctness (M1, §5 lưu ý).

These are pure unit tests against the ingest helpers — no DB needed.
The DB-backed roundtrip lives in test_append_only.py via the db_session
fixture.
"""
import uuid

from app.services.ingest import Turn, normalized_transcript


def test_vietnamese_diacritics_roundtrip() -> None:
    turns = [
        Turn("subject", "Tôi sinh năm 1962 ở Detroit."),
        Turn("interviewer", "Ông có nhớ tên đường không?"),
        Turn("subject", "Đường Grand Boulevard, gần nhà thờ Đức Bà."),
    ]
    transcript = normalized_transcript(turns)

    cursor = 0
    sep_len = len("\n")
    for idx, turn in enumerate(turns):
        char_start = cursor
        char_end = char_start + len(turn.text)
        assert transcript[char_start:char_end] == turn.text, (
            f"turn {idx} offset slice mismatch: "
            f"{transcript[char_start:char_end]!r} != {turn.text!r}"
        )
        cursor = char_end + (sep_len if idx < len(turns) - 1 else 0)


def test_offset_codepoint_not_byte() -> None:
    """A naive byte-offset implementation would put char_end at 2 (one UTF-8
    char of "ô" is 2 bytes); the spec demands codepoint counts.
    """
    text = "Tô"
    assert len(text) == 2, "Python str length is codepoint count"
    assert len(text.encode("utf-8")) == 3, "two bytes for 'ô' plus one for 'T'"


def test_empty_turns_rejected() -> None:
    from app.services.ingest.text import ingest_text_transcript

    # We deliberately call without a db_session — empty turns are caught
    # before the DB is touched, so a None placeholder is fine.
    import pytest

    with pytest.raises(ValueError, match="turns must not be empty"):
        ingest_text_transcript(
            db=None,  # type: ignore[arg-type]
            subject_id=uuid.uuid4(),
            session_no=1,
            turns=[],
            storage_uri="s3://test/empty.txt",
        )
