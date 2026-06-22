"""Unit tests for the M2 extraction layer.

Pure unit — no DB. Covers the load-bearing Pydantic contract and the
deterministic RuleExtractor used as the M2 baseline.

The live LLMExtractor test at the bottom hits a real OpenAI-compatible
endpoint when `MEMOIR_LLM_TEST_BASE_URL` is set; otherwise it is skipped
(same posture as the Postgres-required tests in conftest).
"""
import uuid

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.extract import ExtractedClaim, Extractor, LLMExtractor, RuleExtractor
from app.segment import Segment


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


@pytest.mark.skipif(
    not get_settings().llm_effective_test_base_url,
    reason="MEMOIR_LLM_TEST_BASE_URL (or MEMOIR_LLM_BASE_URL) not set — live vLLM endpoint test skipped",
)
def test_llm_extractor_against_live_endpoint() -> None:
    """Smoke-test the LLMExtractor against a real OpenAI-compatible server.

    Reads `llm_test_*` settings (env: MEMOIR_LLM_TEST_BASE_URL,
    MEMOIR_LLM_TEST_MODEL, MEMOIR_LLM_TEST_API_KEY). Each falls back to
    the main `llm_*` setting so a developer who's already configured the
    LLM extractor for normal use doesn't need separate test vars.

    Asserts the contract, not the content: every returned object is an
    `ExtractedClaim`, every claim cites at least one utterance ID drawn
    from the segment, and confidence sits in [0, 1].
    """
    settings = get_settings()
    seg = _segment("Tôi sinh năm 1962 ở Detroit. Bố tôi là kỹ sư.")
    extractor = LLMExtractor(
        model=settings.llm_effective_test_model,
        base_url=settings.llm_effective_test_base_url,
        api_key=settings.llm_effective_test_api_key,
    )
    claims = extractor.extract(seg)

    assert isinstance(claims, list)
    allowed = set(seg.utterance_ids)
    for c in claims:
        assert isinstance(c, ExtractedClaim)
        assert c.source_utterance_ids, "min_length=1 must hold"
        assert set(c.source_utterance_ids).issubset(allowed), (
            "LLM cited an ID outside the segment — wrapper should have filtered"
        )
        assert 0.0 <= c.confidence <= 1.0
