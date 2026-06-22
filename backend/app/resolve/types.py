import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MergeCandidate:
    """A pair of live claims whose embeddings are close enough to invite
    editor confirmation.

    Note carefully: the pair is *symmetric* (similarity is mutual).
    `claim_a_id` / `claim_b_id` are ordered by id to give a stable
    representation; the editor decides which is the winner and which
    becomes the merged loser. We never imply a direction.
    """

    claim_a_id: uuid.UUID
    claim_b_id: uuid.UUID
    similarity: float


@dataclass(frozen=True, slots=True)
class EntityRef:
    """A single entity reference extracted from a claim's text.

    `kind` is a soft hint (date / person / place / org) — §9 keeps
    entity kinds loose until structure surfaces from real data.
    `canonical` is the form the linker decided is canonical for this
    subject. Two claims that mention the same canonical form get linked
    to the same row in `entities`.
    """

    kind: str
    canonical: str
