"""M4: supersede index + status/superseded_by invariant CHECK

Revision ID: 0004_m4_supersede
Revises: 0003_m3_review_log
Create Date: 2026-06-02

The supersede flow is application-layer logic (`memoir.store.supersede_claim`).
This migration only adds the schema-level invariants and indexes that make
the §6 correction story safe:

- Index on `superseded_by` so `claim_history`'s backward walk
  ("which claim points at me as its successor?") is cheap.
- CHECK ties `status = 'superseded'` to `superseded_by IS NOT NULL` so
  the two columns can't drift out of sync — any code path that sets one
  without the other gets rejected at the DB.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004_m4_supersede"
down_revision: Union[str, None] = "0003_m3_review_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_claims_superseded_by",
        "claims",
        ["superseded_by"],
        postgresql_where="superseded_by IS NOT NULL",
    )
    op.create_check_constraint(
        "claims_supersede_consistency",
        "claims",
        "(status = 'superseded') = (superseded_by IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("claims_supersede_consistency", "claims", type_="check")
    op.drop_index("ix_claims_superseded_by", table_name="claims")
