"""Unit tests for DeterministicEmbedder. No DB, no ML deps."""
import math

import numpy as np
import pytest

from memoir.resolve import DeterministicEmbedder
from memoir.store import EMBEDDING_DIM


def _cosine(a: list[float], b: list[float]) -> float:
    av, bv = np.asarray(a), np.asarray(b)
    return float(np.dot(av, bv) / (np.linalg.norm(av) * np.linalg.norm(bv)))


def test_embedding_has_correct_dim() -> None:
    vec = DeterministicEmbedder().embed("Năm 1962 ở Detroit.")
    assert len(vec) == EMBEDDING_DIM


def test_embedding_is_l2_normalized() -> None:
    vec = DeterministicEmbedder().embed("hello")
    norm = math.sqrt(sum(x * x for x in vec))
    assert math.isclose(norm, 1.0, abs_tol=1e-6)


def test_identical_text_gives_identical_vector() -> None:
    embedder = DeterministicEmbedder()
    a = embedder.embed("Tôi sinh năm 1962.")
    b = embedder.embed("Tôi sinh năm 1962.")
    assert a == b
    assert _cosine(a, b) == pytest.approx(1.0)


def test_different_text_gives_low_similarity() -> None:
    """High-dim random vectors are nearly orthogonal — different strings
    should sit far below any merge threshold.
    """
    embedder = DeterministicEmbedder()
    a = embedder.embed("Tôi sinh năm 1962 ở Detroit.")
    b = embedder.embed("Bố tôi là kỹ sư đường sắt.")
    similarity = _cosine(a, b)
    assert similarity < 0.2, f"unrelated texts shouldn't merge; got {similarity}"


def test_embedder_satisfies_protocol_structurally() -> None:
    from memoir.resolve import Embedder

    assert isinstance(DeterministicEmbedder(), Embedder)
