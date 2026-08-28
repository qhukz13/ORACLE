"""Retrieval thins; it does not fail.

Every part of retrieval is optional at runtime and none of it is optional to get right:
the ONNX model may not be on disk, `knowledge.db` may not have been built, the index may
have been built by a different model or a different chunker. None of those is an error
the user caused, and none of them should cost a delegation.

The rule this suite pins is the one `_curate` already follows and nothing asserted:
**a missing retriever produces a thinner packet, never a failed one.** It is the kind of
property that is true until somebody adds a `raise` to a helper three layers down, which
is exactly why it is cheaper to test than to remember.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from oracle.rag.chunking import CHUNKER_VERSION
from oracle.rag.store import KnowledgeStore, SchemaMismatch

DIM = 8


def test_curation_survives_an_index_that_was_never_built(tmp_path: Path) -> None:
    """First run, and the commonest state of all: no `knowledge.db`. The packet keeps its
    orientation docs and its git state and simply carries no retrieval hits."""
    from oracle.api.app import _curate

    repo = tmp_path / "project"
    (repo / "docs").mkdir(parents=True)
    (repo / "README.md").write_text("# A project\n\nWhat it is.\n", encoding="utf-8")

    class Settings:
        data_dir = tmp_path / "nowhere"

    class State:
        settings = Settings()

    inputs = _curate(State(), repo, "project", "how does the thing work")
    assert inputs.tainted_sources == ()
    # Thinner, not empty: orientation docs come from the filesystem, not the index.
    assert any("README" in e.source for e in inputs.excerpts)


def test_curation_survives_a_retriever_that_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ONNX model missing, a corrupt index, a driver that will not load — all the
    same shape from here, and all of them a routing fact rather than a failure."""
    from oracle.api import app as app_module

    repo = tmp_path / "project"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# A project\n", encoding="utf-8")

    def explode(*_args: Any, **_kw: Any) -> Any:
        raise RuntimeError("onnxruntime is not installed")

    monkeypatch.setattr("oracle.rag.embedding.Embedder", explode)

    class Settings:
        data_dir = tmp_path / "data"

    class State:
        settings = Settings()

    inputs = app_module._curate(State(), repo, "project", "anything")
    assert inputs.excerpts  # the docs survived
    assert inputs.tainted_sources == ()


def test_an_index_cut_by_a_different_chunker_is_refused_not_used(tmp_path: Path) -> None:
    """A boundary change makes old rows and new rows mean different things, and nothing
    about it fails on its own — retrieval just gets quietly worse. `bind()` is the only
    place that can notice, so it raises the same way a model change does, and the health
    view already knows how to render that (RAG.md §9)."""
    path = tmp_path / "knowledge.db"
    store = KnowledgeStore(path, DIM)
    store.bind("bge-m3", DIM)
    store.db.execute(
        "UPDATE meta SET value = ? WHERE key = 'chunker_version'", (str(CHUNKER_VERSION + 1),)
    )
    store.db.commit()
    store.close()

    with pytest.raises(SchemaMismatch, match="delete it and reindex"):
        KnowledgeStore(path, DIM).bind("bge-m3", DIM)


def _store_with_one_chunk(path: Path) -> KnowledgeStore:
    """A real, bound, minimal index — enough for `retrieve()` to run end to end."""
    import hashlib

    import numpy as np

    from oracle.rag.chunking import Chunk
    from oracle.rag.collections import ContentKind, Document

    store = KnowledgeStore(path, DIM)
    store.bind("bge-m3", DIM)
    doc = Document(
        collection="projects",
        project="Asterim",
        path="a.md",
        abs_path=Path("C:/Projects/Asterim/a.md"),
        kind=ContentKind.MARKDOWN,
        size=10,
        mtime_ns=1,
    )
    store.put(
        doc,
        [Chunk(doc=doc, ordinal=0, anchor="A", text="token refresh")],
        np.ones((1, DIM), dtype=np.float32),
        content_hash=hashlib.sha256(b"a.md").hexdigest(),
        provenance="local_owned",
        indexed_at="2026-08-26T00:00:00Z",
        idents=["token refresh"],
        token_counts=[2],
    )
    return store


class _Embedder:
    """A stand-in for the ONNX embedder: constant vectors, no model on disk.

    The vectors are constant on purpose. These tests are about whether a probe *runs*,
    not about what it ranks — a fake that scored differently per query would make a
    degradation test quietly depend on retrieval quality."""

    def encode(self, texts: list[str], role: str, *, batch: int = 16) -> Any:
        import numpy as np

        return np.ones((len(texts), DIM), dtype=np.float32)


@pytest.mark.parametrize(
    ("translator", "why"),
    [
        (None, "translation switched off, or no provider at all"),
        (lambda _q: None, "the model refused, timed out, or did not translate"),
        (lambda _q: "", "an empty translation is not a translation"),
    ],
)
def test_retrieval_thins_to_the_native_probe_when_translation_is_unavailable(
    tmp_path: Path, translator: Any, why: str
) -> None:
    """Every way the second probe can fail to arrive, and the same outcome each time.

    This is the property that let translation ship at all (OQ-18): the degraded result
    is *today's* result, so the worst case of a mechanism that improves crosslingual
    recall is that it improves nothing. Parametrised rather than written three times
    because the failures differ only in where they start."""
    from oracle.rag.retrieval import retrieve

    store = _store_with_one_chunk(tmp_path / "knowledge.db")
    try:
        got = retrieve("как работает refresh токена", store, _Embedder(), translator=translator)
    finally:
        store.close()

    assert got.hits, f"retrieval must still answer when {why}"
    assert got.translated_count == 0
    assert "+translated" not in got.strategy


def test_a_translator_that_raises_is_a_bug_not_a_degradation(tmp_path: Path) -> None:
    """The one case retrieval does NOT absorb, stated so the boundary is deliberate.

    `translate_to_english` returns `None` on every failure it knows about, and the bridge
    in `api/app.py` catches the rest. If an exception still reaches here, the contract
    upstream is broken and hiding it would turn a bug into silently worse retrieval —
    which is the exact failure mode this whole question has been chasing."""
    from oracle.rag.retrieval import retrieve

    def explode(_q: str) -> str:
        raise RuntimeError("a translator must not do this")

    store = _store_with_one_chunk(tmp_path / "knowledge.db")
    try:
        with pytest.raises(RuntimeError, match="must not do this"):
            retrieve("как работает refresh токена", store, _Embedder(), translator=explode)
    finally:
        store.close()


def test_a_refused_index_still_leaves_a_usable_packet(tmp_path: Path) -> None:
    """The end of that path: `SchemaMismatch` is an exception, and `_curate` must treat
    it like every other retrieval outage rather than letting it reach the delegation."""
    from oracle.api.app import _curate

    repo = tmp_path / "project"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# A project\n", encoding="utf-8")

    data = tmp_path / "data"
    data.mkdir()
    store = KnowledgeStore(data / "knowledge.db", DIM)
    store.bind("some-other-model", DIM)
    store.close()

    class Settings:
        data_dir = data

    class State:
        settings = Settings()

    inputs = _curate(State(), repo, "project", "anything")
    assert inputs.excerpts
