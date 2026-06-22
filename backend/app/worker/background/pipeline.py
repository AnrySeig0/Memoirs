"""Background grounded-pipeline task.

Runs the full write path (ingest → segment → extract → store) and then
embeds the resulting claims, all inside one short-lived session that
commits on success / rolls back on error (`session_scope`). This is the
shape a Celery/Prefect worker would call; today it can be invoked directly
from a CLI command or a thread.

Embedding defaults to `DeterministicEmbedder` so a CI/dev box without the
`ml` extras still works; production passes a `BGEEmbedder`. Superseded
claims are never embedded — `set_claim_embedding` refuses them — but a fresh
pipeline run only produces live `pending` claims, so every claim id it
returns is embeddable.
"""
import logging
import uuid
from collections.abc import Sequence

from app.db.models import Claim
from app.db.session import session_scope
from app.repositories.claim import set_claim_embedding
from app.services.ingest import Turn
from app.services.pipeline import PipelineResult, run_text_pipeline
from app.services.resolve import DeterministicEmbedder, Embedder

logger = logging.getLogger(__name__)


def ingest_and_embed(
    *,
    subject_id: uuid.UUID,
    session_no: int,
    turns: Sequence[Turn],
    storage_uri: str,
    embedder: Embedder | None = None,
) -> PipelineResult:
    """Ingest a transcript, extract grounded claims, and embed them.

    Opens its own session and commits the whole batch atomically.
    """
    embedder = embedder or DeterministicEmbedder()
    with session_scope() as db:
        result = run_text_pipeline(
            db,
            subject_id=subject_id,
            session_no=session_no,
            turns=turns,
            storage_uri=storage_uri,
        )
        for claim_id in result.claim_ids:
            claim = db.get(Claim, claim_id)
            if claim is None:
                continue
            set_claim_embedding(db, claim_id=claim_id, vector=embedder.embed(claim.text))
        logger.info(
            "pipeline_done session=%s claims=%d",
            result.session_id,
            len(result.claim_ids),
        )
        return result
