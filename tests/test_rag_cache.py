"""The embedding cache: what it must never do, and the one number it exists for.

P5-T2 acceptance criterion: *"Re-chunking a file with unchanged text costs zero
embedding calls."* Zero, not "fewer" — so the tests count forward passes rather than
timing them, because a timing test cannot tell 43 minutes from 42.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from oracle.rag.cache import EmbeddingCache, cache_path, text_hash
from oracle.rag.indexer import embed_chunked

DIM = 4


class CountingEmbedder:
    """Records exactly which texts reached the model.

    The point of the cache is that some texts do not, and only a counter can prove it.
    """

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode(self, texts: list[str], role: str, *, batch: int = 16) -> np.ndarray:
        self.seen.extend(texts)
        if not texts:
            # Mirrors the real Embedder: an empty batch has a shape, not no shape.
            return np.zeros((0, DIM), dtype=np.float32)
        # Deterministic and text-dependent, so a misrouted vector is detectable.
        return np.array([[float(len(t)), 1.0, 0.0, 0.0] for t in texts], dtype=np.float32)

    @property
    def calls(self) -> int:
        return len(self.seen)


@pytest.fixture
def cache(tmp_path: Path) -> EmbeddingCache:
    return EmbeddingCache(tmp_path / "emb.db", "test-model", DIM)


class TestRoundTrip:
    def test_a_stored_vector_comes_back_unchanged(self, cache: EmbeddingCache) -> None:
        vec = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        cache.put_many([(text_hash("hello"), vec)])
        got = cache.get_many([text_hash("hello")])
        assert np.allclose(got[text_hash("hello")], vec)

    def test_an_unknown_hash_is_simply_absent(self, cache: EmbeddingCache) -> None:
        assert cache.get_many([text_hash("never seen")]) == {}

    def test_it_survives_reopening(self, tmp_path: Path) -> None:
        """The whole value is across runs; an in-memory cache would be pointless."""
        first = EmbeddingCache(tmp_path / "emb.db", "m", DIM)
        first.put_many([(text_hash("x"), np.ones(DIM, dtype=np.float32))])
        first.close()

        second = EmbeddingCache(tmp_path / "emb.db", "m", DIM)
        assert text_hash("x") in second.get_many([text_hash("x")])

    def test_lookups_batch_past_the_sqlite_parameter_limit(self, cache: EmbeddingCache) -> None:
        """SQLite allows 999 host parameters; a rebuild looks up ~9,400 hashes."""
        texts = [f"chunk {i}" for i in range(2500)]
        cache.put_many((text_hash(t), np.ones(DIM, dtype=np.float32)) for t in texts)
        found = cache.get_many([text_hash(t) for t in texts])
        assert len(found) == 2500


class TestKeying:
    def test_the_key_is_the_text_alone(self) -> None:
        """Not the path, not the ordinal — that omission is the entire design.

        `chunk_id` includes both, so a chunking change invalidates every id even where
        the text is byte-identical. This key does not move when boundaries do.
        """
        assert text_hash("same body") == text_hash("same body")
        assert text_hash("a") != text_hash("b")

    def test_a_cache_from_another_model_is_discarded_not_trusted(self, tmp_path: Path) -> None:
        """A mismatch resets, where the *index* refuses.

        The asymmetry is deliberate: a cache holds nothing that cannot be recomputed, so
        throwing it away costs time. A wrong vector in the index costs wrong answers.
        """
        first = EmbeddingCache(tmp_path / "emb.db", "model-a", DIM)
        first.put_many([(text_hash("x"), np.ones(DIM, dtype=np.float32))])
        first.close()

        second = EmbeddingCache(tmp_path / "emb.db", "model-b", DIM)
        assert second.size() == 0
        assert second.get_many([text_hash("x")]) == {}

    def test_a_different_dimension_also_resets(self, tmp_path: Path) -> None:
        EmbeddingCache(tmp_path / "emb.db", "m", 4).put_many(
            [(text_hash("x"), np.ones(4, dtype=np.float32))]
        )
        assert EmbeddingCache(tmp_path / "emb.db", "m", 8).size() == 0

    def test_each_model_gets_its_own_file(self, tmp_path: Path) -> None:
        a = cache_path(tmp_path, "multilingual-e5-base", 768)
        b = cache_path(tmp_path, "bge-m3", 1024)
        assert a != b
        assert "e5-base" in a.name and "768" in a.name
        assert "/" not in b.name and "\\" not in b.name


class TestEmbedChunked:
    def test_unchanged_text_costs_zero_forward_passes(self, cache: EmbeddingCache) -> None:
        """The acceptance criterion, stated as a number.

        This is what makes changing the chunker affordable: tree-sitter will move most
        boundaries while leaving most function bodies byte-identical.
        """
        texts = ["alpha body", "beta body", "gamma body"]
        embedder = CountingEmbedder()

        embed_chunked(embedder, texts, cache, batch=16)
        assert embedder.calls == 3

        embedder.seen.clear()
        _, cached, embedded = embed_chunked(embedder, texts, cache, batch=16)
        assert embedder.calls == 0
        assert (cached, embedded) == (3, 0)

    def test_only_the_new_text_reaches_the_model(self, cache: EmbeddingCache) -> None:
        """A file that gained one function re-embeds one function."""
        embedder = CountingEmbedder()
        embed_chunked(embedder, ["one", "two", "three"], cache, batch=16)

        embedder.seen.clear()
        _, cached, embedded = embed_chunked(
            embedder, ["one", "two", "three", "four"], cache, batch=16
        )
        assert embedder.seen == ["four"]
        assert (cached, embedded) == (3, 1)

    def test_vectors_keep_the_caller_order_when_partly_cached(self, cache: EmbeddingCache) -> None:
        """The failure this prevents attaches every embedding to the wrong chunk.

        A partly-cached batch is where it would happen: cached rows arrive by hash and
        fresh ones by position, and interleaving them wrongly still returns a correct
        *shape*. Every query would then work and every answer would be wrong.
        """
        embedder = CountingEmbedder()
        embed_chunked(embedder, ["bb", "dddd"], cache, batch=16)

        texts = ["a", "bb", "ccc", "dddd", "eeeee"]
        vectors, cached, embedded = embed_chunked(embedder, texts, cache, batch=16)
        assert (cached, embedded) == (2, 3)
        # CountingEmbedder encodes len(text) in the first component.
        assert [v[0] for v in vectors] == [1.0, 2.0, 3.0, 4.0, 5.0]

    def test_the_cache_is_written_before_the_caller_can_fail(self, cache: EmbeddingCache) -> None:
        """A crash mid-index must not discard forward passes already paid for."""
        embed_chunked(CountingEmbedder(), ["x", "y"], cache, batch=16)
        assert cache.size() == 2

    def test_no_cache_still_works(self) -> None:
        """`cache=None` is a supported mode, not an error path."""
        embedder = CountingEmbedder()
        vectors, cached, embedded = embed_chunked(embedder, ["a", "b"], None, batch=16)
        assert vectors.shape == (2, DIM)
        assert (cached, embedded) == (0, 2)

    def test_an_empty_batch_does_not_reach_the_model(self, cache: EmbeddingCache) -> None:
        embedder = CountingEmbedder()
        _, cached, embedded = embed_chunked(embedder, [], cache, batch=16)
        assert embedder.calls == 0
        assert (cached, embedded) == (0, 0)
