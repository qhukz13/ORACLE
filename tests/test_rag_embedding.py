"""Embedding invariants — the ones whose violation is silent.

Nothing in this module fails loudly when it is wrong. An asymmetric model fed the wrong
prefix returns vectors of the right shape and the right norm that simply retrieve worse;
truncating before normalising returns unit-ish vectors whose similarities are all subtly
off. So the properties are asserted rather than assumed.

Tests that need the real ONNX weights skip when the model is not on disk, because the
suite must stay hermetic (docs/TESTING.md) — no test may require a 4 GB download to
pass. On this machine they do run, and they are where the cross-language property
OQ-02 established is actually held to.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import numpy as np
import pytest

from oracle.rag.embedding import E5_BASE, PASSAGE, QUERY, Embedder, ModelSpec, normalise


class TestModelSpec:
    def test_e5_prefixes_are_asymmetric_and_present(self) -> None:
        """RAG.md §4 requires a test for exactly this.

        E5 is trained with `query:` / `passage:`, and using one prefix for both — or
        neither — costs roughly half the model's quality while raising nothing. It is
        the classic bug in this subsystem, so it gets the bluntest possible assertion.
        """
        assert E5_BASE.query_prefix == "query: "
        assert E5_BASE.passage_prefix == "passage: "
        assert E5_BASE.query_prefix != E5_BASE.passage_prefix

    def test_prefix_is_selected_by_role(self) -> None:
        assert E5_BASE.prefix(QUERY) == E5_BASE.query_prefix
        assert E5_BASE.prefix(PASSAGE) == E5_BASE.passage_prefix

    def test_truncation_changes_the_reported_dimension(self) -> None:
        """`out_dim`, not `dim`, is what the vector column has to be built for."""
        truncated = ModelSpec(name="t", path=Path("."), dim=768, pooling="mean", truncate_to=384)
        assert truncated.dim == 768
        assert truncated.out_dim == 384
        assert E5_BASE.out_dim == 768


class TestNormalisation:
    """`normalise` is pure and worth testing without loading 1.1 GB of weights."""

    def test_output_is_unit_norm(self) -> None:
        spec = ModelSpec(name="t", path=Path("."), dim=4, pooling="mean")
        out = normalise(spec, np.array([[3.0, 4.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0]]))
        assert np.allclose(np.linalg.norm(out, axis=1), 1.0)

    def test_truncation_happens_before_normalisation(self) -> None:
        """Order matters, and getting it backwards is invisible.

        Normalise-then-truncate leaves each kept sub-vector with a norm below one that
        varies per row, so a dot product stops being a cosine and every score is quietly
        wrong. Asserting the *result* is unit-norm is what catches it.
        """
        spec = ModelSpec(name="t", path=Path("."), dim=4, pooling="mean", truncate_to=2)
        out = normalise(spec, np.array([[3.0, 4.0, 100.0, 100.0]]))
        assert out.shape == (1, 2)
        assert np.isclose(np.linalg.norm(out[0]), 1.0)
        assert np.allclose(out[0], [0.6, 0.8])

    def test_a_zero_vector_does_not_produce_nan(self) -> None:
        """An empty chunk should never poison a whole batch's similarity scores."""
        spec = ModelSpec(name="t", path=Path("."), dim=3, pooling="mean")
        out = normalise(spec, np.zeros((1, 3)))
        assert not np.isnan(out).any()


@cache
def _load() -> Embedder:
    """Loaded once per session, not once per test.

    A plain cached function rather than a module-scoped fixture: `asyncio_mode = "auto"`
    puts pytest-asyncio in the path of every fixture, and a non-async fixture with a
    scope wider than `function` trips its finaliser bookkeeping. A `@cache` sidesteps
    that entirely, and 1.1 GB of weights should be read from disk once regardless.
    """
    return Embedder(E5_BASE, threads=8)


@pytest.fixture
def embedder() -> Embedder:
    if not (E5_BASE.path / "model.onnx").exists():
        pytest.skip("e5-base ONNX not fetched; scripts/fetch_embedding_models.py")
    return _load()


class TestAgainstRealWeights:
    """Only runs where the ONNX model is actually present, so the suite stays hermetic."""

    def test_shape_and_norm(self, embedder: Embedder) -> None:
        vecs = embedder.encode(["hello world", "привет мир"], PASSAGE)
        assert vecs.shape == (2, E5_BASE.out_dim)
        assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-5)

    def test_order_is_preserved_across_length_sorted_batching(self, embedder: Embedder) -> None:
        """Batching sorts by length internally and must undo it.

        A permutation bug here attaches every embedding to the wrong chunk. The index
        still builds, every query still returns results, and every result is wrong —
        which is why this is asserted with texts of deliberately unequal lengths.
        """
        texts = ["short", "a much longer passage " * 40, "medium length text here", "x"]
        together = embedder.encode(texts, PASSAGE, batch=2)
        for i, text in enumerate(texts):
            alone = embedder.encode([text], PASSAGE)
            assert np.allclose(together[i], alone[0], atol=1e-4), f"row {i} is misaligned"

    def test_cross_language_similarity_beats_an_unrelated_pair(self, embedder: Embedder) -> None:
        """The property OQ-02 exists to establish, in miniature.

        A Russian question must sit closer to the English passage that answers it than
        to an unrelated English passage. If this fails, no amount of fusion saves the
        Russian half of the fixture set.
        """
        query = embedder.encode(["как обновить токен доступа"], QUERY)[0]
        answer, unrelated = embedder.encode(
            [
                "TokenService signs an access token and refreshes it after fifteen minutes",
                "The marketing site uses a dark colour palette and a serif display font",
            ],
            PASSAGE,
        )
        assert float(query @ answer) > float(query @ unrelated)

    def test_the_role_changes_the_vector(self) -> None:
        """The prefix reaches the model, which is all a unit test can honestly assert.

        The first version of this test claimed the *correct* prefix scores higher on a
        hand-written query/passage pair. It does not: measured here, `query:`/`passage:`
        gave 0.877 against 0.888 for the wrong pairing on one example. The prefix effect
        is a distributional property of the corpus, not a guarantee about any single
        pair, and a test that asserts otherwise is a test that will fail for the right
        reason and be "fixed" by weakening it.

        The real measurement is corpus-scale and lives in the benchmark:
        `scripts/eval_embeddings.py --no-prefix`, recorded in
        `logs/development/2026-08-22-oq02-embeddings.md`.
        """
        embedder = _load()
        text = "how long is an access token valid"
        assert not np.allclose(
            embedder.encode([text], QUERY)[0], embedder.encode([text], PASSAGE)[0]
        )

    def test_an_unknown_role_is_refused(self) -> None:
        """Better a loud error than a silently unprefixed vector."""
        with pytest.raises(ValueError, match="role must be"):
            _load().encode(["x"], "document")
