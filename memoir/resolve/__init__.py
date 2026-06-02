"""Step 5-6: Embedding, dedup candidates, entity linking.

BGE-m3 / E5 đa ngữ qua sentence-transformers; lưu pgvector.
spaCy / underthesea cho NLP tiếng Việt.
Dedup & merge: high-precision, NGƯỜI xác nhận. Không tự quyết.

The production path uses BGEEmbedder + spaCy. CI and developer setups
without the `ml` extras stay green via DeterministicEmbedder +
RuleEntityLinker — same protocols, deterministic outputs.
"""
from memoir.resolve.dedup import DEFAULT_LIMIT, DEFAULT_THRESHOLD, find_merge_candidates
from memoir.resolve.embedder import BGEEmbedder, DeterministicEmbedder, Embedder
from memoir.resolve.entity import (
    CANONICAL_ENTITY_KINDS,
    EntityLinker,
    RuleEntityLinker,
)
from memoir.resolve.types import EntityRef, MergeCandidate

__all__ = [
    "BGEEmbedder",
    "CANONICAL_ENTITY_KINDS",
    "DEFAULT_LIMIT",
    "DEFAULT_THRESHOLD",
    "DeterministicEmbedder",
    "Embedder",
    "EntityLinker",
    "EntityRef",
    "MergeCandidate",
    "RuleEntityLinker",
    "find_merge_candidates",
]
