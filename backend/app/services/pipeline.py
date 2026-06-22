"""Grounded extraction pipeline: ingest → segment → extract → store.

This is the write path the M2 acceptance test exercises end-to-end. It is
deliberately the same sequence of stage calls, factored into one function
so a background worker (Phase 5) or a CLI command can run it without
duplicating the wiring. Every claim it writes is grounded — a claim with
zero source utterances is rejected by `insert_claim_with_sources` before
it can reach the DB (§7 "không tồn tại claim mồ côi").

The caller owns the transaction: pass a live session and commit when the
whole batch succeeds, so a mid-run failure rolls back cleanly.
"""
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session as OrmSession

from app.repositories.claim import insert_claim_with_sources
from app.services.extract import Extractor, RuleExtractor
from app.services.ingest import Turn, ingest_text_transcript
from app.services.segment import segment_by_utterance


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """What one pipeline run produced — useful for logging / smoke checks."""

    session_id: uuid.UUID
    utterance_ids: list[uuid.UUID]
    claim_ids: list[uuid.UUID]


def run_text_pipeline(
    db: OrmSession,
    *,
    subject_id: uuid.UUID,
    session_no: int,
    turns: Sequence[Turn],
    storage_uri: str,
    extractor: Extractor | None = None,
) -> PipelineResult:
    """Run the full grounded pipeline for one text transcript.

    Stages:
      1. ingest  — write the immutable substrate (source/session/utterances)
         with Unicode-codepoint offsets.
      2. segment — one utterance = one segment (identity segmentation, M2).
      3. extract — `extractor` (defaults to the deterministic RuleExtractor)
         turns each segment into grounded `ExtractedClaim`s.
      4. store   — `insert_claim_with_sources` writes claim + claim_sources
         atomically; un-grounded output is impossible by construction.

    Does NOT commit — the caller decides the transaction boundary.
    """
    extractor = extractor or RuleExtractor()

    ingest = ingest_text_transcript(
        db,
        subject_id=subject_id,
        session_no=session_no,
        turns=list(turns),
        storage_uri=storage_uri,
    )

    claim_ids: list[uuid.UUID] = []
    for segment in segment_by_utterance(db, ingest.session_id):
        for claim in extractor.extract(segment):
            row = insert_claim_with_sources(
                db,
                subject_id=subject_id,
                text=claim.text,
                claim_type=claim.claim_type,
                confidence=claim.confidence,
                source_utterance_ids=claim.source_utterance_ids,
            )
            claim_ids.append(row.id)

    return PipelineResult(
        session_id=ingest.session_id,
        utterance_ids=list(ingest.utterance_ids),
        claim_ids=claim_ids,
    )
