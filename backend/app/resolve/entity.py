"""Step 6 entity linking — deterministic baseline.

§9 keeps the ontology loose: a few entity kinds, structure emerges from
real claims. `RuleEntityLinker` ships the V1 floor:

- `date`     — 4-digit years (regex; same shape as RuleExtractor)
- `place`    — capitalized tokens longer than 2 chars, deduped per text

That's it. A production linker (spaCy / underthesea) is a follow-up
under the `ml` optional extra; the protocol here is what it'll
satisfy.

§5 note about Vietnamese diacritics: regex uses Unicode-aware `\\w`,
not byte-level matching, so "Đà Nẵng" is recognized as a single token.
"""
import re
from typing import Protocol, runtime_checkable

from app.resolve.types import EntityRef

# Suggested ontology — soft, not enforced anywhere.
CANONICAL_ENTITY_KINDS: tuple[str, ...] = ("date", "person", "place", "org")

# 4-digit year between 1800 and 2099 — wide enough for memoir use,
# narrow enough to avoid catching, say, postal codes.
_YEAR_PATTERN = re.compile(r"\b(?:18|19|20)\d{2}\b")

# A "proper-noun-ish" token: starts uppercase (incl. Vietnamese), len >= 3.
# Naive but useful as a floor; the production linker replaces this.
_PROPER_PATTERN = re.compile(r"\b[A-ZĐÀÁẢÃẠÂẦẤẨẪẬĂẰẮẲẴẶÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ][\w'-]{2,}", re.UNICODE)


@runtime_checkable
class EntityLinker(Protocol):
    """Step 6 contract: claim text → soft entity references."""

    def link(self, text: str) -> list[EntityRef]: ...


class RuleEntityLinker:
    """Deterministic year + proper-noun extractor.

    Returns refs deduplicated by `(kind, canonical)` so the same year
    mentioned twice in one claim becomes one ref. Years are normalized
    to their digit form; proper nouns are kept verbatim.
    """

    def link(self, text: str) -> list[EntityRef]:
        if not text:
            return []
        refs: list[EntityRef] = []
        seen: set[tuple[str, str]] = set()

        for match in _YEAR_PATTERN.finditer(text):
            key = ("date", match.group(0))
            if key not in seen:
                seen.add(key)
                refs.append(EntityRef(kind="date", canonical=match.group(0)))

        for match in _PROPER_PATTERN.finditer(text):
            token = match.group(0)
            key = ("place", token)
            if key not in seen:
                seen.add(key)
                refs.append(EntityRef(kind="place", canonical=token))

        return refs
