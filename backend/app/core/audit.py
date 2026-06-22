"""Provenance audit — the §1 M6 contract in code.

`audit_provenance(db, claim_id)` walks the full chain a claim must
satisfy and returns a `ProvenanceResult` whose `ok` field is True iff
every M1–M5 invariant holds for this specific claim.

The audit is deliberately a SELECT-only function. It NEVER writes —
running it on production traffic costs reads and nothing else. The
function lives in `app.core.audit` rather than `tests/` because it's the
same query an operator would issue to verify data quality on a live
deployment.

Checks per claim, in order:

1. Claim row exists.
2. ≥1 row in `claim_sources` (M2 hard rule — "không tồn tại claim mồ
   côi").
3. Each `claim_sources.utterance_id` resolves to a real `utterances`
   row (DB FK enforces this; we check anyway in case the FK is ever
   relaxed).
4. Each utterance has well-formed offsets (`char_start >= 0`,
   `char_end >= char_start`, span length == codepoint length of text).
5. Each utterance's session and source resolve (FK belt-and-braces).
6. Reconstruction: building the normalized session transcript
   (`"\n".join(turn.text)`) and slicing `[char_start:char_end]`
   recovers the utterance text verbatim. This is the §5 lưu ý about
   Unicode-codepoint offsets, verified end-to-end at audit time.
7. If `claim.status == 'edited'`, there's ≥1 `review_log` row with
   `action='edit'` whose payload contains a recoverable
   `previous_text` (the original wording is reachable from audit even
   though the claim row carries the new text).
8. If `claim.status == 'superseded'`, `claim.superseded_by` resolves,
   the successor is a live claim or itself superseded (chain valid),
   and the chain contains no cycles.
9. If status indicates a review happened (anything other than
   'pending'), ≥1 matching `review_log` row exists.

Any failure adds an entry to `issues` and flips `ok` to False. A
claim with multiple issues collects all of them — we surface the full
picture rather than short-circuiting, so an operator sees every break
in one pass.
"""
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.db.models import (
    Claim,
    ClaimSource,
    ReviewLog,
    Session as SessionRow,
    Source,
    Utterance,
)


@dataclass(frozen=True)
class ProvenanceResult:
    """Outcome of `audit_provenance` for one claim.

    `ok` is True iff `issues` is empty. We still expose both so callers
    that don't care about the reason can use `ok` directly while
    operators get the full list when something breaks.
    """

    claim_id: uuid.UUID
    ok: bool
    issues: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.ok:
            return f"{self.claim_id}: OK"
        return f"{self.claim_id}: {len(self.issues)} issue(s): " + "; ".join(self.issues)


def _reconstruct_session_transcript(db: OrmSession, session_id: uuid.UUID) -> str:
    """Rebuild the normalized session transcript from its utterance rows.

    Must match the convention `memoir.ingest.text.ingest_text_transcript`
    used at write time: utterances in `char_start` order joined by
    `TURN_SEPARATOR` (newline). The import is deferred to call time to
    avoid a circular dep (memoir.ingest.text imports the repository,
    which lives next to this module).
    """
    from app.services.ingest.text import TURN_SEPARATOR

    rows = db.execute(
        select(Utterance.text)
        .where(Utterance.session_id == session_id)
        .order_by(Utterance.char_start)
    ).scalars().all()
    return TURN_SEPARATOR.join(rows)


# Statuses where the claim is considered "reviewed" — they must have at
# least one matching review_log row by the time M6 audits them.
_REVIEWED_STATUSES = frozenset(
    {"accepted", "rejected", "edited", "flagged", "superseded"}
)


