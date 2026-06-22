"""End-to-end pipeline test for the M2 acceptance criterion.

§7: "Mọi claim có ≥1 `claim_sources`; claim không nguồn bị loại/flag."

Runs the full path: ingest text turns → segment → RuleExtractor →
insert_claim_with_sources → assert every claim row has ≥1 claim_sources
row pointing back at a real utterance in the same session.
"""
import uuid

from sqlalchemy import select, func

from app.services.extract import RuleExtractor
from app.services.ingest import Turn, ingest_text_transcript
from app.services.segment import segment_by_utterance
from app.db.models import Claim, ClaimSource, Utterance
from app.repositories.claim import insert_claim_with_sources


def test_no_orphan_claims_after_full_pipeline(db_session) -> None:
    subject_id = uuid.uuid4()
    # Mix of turns: some have year mentions, some don't. RuleExtractor
    # should under-extract on the silent ones (§9), not invent grounding.
    turns = [
        Turn("interviewer", "Ông sinh năm bao nhiêu?"),
        Turn("subject", "Tôi sinh năm 1962 ở Detroit, Michigan."),
        Turn("interviewer", "Còn vợ ông?"),
        Turn("subject", "Vợ tôi sinh sau đó vài năm."),  # no 19xx/20xx
        Turn("subject", "Chúng tôi cưới năm 1985."),
    ]
    ingest = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=1,
        turns=turns,
        storage_uri="s3://memoir/test/m2_pipeline.txt",
    )
    db_session.commit()

    valid_utterance_ids = set(ingest.utterance_ids)

    extractor = RuleExtractor()
    written_ids: list[uuid.UUID] = []
    for segment in segment_by_utterance(db_session, ingest.session_id):
        for claim in extractor.extract(segment):
            row = insert_claim_with_sources(
                db_session,
                subject_id=subject_id,
                text=claim.text,
                claim_type=claim.claim_type,
                confidence=claim.confidence,
                source_utterance_ids=claim.source_utterance_ids,
            )
            written_ids.append(row.id)
    db_session.commit()

    # RuleExtractor finds years in turns 2 and 5 only → 2 claims.
    assert len(written_ids) == 2

    # M2 acceptance: no orphans.
    orphan_count = db_session.execute(
        select(func.count())
        .select_from(Claim)
        .outerjoin(ClaimSource, Claim.id == ClaimSource.claim_id)
        .where(ClaimSource.claim_id.is_(None))
    ).scalar_one()
    assert orphan_count == 0

    # Every grounding row points at a real utterance from this session.
    sources = db_session.execute(select(ClaimSource)).scalars().all()
    assert len(sources) >= len(written_ids)
    for src in sources:
        assert src.utterance_id in valid_utterance_ids

    # Spot-check the link goes back to the original verbatim text.
    for claim_id in written_ids:
        joined = db_session.execute(
            select(Utterance.text)
            .join(ClaimSource, ClaimSource.utterance_id == Utterance.id)
            .where(ClaimSource.claim_id == claim_id)
        ).scalars().all()
        # The rule extractor pulls one segment per claim, so each claim
        # has exactly one source utterance in this fixture.
        assert any("năm" in t for t in joined)
