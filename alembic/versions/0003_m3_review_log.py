"""M3: review_log + append-only audit trigger

Revision ID: 0003_m3_review_log
Revises: 0002_m2_claims
Create Date: 2026-06-02

The audit trail behind every editor action. Same append-only discipline
as utterances/claim_sources: once an action is logged it can never be
rewritten or hidden. If a reviewer reverses themselves the log gains
another row; the previous one stays.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_m3_review_log"
down_revision: Union[str, None] = "0002_m2_claims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claims.id"),
            nullable=False,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "action IN ('accept','reject','edit','flag','merge','supersede')",
            name="review_log_action_check",
        ),
    )
    op.create_index(
        "ix_review_log_claim_time",
        "review_log",
        ["claim_id", "created_at"],
    )

    # Audit log is append-only. Editors can disagree with themselves and
    # write another row, but no row ever vanishes or changes.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION review_log_no_modify() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'review_log is append-only: % not allowed', TG_OP
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER review_log_block_update
            BEFORE UPDATE ON review_log
            FOR EACH ROW EXECUTE FUNCTION review_log_no_modify();
        """
    )
    op.execute(
        """
        CREATE TRIGGER review_log_block_delete
            BEFORE DELETE ON review_log
            FOR EACH ROW EXECUTE FUNCTION review_log_no_modify();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS review_log_block_delete ON review_log")
    op.execute("DROP TRIGGER IF EXISTS review_log_block_update ON review_log")
    op.execute("DROP FUNCTION IF EXISTS review_log_no_modify()")
    op.drop_index("ix_review_log_claim_time", table_name="review_log")
    op.drop_table("review_log")
