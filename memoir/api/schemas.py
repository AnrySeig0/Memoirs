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


class SupersedeRequest(_ActorBody):
    new_claim_id: uuid.UUID = Field(
        description=(
            "The new claim that carries the corrected statement. Must be a "
            "live claim (not itself superseded) and not already the "
            "successor of another claim (many-to-one is a merge, M5)."
        ),
    )
    note: str | None = Field(
        default=None,
        description="Optional editor note, stored in the audit payload.",
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


class ClaimHistoryEntry(BaseModel):
    """One link in a correction chain — the §6 "đã nói gì → sửa thành gì → khi nào".

    Returned in chronological order by `GET /claims/{id}/history`. For the
    leaf (still-current claim) the `superseded_*` fields are null.
    """

    claim: ClaimOut
    superseded_at: datetime | None
    superseded_by_actor: str | None
    note: str | None


class MergeRequest(_ActorBody):
    """Editor confirms that `loser_id` (URL) is the same fact as `winner_id`."""

    winner_claim_id: uuid.UUID = Field(
        description=(
            "The live claim that survives the merge. The loser becomes "
            "superseded and points at this winner. Many losers MAY share a "
            "winner — that is the M5 relaxation versus M4 supersede."
        ),
    )
    similarity: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description=(
            "Optional cosine similarity from the dedup query — captured in "
            "the audit payload so the merge decision is reviewable."
        ),
    )
    note: str | None = Field(default=None)


class MergeCandidateOut(BaseModel):
    """A pair of claims surfaced as a possible merge.

    §1 hard rule: surfacing a candidate is NOT a merge. The editor must
    `POST /claims/{loser}/merge` to commit anything.
    """

    claim_a_id: uuid.UUID
    claim_b_id: uuid.UUID
    similarity: float
