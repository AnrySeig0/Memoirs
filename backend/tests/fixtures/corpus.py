"""Deterministic, realistic corpus fixture for the M6 provenance audit.

The §1 acceptance test samples 100 random claims out of a corpus and
demands every one trace correctly back to its source. To make that
meaningful we need:

- Multiple subjects (so cross-subject leakage would show)
- Vietnamese diacritics (the §5 lưu ý about Unicode offsets)
- Multiple sessions per subject (so transcript reconstruction has to
  work across many session boundaries)
- Every review state actually exercised: pending, accepted, rejected,
  edited, flagged, superseded (M4), merged (M5).
- Enough total claims so a random sample of 100 has headroom.

The builder is **deterministic** via a `random.Random(seed)` so the
test runs identically each session. We deliberately don't use
`Date.now()` / module-level RNG anywhere — every shuffle and choice
goes through the seeded RNG.
"""
import random
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session as OrmSession

from app.extract import RuleExtractor
from app.ingest import Turn, ingest_text_transcript
from app.resolve import DeterministicEmbedder
from app.segment import segment_by_utterance
from app.store import (
    accept_claim,
    edit_claim,
    flag_claim,
    insert_claim_with_sources,
    merge_claim,
    reject_claim,
    set_claim_embedding,
    supersede_claim,
)


@dataclass(frozen=True)
class CorpusFixture:
    """All the IDs the audit test needs."""

    subject_ids: list[uuid.UUID]
    all_claim_ids: list[uuid.UUID]
    reviewed_claim_ids: list[uuid.UUID] = field(default_factory=list)


# A subject's session content: list of (year_in_sentence, vietnamese_diacritics)
# turn templates. We keep the templates small so we can reason about how
# many year-mentions exist per subject (RuleExtractor gates claim
# extraction on year detection).
_ENGLISH_TEMPLATES: list[tuple[str, str, str]] = [
    # (speaker, template_with_{year}, claim_text_template)
    ("subject", "I moved to Detroit in {year}.", "Subject moved to Detroit in {year}."),
    ("subject", "My father got that factory job in {year}.", "Subject's father started factory work in {year}."),
    ("subject", "We bought the house on Grand Boulevard in {year}.", "Subject bought a house on Grand Boulevard in {year}."),
    ("subject", "I met my wife in the spring of {year}.", "Subject met spouse in spring {year}."),
    ("subject", "Our first child was born in {year}.", "Subject's first child born in {year}."),
    ("subject", "My mother passed away in {year}.", "Subject's mother died in {year}."),
    ("subject", "I started teaching at the high school in {year}.", "Subject began teaching high school in {year}."),
    ("subject", "We took that long trip to California in {year}.", "Subject travelled to California in {year}."),
]

_VIETNAMESE_TEMPLATES: list[tuple[str, str, str]] = [
    ("subject", "Năm {year}, tôi chuyển đến Đà Nẵng.", "Chuyển đến Đà Nẵng năm {year}."),
    ("subject", "Bố tôi bắt đầu làm việc ở nhà máy năm {year}.", "Bố làm nhà máy năm {year}."),
    ("subject", "Chúng tôi mua nhà ở đường Nguyễn Du năm {year}.", "Mua nhà đường Nguyễn Du năm {year}."),
    ("subject", "Tôi gặp vợ tôi vào mùa xuân năm {year}.", "Gặp vợ mùa xuân năm {year}."),
    ("subject", "Con đầu lòng của chúng tôi sinh năm {year}.", "Con đầu sinh năm {year}."),
    ("subject", "Mẹ tôi mất năm {year}.", "Mẹ mất năm {year}."),
    ("subject", "Tôi bắt đầu dạy trường cấp ba năm {year}.", "Bắt đầu dạy năm {year}."),
    ("subject", "Chúng tôi đi du lịch dài ngày năm {year}.", "Đi du lịch năm {year}."),
]

_INTERVIEWER_PROMPTS = (
    ("interviewer", "Can you tell me more about that?"),
    ("interviewer", "Ông có nhớ năm đó không?"),
    ("interviewer", "What happened next?"),
)


