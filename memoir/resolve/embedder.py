"""Embedders for memoir.resolve.

Two shipped implementations:

- `DeterministicEmbedder` — hash-based fake. Identical text → identical
  vector. Used by tests and as a stub for `set_claim_embedding` in
  developer setups without the heavy ML deps.
- `BGEEmbedder` — the production path per §5 (BGE-m3 via
  sentence-transformers). Lazy-imports the lib so plain `pip install
  memoir` doesn't drag in torch.

Both produce L2-normalized 1024-dim vectors, so cosine similarity
(`1 - <=> distance`) is well-defined and bounded in `[-1, 1]`.
"""
import hashlib
from typing import Protocol, runtime_checkable

import numpy as np

from memoir.store.models import EMBEDDING_DIM


@runtime_checkable
class Embedder(Protocol):
    """Step 5 contract: text → fixed-dim, L2-normalized embedding vector.

    Implementations MUST return `len == EMBEDDING_DIM` (1024) so the
    pgvector column accepts the row. Vectors SHOULD be L2-normalized so
    `<=>` distance equals `1 - cosine_similarity`.
    """

    def embed(self, text: str) -> list[float]: ...


class DeterministicEmbedder:
    """Reproducible, content-only embedder. NO semantic meaning.

    `embed("X") == embed("X")` always; different texts give independent
    random-looking vectors (hash-seeded). Cosine sim of equal texts is
    ~1.0; of unrelated texts is ~0 (high-dim random vectors are nearly
    orthogonal).

    This is enough to exercise `find_merge_candidates` deterministically:
    two claims with identical text are guaranteed to land above any
    reasonable threshold; two claims with different text are guaranteed
    NOT to (modulo astronomical hash collisions).
    """

    def embed(self, text: str) -> list[float]:
        # SHA-256 → seed a numpy Generator → sample 1024 normal floats →
        # L2-normalize. Independent of run-to-run randomness.
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big", signed=False)
        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm == 0:
            # vanishingly unlikely; defensive
            vec[0] = 1.0
            norm = 1.0
        return (vec / norm).tolist()


class BGEEmbedder:
    """Production embedder per §5: BGE-m3 via sentence-transformers.

    sentence-transformers is an opt-in dependency
    (`uv sync --extra ml`); we lazy-import to keep the core install
    free of torch.
    """

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        self.model_name = model_name
        self._model = None  # lazy

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "BGEEmbedder requires the 'ml' extras: "
                    "`uv sync --extra ml`. For tests use DeterministicEmbedder."
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        model = self._get_model()
        vec = model.encode([text], normalize_embeddings=True)[0]
        return vec.tolist()
