"""Unit + DB tests for the M5 entity layer."""
import uuid

import pytest

from app.ingest import Turn, ingest_text_transcript
from app.resolve import EntityLinker, EntityRef, RuleEntityLinker
from app.store import (
    Entity,
    get_or_create_entity,
    insert_claim_with_sources,
    link_claim_to_entities,
)


# --- pure unit -------------------------------------------------------------


def test_rule_entity_linker_finds_year() -> None:
    refs = RuleEntityLinker().link("Tôi sinh năm 1962 ở Detroit.")
    kinds_canon = {(r.kind, r.canonical) for r in refs}
    assert ("date", "1962") in kinds_canon


def test_rule_entity_linker_finds_proper_noun_with_diacritics() -> None:
    refs = RuleEntityLinker().link("Chúng tôi rời Đà Nẵng năm 1975.")
    canonicals = {r.canonical for r in refs}
    assert "Đà" in canonicals or "Nẵng" in canonicals or any(c.startswith("Đà") for c in canonicals)
    assert "1975" in {r.canonical for r in refs}


def test_rule_entity_linker_dedups_within_text() -> None:
    refs = RuleEntityLinker().link("Năm 1962, Detroit. Năm 1962 lần nữa, Detroit.")
    # 1 year + 1 proper noun, deduplicated.
    canonicals = [r.canonical for r in refs]
    assert canonicals.count("1962") == 1
    assert canonicals.count("Detroit") == 1


def test_rule_entity_linker_empty_text() -> None:
    assert RuleEntityLinker().link("") == []


def test_linker_satisfies_protocol() -> None:
    assert isinstance(RuleEntityLinker(), EntityLinker)


# --- DB-backed --------------------------------------------------------------


def test_get_or_create_entity_is_idempotent(db_session) -> None:
    subject_id = uuid.uuid4()
    a = get_or_create_entity(db_session, subject_id=subject_id, kind="date", canonical="1962")
    b = get_or_create_entity(db_session, subject_id=subject_id, kind="date", canonical="1962")
    db_session.commit()
    assert a.id == b.id


def test_get_or_create_entity_distinct_per_subject(db_session) -> None:
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    a = get_or_create_entity(db_session, subject_id=s1, kind="date", canonical="1962")
    b = get_or_create_entity(db_session, subject_id=s2, kind="date", canonical="1962")
    db_session.commit()
    assert a.id != b.id


def test_get_or_create_entity_empty_fields_rejected(db_session) -> None:
    with pytest.raises(ValueError):
        get_or_create_entity(db_session, subject_id=uuid.uuid4(), kind="", canonical="1962")
    with pytest.raises(ValueError):
        get_or_create_entity(db_session, subject_id=uuid.uuid4(), kind="date", canonical="")


def test_link_claim_to_entities_idempotent(db_session) -> None:
    subject_id = uuid.uuid4()
    ingest = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=1,
        turns=[Turn("subject", "Năm 1962, ở Detroit.")],
        storage_uri="s3://memoir/test/m5_entity.txt",
    )
    claim = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved to Detroit in 1962.",
        claim_type="event",
        confidence=0.8,
        source_utterance_ids=list(ingest.utterance_ids),
    )
    db_session.commit()

    refs = RuleEntityLinker().link(claim.text)
    entity_ids = [
        get_or_create_entity(
            db_session, subject_id=subject_id, kind=r.kind, canonical=r.canonical
        ).id
        for r in refs
    ]
    db_session.commit()

    first = link_claim_to_entities(db_session, claim_id=claim.id, entity_ids=entity_ids)
    db_session.commit()
    second = link_claim_to_entities(db_session, claim_id=claim.id, entity_ids=entity_ids)
    db_session.commit()
    assert first == len(entity_ids)
    assert second == 0  # idempotent