def _build_session_turns(
    rng: random.Random,
    templates: list[tuple[str, str, str]],
    *,
    years: list[int],
    interviewer_lang: int,
) -> tuple[list[Turn], list[tuple[int, str]]]:
    """Pick 4-6 templates, instantiate with years, intersperse interviewer
    turns. Returns (turns_to_ingest, claim_seeds) where claim_seeds tells
    the caller which utterance indices map to which canonical claim text.
    """
    n_turns = rng.randint(4, 6)
    chosen = rng.sample(templates, k=min(n_turns, len(templates)))
    turns: list[Turn] = []
    claim_seeds: list[tuple[int, str]] = []
    for speaker, template, claim_template in chosen:
        if rng.random() < 0.35:
            interviewer = _INTERVIEWER_PROMPTS[interviewer_lang % len(_INTERVIEWER_PROMPTS)]
            turns.append(Turn(speaker=interviewer[0], text=interviewer[1]))
        year = rng.choice(years)
        text = template.format(year=year)
        turns.append(Turn(speaker=speaker, text=text))
        claim_seeds.append((len(turns) - 1, claim_template.format(year=year)))
    return turns, claim_seeds


def build_realistic_corpus(
    db: OrmSession,
    *,
    seed: int = 42,
    sessions_per_subject: int = 8,
) -> CorpusFixture:
    """Build a deterministic corpus large enough for §1's 100-sample audit.

    Roughly produces:
        - 2 subjects (English + Vietnamese)
        - sessions_per_subject sessions each
        - 4-6 turns per session (some interviewer), most with a year
          mention so RuleExtractor lights up
        - Plus 1-2 hand-seeded compound claims per session for variety
        - Reviews applied: accept / reject / edit / flag in rotation,
          plus supersede + merge chains where pairs are available
        - Total: enough reviewed claims to comfortably sample 100
    """
    rng = random.Random(seed)
    embedder = DeterministicEmbedder()
    extractor = RuleExtractor()

    subjects: list[tuple[uuid.UUID, list[tuple[str, str, str]]]] = [
        (uuid.uuid4(), _ENGLISH_TEMPLATES),
        (uuid.uuid4(), _VIETNAMESE_TEMPLATES),
    ]
    all_claim_ids: list[uuid.UUID] = []
    # Per-subject ledger so supersede/merge can pair up safely.
    per_subject_pending: dict[uuid.UUID, list[uuid.UUID]] = {s[0]: [] for s in subjects}

    years_pool = list(range(1955, 2005))

    for subject_idx, (subject_id, templates) in enumerate(subjects):
        for session_no in range(1, sessions_per_subject + 1):
            years = rng.sample(years_pool, k=rng.randint(3, 5))
            turns, claim_seeds = _build_session_turns(
                rng, templates, years=years, interviewer_lang=subject_idx
            )

            ingest = ingest_text_transcript(
                db,
                subject_id=subject_id,
                session_no=session_no,
                turns=turns,
                storage_uri=f"s3://memoir/corpus/subject{subject_idx}/session{session_no}.txt",
            )

            # 1) Manual compound claims — one per claim_seed, grounded on
            #    the corresponding utterance.
            for utt_index, claim_text in claim_seeds:
                utterance_id = ingest.utterance_ids[utt_index]
                claim = insert_claim_with_sources(
                    db,
                    subject_id=subject_id,
                    text=claim_text,
                    claim_type="event",
                    confidence=round(rng.uniform(0.55, 0.95), 3),
                    source_utterance_ids=[utterance_id],
                )
                all_claim_ids.append(claim.id)
                per_subject_pending[subject_id].append(claim.id)
                set_claim_embedding(
                    db, claim_id=claim.id, vector=embedder.embed(claim_text)
                )

            # 2) RuleExtractor — catches the year-mentioning utterances
            #    as fact-claims. Many of these duplicate the manual
            #    compound claims (which is fine — they're separately
            #    grounded into the same utterance and will exercise the
            #    merge flow downstream).
            for segment in segment_by_utterance(db, ingest.session_id):
                for extracted in extractor.extract(segment):
                    claim = insert_claim_with_sources(
                        db,
                        subject_id=subject_id,
                        text=extracted.text,
                        claim_type=extracted.claim_type,
                        confidence=extracted.confidence,
                        source_utterance_ids=extracted.source_utterance_ids,
                    )
                    all_claim_ids.append(claim.id)
                    per_subject_pending[subject_id].append(claim.id)
                    set_claim_embedding(
                        db, claim_id=claim.id, vector=embedder.embed(extracted.text)
                    )

            db.commit()

    # ---- review actions: rotate through every reviewable state ------------
    #
    # Proportions are chosen so each state has at least a handful of
    # representatives in the 100-claim sample. The exact split:
    #
    #   accept    ~ 45%
    #   reject    ~ 12%
    #   edit      ~ 12%
    #   flag      ~ 10%
    #   superseded ~ 10% (via supersede_claim — pairs)
    #   merged    ~ 8%   (via merge_claim — pairs)
    #   pending   ~  3%  (left alone deliberately so audit sees the state)
    #
    rng.shuffle(all_claim_ids)
    reviewed_ids: list[uuid.UUID] = []

    # We have to consume claims per subject for supersede/merge to find
    # legal pairs. Easiest: walk all_claim_ids; if it's the loser of a
    # supersede/merge, find a partner from the same subject in pending
    # state.

    actions_remaining = {
        "accept": int(0.45 * len(all_claim_ids)),
        "reject": int(0.12 * len(all_claim_ids)),
        "edit": int(0.12 * len(all_claim_ids)),
        "flag": int(0.10 * len(all_claim_ids)),
        "supersede": int(0.10 * len(all_claim_ids)),
        "merge": int(0.08 * len(all_claim_ids)),
    }
    # Anything not allocated stays pending.

    from app.store.models import Claim
    from sqlalchemy import select

    def _partner_for(
        subject_id: uuid.UUID, exclude: uuid.UUID, *, strict_one_to_one: bool
    ) -> uuid.UUID | None:
        """Find a live (status='pending') claim under `subject_id` that
        isn't `exclude` and hasn't been touched yet.

        When `strict_one_to_one=True` (M4 supersede flow), the partner
        also must NOT already be the successor of some other claim —
        otherwise we'd violate M4's 1:1 invariant. Merge has no such
        restriction (M5 explicitly relaxes it).
        """
        candidates = [
            cid
            for cid in per_subject_pending[subject_id]
            if cid != exclude
            and db.get(Claim, cid).status == "pending"
        ]
        if strict_one_to_one:
            taken = set(
                db.execute(
                    select(Claim.superseded_by).where(Claim.superseded_by.is_not(None))
                ).scalars()
            )
            candidates = [c for c in candidates if c not in taken]
        return rng.choice(candidates) if candidates else None

    for claim_id in all_claim_ids:
        if db.get(Claim, claim_id).status != "pending":
            # Already consumed as a partner in a supersede/merge.
            reviewed_ids.append(claim_id)
            continue

        # Pick the next action with remaining budget, in priority order.
        action_choices = [k for k, v in actions_remaining.items() if v > 0]
        if not action_choices:
            # Leftover claims stay pending.
            continue
        action = rng.choice(action_choices)
        actor = rng.choice(["alice", "bob", "carol"])

        if action == "accept":
            accept_claim(db, claim_id=claim_id, actor=actor)
            reviewed_ids.append(claim_id)
        elif action == "reject":
            reject_claim(
                db,
                claim_id=claim_id,
                actor=actor,
                reason=rng.choice(["duplicate", "unclear", "low confidence"]),
            )
            reviewed_ids.append(claim_id)
        elif action == "edit":
            existing = db.get(Claim, claim_id)
            edit_claim(
                db,
                claim_id=claim_id,
                actor=actor,
                new_text=existing.text + " [editor-rephrased]",
            )
            reviewed_ids.append(claim_id)
        elif action == "flag":
            flag_claim(
                db,
                claim_id=claim_id,
                actor=actor,
                reason=rng.choice(["possible contradiction", "verify date"]),
            )
            reviewed_ids.append(claim_id)
        elif action == "supersede":
            subj = db.get(Claim, claim_id).subject_id
            partner = _partner_for(subj, exclude=claim_id, strict_one_to_one=True)
            if partner is None:
                # No partner available — fall back to accept so the
                # claim doesn't stay pending forever.
                accept_claim(db, claim_id=claim_id, actor=actor)
                reviewed_ids.append(claim_id)
            else:
                supersede_claim(
                    db,
                    old_id=claim_id,
                    new_id=partner,
                    actor=actor,
                    note="self-correction",
                )
                # Partner stays pending — it's the leaf of the chain.
                # We'll review it on its own iteration.
                reviewed_ids.append(claim_id)
        elif action == "merge":
            subj = db.get(Claim, claim_id).subject_id
            partner = _partner_for(subj, exclude=claim_id, strict_one_to_one=False)
            if partner is None:
                accept_claim(db, claim_id=claim_id, actor=actor)
                reviewed_ids.append(claim_id)
            else:
                merge_claim(
                    db,
                    loser_id=claim_id,
                    winner_id=partner,
                    actor=actor,
                    similarity=round(rng.uniform(0.85, 1.0), 3),
                )
                reviewed_ids.append(claim_id)

        actions_remaining[action] -= 1

    db.commit()

    return CorpusFixture(
        subject_ids=[s[0] for s in subjects],
        all_claim_ids=all_claim_ids,
        reviewed_claim_ids=reviewed_ids,
    )
