"""An embedding cache, keyed by the text rather than by where the text lives.

The problem it solves is specific. `chunk_id` is `sha256(path + ordinal + text)`, so a
change to *chunking* changes every id even where the text is byte-identical — and a full
re-embed is 43 minutes. tree-sitter chunking is the next task, and it will move most
boundaries while leaving most function bodies untouched. Without this, every experiment
with chunk boundaries costs three quarters of an hour.

**It is a separate file from `knowledge.db`, and that is the whole point.** The index is
disposable by design (ADR-0006) — "delete it and rebuild" is a promise the project leans
on. But the expensive part of rebuilding is not the index, it is the forward passes, and
those depend only on `(model, text)`. Keeping them in their own file means:

* deleting `knowledge.db` costs a walk and a re-chunk, not an hour of CPU;
* `knowledge.db`'s schema does not change to accommodate this, so nothing already built
  needs rebuilding to gain it;
* the cache is *itself* disposable and independently deletable, with no correctness risk
  — a miss costs time, never accuracy.

**One file per model.** The vector for a text is a function of the model, and mixing two
models' vectors in one table means every read has to filter on it and every mistake is
silent. Deleting a model's cache is then deleting one file.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from oracle.logsink import get_logger

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS embeddings (
  text_hash TEXT PRIMARY KEY,
  vector    BLOB NOT NULL
);
"""

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
)

#: SQLite's default limit is 999 host parameters, and a rebuild looks up ~9.4k hashes.
_BATCH = 500


def text_hash(text: str) -> str:
    """`sha256` of the chunk text alone — no path, no ordinal.

    That omission *is* the design: two chunks with the same text share an embedding no
    matter which file they came from or where in it they sit.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CacheStats:
    hits: int = 0
    misses: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class EmbeddingCache:
    """`text -> vector` for one model. Misses cost time; they never cost correctness."""

    def __init__(self, path: Path, model: str, dim: int) -> None:
        self.path = path
        self.model = model
        self.dim = dim
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        for pragma in _PRAGMAS:
            self.db.execute(pragma)
        self.db.executescript(_SCHEMA)
        self._bind()

    def _bind(self) -> None:
        """Record the model, or discard a cache built by a different one.

        Unlike the index, a mismatch here **resets rather than raises**. A cache is a
        performance artefact with no information in it that cannot be recomputed, so the
        useful behaviour is to throw it away and carry on — where the *index* must refuse,
        because a wrong vector there is silently wrong answers.
        """
        stored = {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM meta")}
        want = {"model": self.model, "dim": str(self.dim)}
        if stored and stored != want:
            log.warning("rag.cache_reset", had=stored, want=want, path=str(self.path))
            self.db.execute("DELETE FROM embeddings")
            self.db.execute("DELETE FROM meta")
        self.db.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            list(want.items()),
        )
        self.db.commit()

    def get_many(self, hashes: Sequence[str]) -> dict[str, np.ndarray]:
        """Whatever of `hashes` is already known. Absent keys are simply absent."""
        found: dict[str, np.ndarray] = {}
        for start in range(0, len(hashes), _BATCH):
            window = hashes[start : start + _BATCH]
            marks = ",".join("?" * len(window))
            rows = self.db.execute(
                # S608: `marks` is placeholders only; the hashes themselves are bound.
                f"SELECT text_hash, vector FROM embeddings WHERE text_hash IN ({marks})",  # noqa: S608
                list(window),
            ).fetchall()
            for row in rows:
                found[row["text_hash"]] = np.frombuffer(row["vector"], dtype=np.float32)
        return found

    def put_many(self, items: Iterable[tuple[str, np.ndarray]]) -> int:
        """Store vectors. `INSERT OR IGNORE`, because a race is a duplicate, not an error."""
        rows = [
            (h, struct.pack(f"{self.dim}f", *v.astype(np.float32)))
            for h, v in items
            if len(v) == self.dim
        ]
        if not rows:
            return 0
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO embeddings(text_hash, vector) VALUES (?, ?)", rows
            )
        return len(rows)

    def size(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) AS n FROM embeddings").fetchone()["n"])

    def stats(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "model": self.model,
            "dim": self.dim,
            "entries": self.size(),
            "file_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }

    def close(self) -> None:
        self.db.close()


def warm_from_index(cache: EmbeddingCache, index_db: sqlite3.Connection) -> int:
    """Seed the cache from vectors an existing index already holds. Returns rows added.

    Without this, adding the cache would mean the *next* rebuild is fast and the hour
    already spent building the current index is thrown away — which is a strange thing to
    ask of anyone. The chunk text and its vector are both in `knowledge.db`; the cache key
    is a hash of that text. So the join exists, and it takes seconds.

    Safe to run repeatedly: `put_many` ignores duplicates, and a vector recovered here is
    the same forward pass it would have recomputed.
    """
    rows = index_db.execute(
        "SELECT c.text AS text, v.embedding AS embedding"
        " FROM chunks c JOIN chunk_vectors v ON v.chunk_id = c.id"
    ).fetchall()
    added = cache.put_many(
        (text_hash(r["text"]), np.frombuffer(r["embedding"], dtype=np.float32)) for r in rows
    )
    log.info("rag.cache_warmed", rows=len(rows), added=added, path=str(cache.path))
    return added


def cache_path(data_dir: Path, model: str, dim: int) -> Path:
    """One file per model, named so a human can see which is which and delete one."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in model)
    return data_dir / f"embeddings-{safe}-{dim}.db"
