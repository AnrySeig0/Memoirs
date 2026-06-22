"""Dedup candidate discovery — read-only.

`find_merge_candidates` is a SELECT. It NEVER writes. §1 Merge safety
test asserts that no claim row is modified by this call. The editor
must explicitly call `merge_claim` to commit a merge.

The similarity metric is cosine, computed in pgvector via `<=>` (cosine
distance). For L2-normalized vectors — which both DeterministicEmbedder
and BGEEmbedder produce — `similarity = 1 - distance` is in `[-1, 1]`.
"""
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session as OrmSession

from app.services.resolve.types import MergeCandidate

DEFAULT_THRESHOLD = 0.85
DEFAULT_LIMIT = 50


def find_merge_candidates(
    db: OrmSession,
    *,
    subject_id: uuid.UUID,
    threshold: float = DEFAULT_THRESHOLD,
    limit: int = DEFAULT_LIMIT,
) -> list[MergeCandidate]:
    """Return high-similarity pairs of live, embedded claims.

    A "live" claim has `status != 'superseded'`. An "embedded" claim has
    a non-null `embedding`. Pairs are deduplicated by `a.id < b.id` and
    sorted by similarity descending. No DB mutation.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0,1], got {threshold}")
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")

    stmt = text(
        """
        SELECT
            a.id AS a_id,
            b.id AS b_id,
            1 - (a.embedding <=> b.embedding) AS similarity
        FROM claims a
        JOIN claims b
          ON a.id < b.id
         AND a.subject_id = b.subject_id
        WHERE a.subject_id = :subject_id
          AND a.status <> 'superseded'
          AND b.status <> 'superseded'
          AND a.embedding IS NOT NULL
          AND b.embedding IS NOT NULL
          AND 1 - (a.embedding <=> b.embedding) >= :threshold
        ORDER BY similarity DESC
        LIMIT :limit
        """
    ).bindparams(
        bindparam("subject_id"),
        bindparam("threshold"),
        bindparam("limit"),
    )
    rows = db.execute(
        stmt, {"subject_id": subject_id, "threshold": threshold, "limit": limit}
    ).all()
    return [
        MergeCandidate(claim_a_id=r.a_id, claim_b_id=r.b_id, similarity=float(r.similarity))
        for r in rows
    ]
