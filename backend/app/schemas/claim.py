"""Claim request/response schemas for the Review UI.

`ClaimOut` carries its grounding utterances inline (§1 "hiển thị cạnh câu
gốc") so a UI never needs a second round-trip to render a reviewable card.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.base import ActorBody
from app.schemas.utterance import UtteranceOut


class AcceptRequest(ActorBody):
    pass


class RejectRequest(ActorBody):
    reason: str | None = Field(
        default=None,
        description="Optional rationale, stored in the audit payload.",
    )


class EditRequest(ActorBody):
    text: str = Field(
        min_length=1,
        description=(
            "New canonical wording. The previous text is preserved in the "
            "audit payload (review_log.payload.previous_text)."
        ),
    )


class FlagRequest(ActorBody):
    reason: str | None = Field(
        default=None,
        description="Optional rationale, stored in the audit payload.",
    )


class SupersedeRequest(ActorBody):
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


class MergeRequest(ActorBody):
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


class ClaimHistoryEntry(BaseModel):
    """One link in a correction chain — the §6 "đã nói gì → sửa thành gì → khi nào".

    Returned in chronological order by `GET /claims/{id}/history`. For the
    leaf (still-current claim) the `superseded_*` fields are null.
    """

    claim: ClaimOut
    superseded_at: datetime | None
    superseded_by_actor: str | None
    note: str | None


class MergeCandidateOut(BaseModel):
    """A pair of claims surfaced as a possible merge.

    §1 hard rule: surfacing a candidate is NOT a merge. The editor must
    `POST /claims/{loser}/merge` to commit anything.
    """

    claim_a_id: uuid.UUID
    claim_b_id: uuid.UUID
    similarity: float
