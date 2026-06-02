"""Unit tests for the M2 extraction layer.

Pure unit — no DB. Covers the load-bearing Pydantic contract and the
deterministic RuleExtractor used as the M2 baseline.
"""
import uuid

import pytest
from pydantic import ValidationError

from memoir.extract import ExtractedClaim, Extractor, LLMExtractor, RuleExtractor
from memoir.segment import Segment


def _segment(text: str) -> Segment:
    uid = uuid.uuid4()
    return Segment(
        session_id=uuid.uuid4(),
        speaker="subject",
        text=text,
        char_start=0,
        char_end=len(text),
        utterance_ids=(uid,),
    )


def test_extracted_claim_requires_at_least_one_source() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ExtractedClaim(
            text="ungrounded",
            claim_type="fact",
            confidence=0.5,
            source_utterance_ids=[],
        )
    # min_length=1 fires under the "too_short" code in Pydantic v2
    assert "source_utterance_ids" in str(exc_info.value)


def test_extracted_claim_confidence_bounds() -> None:
    uid = uuid.uuid4()
    with pytest.raises(ValidationError):
        ExtractedClaim(
            text="x",
            claim_type="fact",
            confidence=1.5,
            source_utterance_ids=[uid],
        )
    with pytest.raises(ValidationError):
        ExtractedClaim(
            text="x",
            claim_type="fact",
            confidence=-0.1,
            source_utterance_ids=[uid],
        )


def test_rule_extractor_emits_grounded_claim_for_year_mention() -> None:
    seg = _segment("Tôi sinh năm 1962 ở Detroit.")
    claims = RuleExtractor().extract(seg)
    assert len(claims) == 1
    claim = claims[0]
    assert claim.claim_type == "fact"
    assert claim.source_utterance_ids == list(seg.utterance_ids)
    assert "1962" in claim.text


def test_rule_extractor_silent_when_no_year() -> None:
    """§9 'trích ít hơn' — when the rule has nothing to say, it says nothing."""
    seg = _segment("Tôi thích đọc sách lắm.")
    assert RuleExtractor().extract(seg) == []


def test_rule_extractor_handles_vietnamese_diacritics() -> None:
    seg = _segment("Đến năm 1975 chúng tôi rời Đà Nẵng.")
    claims = RuleExtractor().extract(seg)
    assert len(claims) == 1 and "1975" in claims[0].text


def test_protocol_structural_match() -> None:
    """Both shipped extractors satisfy the Extractor protocol structurally
    (`isinstance` works because the protocol is `runtime_checkable`).
    """
    assert isinstance(RuleExtractor(), Extractor)
    assert isinstance(LLMExtractor(), Extractor)


def test_llm_extractor_is_stub_in_m2() -> None:
    """LLM integration is deliberately deferred to a post-M2 follow-up."""
    with pytest.raises(NotImplementedError, match="post-M2"):
        LLMExtractor().extract(_segment("Năm 1962."))
