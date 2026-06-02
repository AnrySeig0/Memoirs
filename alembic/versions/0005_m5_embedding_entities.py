"""M5: pgvector embedding column + entities tables

Revision ID: 0005_m5_embedding_entities
Revises: 0004_m4_supersede
Create Date: 2026-06-02

Step 5 (embedding) + Step 6 (entity linking) schema. The actual
dedup-candidates query is a SELECT in `memoir.resolve.find_merge_candidates`
that never writes; the merge action itself reuses M4's supersede shape
plus an audit `action='merge'` row.

Notes:
- We do NOT create an IVFFlat / HNSW index. At V1 scale a sequential
  cosine scan is fine; §9 "không phức tạp hóa sớm". A perf-tuning PR
  can add it later without touching application code.
- `embedding` is NULLABLE. Claims start unembedded; an orchestrator
  job (post-M5) calls `set_claim_embedding` once an embedder produces
  the vector. Unembedded claims are simply absent from dedup candidates.
- BGE-m3 (the model §5 calls out) is 1024-dimensional. We pin that
  here; switching embedders later means a new migration that drops +
  recreates the column.
- entities / claim_entities are intentionally LOOSE: kind is free-form
  text per §9 (only a soft hint list lives in resolve/entity.py).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0005_m5_embedding_entities"
down_revision: Union[str, None] = "0004_m4_supersede"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1024


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "claims",
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
    )

    op.create_table(
        "entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("canonical", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # One canonical entity per (subject, kind, canonical) — get-or-create
    # semantics rely on this.
    op.create_index(
        "ix_entities_subject_kind_canonical",
        "entities",
        ["subject_id", "kind", "canonical"],
        unique=True,
    )

    op.create_table(
        "claim_entities",
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claims.id"),
            primary_key=True,
        ),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_claim_entities_entity",
        "claim_entities",
        ["entity_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_claim_entities_entity", table_name="claim_entities")
    op.drop_table("claim_entities")
    op.drop_index("ix_entities_subject_kind_canonical", table_name="entities")
    op.drop_table("entities")
    op.drop_column("claims", "embedding")
    # vector extension intentionally kept — other tables may use it later.
