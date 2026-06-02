"""M2: claims + claim_sources (grounded extraction)

Revision ID: 0002_m2_claims
Revises: 0001_m1_substrate
Create Date: 2026-06-02

Schema for §4 claim/grounding tables. Embedding column and entities/
claim_entities tables are deferred to M5 — keeping this migration focused
on the grounding contract.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_m2_claims"
down_revision: Union[str, None] = "0001_m1_substrate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CLAIM_STATUSES = ("pending", "accepted", "rejected", "edited", "flagged", "superseded")


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        # claim_type intentionally left as free-form Text per §9 — "Không
        # xây ontology phong phú vội"; soft enum hint enforced in Pydantic.
        sa.Column("claim_type", sa.Text(), nullable=True),
        sa.Column("confidence", sa.REAL(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "superseded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claims.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reviewed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="claims_confidence_range",
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','rejected','edited','flagged','superseded')",
            name="claims_status_check",
        ),
        # Loose self-supersede sanity: a claim should not point at itself.
        sa.CheckConstraint(
            "superseded_by IS NULL OR superseded_by <> id",
            name="claims_no_self_supersede",
        ),
    )
    op.create_index("ix_claims_subject_status", "claims", ["subject_id", "status"])

    op.create_table(
        "claim_sources",
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claims.id"),
            primary_key=True,
        ),
        sa.Column(
            "utterance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("utterances.id"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_claim_sources_utterance",
        "claim_sources",
        ["utterance_id"],
    )

    # Append-only on grounding rows. §4 rule "Insert claim mà không có ít
    # nhất 1 dòng claim_sources → từ chối" is enforced at the repository
    # layer (in one transaction). At the DB layer we additionally forbid
    # rewriting / deleting a grounding row once written — provenance once
    # established does not move. (Hard-deleting a claim itself remains
    # possible during M2/M3 development; the M4 supersede flow doesn't
    # delete either, it sets status='superseded'.)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION claim_sources_no_modify() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'claim_sources is append-only: % not allowed', TG_OP
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER claim_sources_block_update
            BEFORE UPDATE ON claim_sources
            FOR EACH ROW EXECUTE FUNCTION claim_sources_no_modify();
        """
    )
    op.execute(
        """
        CREATE TRIGGER claim_sources_block_delete
            BEFORE DELETE ON claim_sources
            FOR EACH ROW EXECUTE FUNCTION claim_sources_no_modify();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS claim_sources_block_delete ON claim_sources")
    op.execute("DROP TRIGGER IF EXISTS claim_sources_block_update ON claim_sources")
    op.execute("DROP FUNCTION IF EXISTS claim_sources_no_modify()")
    op.drop_index("ix_claim_sources_utterance", table_name="claim_sources")
    op.drop_table("claim_sources")
    op.drop_index("ix_claims_subject_status", table_name="claims")
    op.drop_table("claims")
