"""M1: substrate (sources, sessions, utterances) + append-only trigger

Revision ID: 0001_m1_substrate
Revises:
Create Date: 2026-06-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_m1_substrate"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("kind IN ('audio', 'text')", name="sources_kind_check"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id"),
            nullable=False,
        ),
        sa.Column("session_no", sa.Integer(), nullable=False),
        sa.Column("recorded_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_sessions_subject_session_no",
        "sessions",
        ["subject_id", "session_no"],
        unique=True,
    )

    op.create_table(
        "utterances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("speaker", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("ts_start_ms", sa.Integer(), nullable=True),
        sa.Column("ts_end_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("char_start >= 0", name="utterances_char_start_nonneg"),
        sa.CheckConstraint("char_end >= char_start", name="utterances_char_order"),
    )
    op.create_index("ix_utterances_session", "utterances", ["session_id", "char_start"])

    # Append-only enforcement at the DB layer.
    # The repository also refuses to expose update/delete; the trigger is the
    # belt-and-braces guarantee for anything that hits the DB directly.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION utterances_no_modify() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'utterances is append-only: % not allowed', TG_OP
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER utterances_block_update
            BEFORE UPDATE ON utterances
            FOR EACH ROW EXECUTE FUNCTION utterances_no_modify();
        """
    )
    op.execute(
        """
        CREATE TRIGGER utterances_block_delete
            BEFORE DELETE ON utterances
            FOR EACH ROW EXECUTE FUNCTION utterances_no_modify();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS utterances_block_delete ON utterances")
    op.execute("DROP TRIGGER IF EXISTS utterances_block_update ON utterances")
    op.execute("DROP FUNCTION IF EXISTS utterances_no_modify()")
    op.drop_index("ix_utterances_session", table_name="utterances")
    op.drop_table("utterances")
    op.drop_index("ix_sessions_subject_session_no", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("sources")
