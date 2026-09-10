"""The eval's frozen corpus must survive a round-trip byte-for-byte where it counts.

`corpus_fingerprint()` hashes the text of every embedded chunk, ORACLE indexes `C:/Projects`, and
ORACLE lives in `C:/Projects`. So a commit during a multi-hour embedding run moves the corpus under
it and the vector checkpoint becomes unusable — which is not a hypothetical: the 2026-09-10 run
reached 28% and its checkpoint was already dead, killed by a docs commit an hour in that took the
semantic chunk count from 19,212 to 19,191.

`--corpus-cache` freezes the walk so the run is reproducible rather than merely restartable. The
property that buys anything is narrow and is what this file pins: **a corpus loaded from the cache
must fingerprint identically to the one that was saved.** Everything else about the cache can be
wrong in a way that shows up immediately; that one can be wrong in a way that shows up as a
plausible number six hours later, which is this question's whole history.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load_eval() -> ModuleType:
    """Import `scripts/eval_embeddings.py`, which is a script rather than a package module."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "eval_embeddings.py"
    spec = importlib.util.spec_from_file_location("eval_embeddings", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_embeddings"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def evalmod() -> ModuleType:
    return _load_eval()


def _corpus(evalmod: ModuleType) -> tuple[list[object], list[object]]:
    """A small corpus shaped like the real one: production chunks over production documents,
    which is what `chunk_doc` actually returns despite this module's own `Chunk` annotation."""
    from oracle.rag.chunking import Chunk
    from oracle.rag.collections import ContentKind, Document

    docs = [
        Document(
            collection="projects",
            project="ORACLE",
            path="ORACLE/docs/UI.md",
            abs_path=Path("C:/Projects/ORACLE/docs/UI.md"),
            kind=ContentKind.MARKDOWN,
            size=42,
            mtime_ns=0,
        ),
        Document(
            collection="projects",
            project="ORACLE",
            path="ORACLE/config/policy.yaml",
            abs_path=Path("C:/Projects/ORACLE/config/policy.yaml"),
            kind=ContentKind.CONFIG,
            size=7,
            mtime_ns=0,
        ),
    ]
    chunks = [
        Chunk(doc=docs[0], ordinal=0, anchor="§3", text="the orbital view", links=("a",), tags=()),
        Chunk(doc=docs[0], ordinal=1, anchor="§8", text="the agent queue", links=(), tags=("ui",)),
        # Config is lexical-only, so this one must NOT come back semantic — the split between
        # the embedded set and the lexical-only set is exactly what the fingerprint indexes.
        Chunk(doc=docs[1], ordinal=0, anchor="(file)", text="scopes: [notes]", links=(), tags=()),
    ]
    # The walk list the stats line counts. Bodies are deliberately not cached.
    walk = [
        evalmod.Doc(
            collection=d.collection,
            project=d.project,
            path=d.path,
            abs_path=d.abs_path,
            kind=d.kind.value,
            text="not cached",
        )
        for d in docs
    ]
    return walk, chunks


def test_a_cached_corpus_fingerprints_identically(evalmod: ModuleType, tmp_path: Path) -> None:
    """The whole point. If this drifts, a resumed run scores one corpus with another's vectors."""
    walk, chunks = _corpus(evalmod)
    key = ("bge-m3", "model.onnx", True)

    sem = [i for i, c in enumerate(chunks) if c.semantic]
    before = evalmod.corpus_fingerprint(chunks, sem, key, 512)

    path = str(tmp_path / "corpus.json.gz")
    evalmod.save_corpus_cache(path, walk, chunks)
    loaded = evalmod.load_corpus_cache(path)
    assert loaded is not None
    _, restored = loaded

    sem_after = [i for i, c in enumerate(restored) if c.semantic]
    assert sem_after == sem, "the embedded set moved, so the vectors would line up with nothing"
    assert evalmod.corpus_fingerprint(restored, sem_after, key, 512) == before


def test_the_round_trip_keeps_what_a_citation_needs(evalmod: ModuleType, tmp_path: Path) -> None:
    """Fixtures match on `doc.path` and citations show the anchor. A cache that dropped either
    would score every fixture as unreachable, which reads as a retrieval collapse."""
    walk, chunks = _corpus(evalmod)
    path = str(tmp_path / "corpus.json.gz")
    evalmod.save_corpus_cache(path, walk, chunks)
    restored_walk, restored = evalmod.load_corpus_cache(path)  # type: ignore[misc]

    assert [c.doc.path for c in restored] == [c.doc.path for c in chunks]
    assert [c.anchor for c in restored] == [c.anchor for c in chunks]
    assert [c.ordinal for c in restored] == [c.ordinal for c in chunks]
    assert [c.links for c in restored] == [c.links for c in chunks]
    assert [c.tags for c in restored] == [c.tags for c in chunks]
    # Documents are shared, not copied per chunk: two chunks of one file must still be one file.
    assert restored[0].doc is restored[1].doc
    # The stats line's counts survive, which is all the walk list is kept for.
    assert [d.kind for d in restored_walk] == ["markdown", "config"]


def test_a_missing_cache_is_a_quiet_none_and_a_stale_one_is_a_loud_none(
    evalmod: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Absent means "first run, go and walk". Wrong-shaped means "somebody changed the format",
    and that must never be adapted to — this file's whole history is of quietly-wrong reuse."""
    missing = str(tmp_path / "nope.json.gz")
    assert evalmod.load_corpus_cache(missing) is None

    stale = tmp_path / "stale.json.gz"
    with gzip.open(stale, "wt", encoding="utf-8") as fh:
        json.dump({"version": evalmod.CORPUS_CACHE_VERSION + 1, "docs": [], "chunks": []}, fh)
    assert evalmod.load_corpus_cache(str(stale)) is None
    assert "not v" in capsys.readouterr().out


def test_the_cache_is_written_atomically(evalmod: ModuleType, tmp_path: Path) -> None:
    """A kill mid-write must not leave a truncated cache that loads as a smaller corpus —
    the same rule `save_vectors` follows, and for the same reason."""
    walk, chunks = _corpus(evalmod)
    path = tmp_path / "corpus.json.gz"
    evalmod.save_corpus_cache(str(path), walk, chunks)
    assert path.exists()
    assert not (tmp_path / "corpus.json.gz.tmp").exists()