def audit_provenance(db: OrmSession, *, claim_id: uuid.UUID) -> ProvenanceResult:
    """Run all M1–M5 invariant checks for `claim_id` and report.

    Read-only. Never raises for normal failures (a missing claim → ok
    False with an issue, not a `ClaimNotFound`); callers can treat the
    result uniformly.
    """
    issues: list[str] = []

    claim = db.get(Claim, claim_id)
    if claim is None:
        return ProvenanceResult(claim_id=claim_id, ok=False, issues=["claim row missing"])

    # --- 2: ≥1 claim_sources -------------------------------------------------
    sources = db.execute(
        select(ClaimSource).where(ClaimSource.claim_id == claim_id)
    ).scalars().all()
    if not sources:
        issues.append("claim has zero claim_sources rows (M2 orphan)")
        # Without sources we can't run the offset/reconstruction checks.
        return ProvenanceResult(claim_id=claim_id, ok=False, issues=issues)

    # --- 3-6: per-source chain + reconstruction ------------------------------
    # Cache transcripts per session — many sources share the same session.
    transcript_cache: dict[uuid.UUID, str] = {}
    for cs in sources:
        utt = db.get(Utterance, cs.utterance_id)
        if utt is None:
            issues.append(f"claim_sources.utterance_id {cs.utterance_id} does not resolve")
            continue
        if utt.char_start < 0 or utt.char_end < utt.char_start:
            issues.append(
                f"utterance {utt.id} has invalid offsets "
                f"({utt.char_start}, {utt.char_end})"
            )
        if utt.char_end - utt.char_start != len(utt.text):
            issues.append(
                f"utterance {utt.id}: offset span "
                f"({utt.char_end - utt.char_start}) ≠ codepoint length "
                f"({len(utt.text)})"
            )

        sess = db.get(SessionRow, utt.session_id)
        if sess is None:
            issues.append(f"utterance {utt.id} references missing session {utt.session_id}")
            continue
        src = db.get(Source, sess.source_id)
        if src is None:
            issues.append(f"session {sess.id} references missing source {sess.source_id}")

        # Reconstruction — the §5 lưu ý in test form.
        if sess.id not in transcript_cache:
            transcript_cache[sess.id] = _reconstruct_session_transcript(db, sess.id)
        transcript = transcript_cache[sess.id]
        if (
            utt.char_start > len(transcript)
            or utt.char_end > len(transcript)
            or transcript[utt.char_start : utt.char_end] != utt.text
        ):
            issues.append(
                f"utterance {utt.id}: transcript[{utt.char_start}:{utt.char_end}] "
                "does not equal utterance.text"
            )

    # --- 7: edit history reachable from review_log ---------------------------
    if claim.status == "edited":
        edit_logs = db.execute(
            select(ReviewLog).where(
                ReviewLog.claim_id == claim_id, ReviewLog.action == "edit"
            )
        ).scalars().all()
        if not edit_logs:
            issues.append(
                "claim.status='edited' but no review_log action='edit' row"
            )
        else:
            has_previous = any(
                (log.payload or {}).get("previous_text") for log in edit_logs
            )
            if not has_previous:
                issues.append(
                    "edit audit row(s) exist but no previous_text recoverable"
                )

    # --- 8: supersede chain valid + cycle-free -------------------------------
    if claim.status == "superseded":
        if claim.superseded_by is None:
            # M4 CHECK should make this impossible at the DB layer; we
            # still report it because audits must not assume invariants.
            issues.append(
                "claim.status='superseded' but superseded_by is NULL"
            )
        else:
            seen: set[uuid.UUID] = {claim_id}
            cursor = claim.superseded_by
            cycle = False
            while cursor is not None:
                if cursor in seen:
                    issues.append(f"supersede chain cycle through claim {cursor}")
                    cycle = True
                    break
                seen.add(cursor)
                successor = db.get(Claim, cursor)
                if successor is None:
                    issues.append(f"superseded_by {cursor} does not resolve")
                    break
                cursor = successor.superseded_by
            if not cycle and cursor is None:
                # Chain terminated at a live leaf — that's correct.
                pass

    # --- 9: reviewed claims have at least one audit row ----------------------
    if claim.status in _REVIEWED_STATUSES:
        any_log = db.execute(
            select(ReviewLog.id).where(ReviewLog.claim_id == claim_id).limit(1)
        ).scalar_one_or_none()
        if any_log is None:
            issues.append(
                f"claim.status='{claim.status}' but no review_log row exists"
            )

    return ProvenanceResult(claim_id=claim_id, ok=not issues, issues=issues)
