"""Pydantic schemas for the Review UI HTTP layer.

The response shapes are designed for §1's "hiển thị cạnh câu gốc":
`ClaimOut` carries its grounding utterances inline so a UI never needs
a second round-trip to render a reviewable card.
"""
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _ActorBody(BaseModel):
    """Common shape: every state-mutating action records who did it."""

    actor: str = Field(min_length=1, description="Identifier of the reviewer.")


class AcceptRequest(_ActorBody):
    pass


class RejectRequest(_ActorBody):
    reason: str | None = Field(
        default=None,
        description="Optional rationale, stored in the audit payload.",
    )


class EditRequest(_ActorBody):
    text: str = Field(
        min_length=1,
        description=(
            "New canonical wording. The previous text is preserved in the "
            "audit payload (review_log.payload.previous_text)."
        ),
    )


class FlagRequest(_ActorBody):
    reason: str | None = Field(
        default=None,
        description="Optional rationale, stored in the audit payload.",
    )


class UtteranceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    speaker: str
    text: str
    char_start: int
    char_end: int


class ClaimOut(BaseModel):
    """A reviewable claim, served alongside the utterances it cites."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    subject_id: uuid.UUID
    text: str
    claim_type: str | None
    confidence: float
    status: str
    superseded_by: uuid.UUID | None
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    sources: list[UtteranceOut]


class ReviewLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    claim_id: uuid.UUID
    action: str
    payload: dict[str, Any] | None
    actor: str
    created_at: datetime
