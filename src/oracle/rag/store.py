"""`knowledge.db` — chunks, vectors and BM25 in one file and one transaction.

Schema per [DATABASE.md §2](../../../docs/DATABASE.md). Three decisions are worth stating
because each looks like an omission until you know why:

**No migration runner.** `oracle.db` has one; this file deliberately does not. The index
is disposable by definition (ADR-0006), so the answer to a schema change is to delete the
file and reindex, and a migration path would be code written to preserve something whose
whole design property is that it needs no preserving. `_SCHEMA_VERSION` and the embedding
model are recorded in `meta`, and a mismatch refuses to open rather than migrating.

**Synchronous sqlite3, not aiosqlite.** Loading a SQLite extension is a sync-only API,
and the two callers do not want an event loop anyway: indexing is a background batch job
that must not share one, and retrieval is a short read that a caller wraps in
`asyncio.to_thread`. `oracle.db` stays on aiosqlite — it is the file with concurrent
readers and a live event feed.

**Brute force, on purpose.** ~9.4k vectors at 768 dimensions is 29 MB; a full scan is
milliseconds and an ANN index is pure overhead at this scale (RAG.md §1). Metadata
filtering happens *inside* the KNN query via vec0 partition keys, which is what keeps
that true when a search is scoped to one project.
"""

from __future__ import annotations

import sqlite3
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from oracle.logsink import get_logger
from oracle.rag.chunking import Chunk
from oracle.rag.collections import Document

log = get_logger(__name__)

#: Bumped whenever the schema below changes. There is no migration; a mismatch means the
#: file is deleted and rebuilt, which is the contract for a disposable index.
_SCHEMA_VERSION = 1

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
)


class SchemaMismatch(RuntimeError):
    """The file on disk was built by different code, or a different embedding model."""


def _schema(dim: int) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id            TEXT PRIMARY KEY,          -- collection/path, stable across machines
  collection_id TEXT NOT NULL,
  project_id    TEXT,
  path          TEXT NOT NULL,             -- absolute, for opening the citation
  rel_path      TEXT NOT NULL,             -- corpus-relative, for displaying it
  kind          TEXT NOT NULL,
  mtime_ns      INTEGER NOT NULL,
  size          INTEGER NOT NULL,
  content_hash  TEXT NOT NULL,             -- gates re-embedding; mtime alone lies on Windows
  provenance    TEXT NOT NULL,             -- local_owned | local_foreign (SECURITY.md §6)
  indexed_at    TEXT NOT NULL,
  parse_error   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_rel ON documents(collection_id, rel_path);

CREATE TABLE IF NOT EXISTS chunks (
  id          TEXT PRIMARY KEY,            -- sha256(rel_path + ordinal + text)
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal     INTEGER NOT NULL,
  text        TEXT NOT NULL,
  anchor      TEXT,
  token_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunks_document ON chunks(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors USING vec0(
  chunk_id      TEXT PRIMARY KEY,
  collection_id TEXT partition key,
  project_id    TEXT,
  embedding     FLOAT[{dim}]
);

-- `ident` holds identifiers exploded into parts. unicode61 cannot split camelCase and
-- cannot be configured to (OQ-08), so `entitlement` would never find `entitlementGuard`
-- without it. An unqualified MATCH searches every column, so callers need not know.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, anchor, ident, rel_path UNINDEXED, chunk_id UNINDEXED,
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS links (
  from_document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  to_path TEXT NOT NULL,
  kind    TEXT NOT NULL,
  PRIMARY KEY (from_document_id, to_path, kind)
);
"""


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk, carrying everything a citation needs (RAG.md §7)."""

    chunk_id: str
    collection: str
    project: str
    rel_path: str
    abs_path: str
    anchor: str
    text: str
    score: float
    provenance: str
    indexed_at: str


def _pack(vec: np.ndarray) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec.astype(np.float32))


class KnowledgeStore:
    """Open, write and query `knowledge.db`. One instance per process."""

    def __init__(self, path: Path, dim: int) -> None:
        self.path = path
        self.dim = dim
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        for pragma in _PRAGMAS:
            self.db.execute(pragma)
        self._load_vec()
        self.db.executescript(_schema(dim))
        self.db.commit()

    def _load_vec(self) -> None:
        import sqlite_vec

        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)

    # ---------------------------------------------------------------- meta

    def bind(self, model: str, dim: int) -> None:
        """Record which model and which chunker built this index, or refuse if it was a
        different one.

        The vector dimension is fixed at build time, so an index built by one model and
        queried by another does not fail — it returns confident nonsense. This is the
        only place that can notice, so it raises rather than warns.

        `chunker_version` is here for the same reason and was added after the same kind of
        near-miss (2026-08-26): incremental indexing does not rebuild rows it already has,
        so moving a chunk boundary leaves half an index cut one way and half the other.
        A model change and a boundary change are both "the vectors mean something else
        now", and both should cost a reindex rather than a slow decline nobody can see.
        """
        from oracle.rag.chunking import CHUNKER_VERSION

        stored = {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM meta")}
        expected = {
            "schema_version": str(_SCHEMA_VERSION),
            "embedding_model": model,
            "embedding_dim": str(dim),
            "chunker_version": str(CHUNKER_VERSION),
        }
        mismatched = {
            key: (stored[key], want)
            for key, want in expected.items()
            if key in stored and stored[key] != want
        }
        if mismatched:
            raise SchemaMismatch(
                f"{self.path} was built with {mismatched}; delete it and reindex "
                "(the index is disposable — ADR-0006)"
            )
        self.db.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            list(expected.items()),
        )
        self.db.commit()

    def record_script_census(self) -> int:
        """Count chunks containing Cyrillic, and store it. Returns the count.

        The fusion gate needs to know how much of the corpus a query's script actually
        covers, and the answer costs a full scan (~1.3 s over 11k chunks) — far too much
        per query, and it only changes when the index does. So it is computed once, here,
        at the end of a build.
        """
        count = int(
            self.db.execute(
                # RUF001 flags Cyrillic as "ambiguous"; a Cyrillic character class is
                # precisely what this predicate is for, hence the suppression below.
                "SELECT COUNT(*) AS n FROM chunks WHERE text GLOB '*[а-яА-ЯёЁ]*'"  # noqa: RUF001
            ).fetchone()["n"]
        )
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES ('cyrillic_chunks', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(count),),
        )
        self.db.commit()
        return count

    def script_census(self) -> int | None:
        """Chunks containing Cyrillic, or None if this index predates the census.

        None means "unknown", and the caller falls back to its previous behaviour rather
        than guessing a number — an index built by an older version is not a broken one.
        """
        row = self.db.execute("SELECT value FROM meta WHERE key = 'cyrillic_chunks'").fetchone()
        return int(row["value"]) if row else None

    # ----------------------------------------------------------- incremental

    def known_hashes(self, collection: str) -> dict[str, str]:
        """`rel_path -> content_hash` for one collection, to gate re-embedding."""
        return {
            r["rel_path"]: r["content_hash"]
            for r in self.db.execute(
                "SELECT rel_path, content_hash FROM documents WHERE collection_id = ?",
                (collection,),
            )
        }

    def delete_document(self, collection: str, rel_path: str) -> None:
        row = self.db.execute(
            "SELECT id FROM documents WHERE collection_id = ? AND rel_path = ?",
            (collection, rel_path),
        ).fetchone()
        if row is None:
            return
        self._purge(row["id"])
        self.db.execute("DELETE FROM documents WHERE id = ?", (row["id"],))

    def _purge(self, document_id: str) -> None:
        """Remove a document's chunks from all three indexes.

        The FTS and vector tables are virtual and are **not** reached by the foreign-key
        cascade on `chunks`. Deleting only the row would leave orphaned vectors that
        still match queries and still cite a file that no longer says that — a stale hit
        is worse than a missing one, because it looks correct.
        """
        ids = [
            r["id"]
            for r in self.db.execute("SELECT id FROM chunks WHERE document_id = ?", (document_id,))
        ]
        if not ids:
            return
        # S608 below: `marks` is a run of `?` placeholders whose length is derived from
        # `len(ids)`. No value is interpolated into the SQL — the ids themselves are
        # bound. SQLite has no way to bind a variable-length IN list, so this is the
        # only construction available, and it is safe by inspection.
        marks = ",".join("?" * len(ids))
        self.db.execute(f"DELETE FROM chunk_vectors WHERE chunk_id IN ({marks})", ids)  # noqa: S608
        self.db.execute(f"DELETE FROM chunks_fts WHERE chunk_id IN ({marks})", ids)  # noqa: S608
        self.db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))

    def put(
        self,
        doc: Document,
        chunks: Sequence[Chunk],
        vectors: np.ndarray | None,
        *,
        content_hash: str,
        provenance: str,
        indexed_at: str,
        idents: Sequence[str],
        token_counts: Sequence[int],
    ) -> None:
        """Replace one document and everything derived from it, atomically.

        Replace rather than merge: a document whose chunk count shrank would otherwise
        keep the chunks that no longer exist, and they would keep being retrieved.
        """
        doc_id = f"{doc.collection}/{doc.path}"
        with self.db:  # one transaction — vectors, BM25 and metadata stay consistent
            self.delete_document(doc.collection, doc.path)
            self.db.execute(
                "INSERT INTO documents(id, collection_id, project_id, path, rel_path, kind,"
                " mtime_ns, size, content_hash, provenance, indexed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    doc_id,
                    doc.collection,
                    doc.project,
                    str(doc.abs_path),
                    doc.path,
                    doc.kind.value,
                    doc.mtime_ns,
                    doc.size,
                    content_hash,
                    provenance,
                    indexed_at,
                ),
            )
            self.db.executemany(
                "INSERT INTO chunks(id, document_id, ordinal, text, anchor, token_count)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (chunk_id(doc.path, c.ordinal, c.text), doc_id, c.ordinal, c.text, c.anchor, tc)
                    for c, tc in zip(chunks, token_counts, strict=True)
                ],
            )
            # The FTS rowid is pinned to `chunks.rowid` so a delete can be an indexed
            # lookup rather than a scan. Read back rather than assumed: SQLite assigns
            # rowids, and guessing them is how an unrelated chunk gets deleted.
            rowids = {
                r["id"]: r["rowid"]
                for r in self.db.execute(
                    "SELECT id, rowid FROM chunks WHERE document_id = ?", (doc_id,)
                )
            }
            self.db.executemany(
                "INSERT INTO chunks_fts(rowid, text, anchor, ident, rel_path, chunk_id)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (
                        rowids[chunk_id(doc.path, c.ordinal, c.text)],
                        c.text,
                        c.anchor,
                        ident,
                        doc.path,
                        chunk_id(doc.path, c.ordinal, c.text),
                    )
                    for c, ident in zip(chunks, idents, strict=True)
                ],
            )
            if vectors is not None and len(vectors):
                self.db.executemany(
                    "INSERT INTO chunk_vectors(chunk_id, collection_id, project_id, embedding)"
                    " VALUES (?,?,?,?)",
                    [
                        (
                            chunk_id(doc.path, c.ordinal, c.text),
                            doc.collection,
                            doc.project,
                            _pack(v),
                        )
                        for c, v in zip(chunks, vectors, strict=True)
                    ],
                )
            links = {link for c in chunks for link in c.links}
            if links:
                self.db.executemany(
                    "INSERT OR IGNORE INTO links(from_document_id, to_path, kind)"
                    " VALUES (?,?,'wikilink')",
                    [(doc_id, link) for link in links],
                )

    def prune(self, collection: str, keep: Iterable[str]) -> int:
        """Delete documents no longer present on disk. Returns how many went."""
        alive = set(keep)
        gone = [
            r["rel_path"]
            for r in self.db.execute(
                "SELECT rel_path FROM documents WHERE collection_id = ?", (collection,)
            )
            if r["rel_path"] not in alive
        ]
        with self.db:
            for rel in gone:
                self.delete_document(collection, rel)
        if gone:
            log.info("rag.pruned", collection=collection, count=len(gone))
        return len(gone)

    # -------------------------------------------------------------- retrieval

    def search_dense(
        self,
        vector: np.ndarray,
        k: int = 30,
        *,
        collection: str | None = None,
        project: str | None = None,
    ) -> list[Hit]:
        """KNN with the metadata filter applied *inside* the scan, not after it.

        Filtering afterwards would ask for k results, throw most away, and return three —
        the classic post-filter bug. vec0 partition keys push the predicate down.
        """
        where = ["embedding MATCH ?", "k = ?"]
        params: list[Any] = [_pack(vector), k]
        if collection:
            where.append("collection_id = ?")
            params.append(collection)
        if project:
            where.append("project_id = ?")
            params.append(project)
        # S608: every element of `where` is a literal written above; the filter values
        # are bound in `params`. A caller cannot reach the SQL text.
        sql = (
            "SELECT v.chunk_id, v.distance FROM chunk_vectors v"  # noqa: S608
            f" WHERE {' AND '.join(where)} ORDER BY v.distance"
        )
        rows = self.db.execute(sql, params).fetchall()
        # L2 on unit vectors: d^2 = 2 - 2cos, so cos = 1 - d^2/2. Reported as a
        # similarity because a citation showing "distance 0.6" means nothing to a reader.
        return self._hydrate([(r["chunk_id"], 1.0 - (r["distance"] ** 2) / 2.0) for r in rows])

    def search_lexical(
        self,
        query: str,
        k: int = 30,
        *,
        collection: str | None = None,
        project: str | None = None,
    ) -> list[Hit]:
        """BM25 over the same chunks. `query` must already be FTS5 syntax."""
        sql = [
            "SELECT f.chunk_id AS chunk_id, bm25(chunks_fts) AS score",
            "FROM chunks_fts f",
            "JOIN chunks c ON c.id = f.chunk_id",
            "JOIN documents d ON d.id = c.document_id",
            "WHERE chunks_fts MATCH ?",
        ]
        params: list[Any] = [query]
        if collection:
            sql.append("AND d.collection_id = ?")
            params.append(collection)
        if project:
            sql.append("AND d.project_id = ?")
            params.append(project)
        sql.append("ORDER BY score LIMIT ?")
        params.append(k)
        try:
            rows = self.db.execute(" ".join(sql), params).fetchall()
        except sqlite3.OperationalError as exc:
            # FTS5 raises on malformed query syntax, and a user's question is not FTS5
            # syntax. A lexical miss must degrade to "dense only", never to a failed turn.
            log.warning("rag.fts_query_rejected", query=query, error=str(exc))
            return []
        # bm25() returns a negative number, more negative being better. Flipped so every
        # score in this module means the same thing: larger is more relevant.
        return self._hydrate([(r["chunk_id"], -float(r["score"])) for r in rows])

    def _hydrate(self, scored: list[tuple[str, float]]) -> list[Hit]:
        if not scored:
            return []
        by_id = dict(scored)
        marks = ",".join("?" * len(by_id))
        # S608: `marks` is placeholders only; the ids themselves are bound.
        rows = self.db.execute(
            "SELECT c.id, c.text, c.anchor, d.collection_id, d.project_id, d.rel_path,"  # noqa: S608
            " d.path, d.provenance, d.indexed_at"
            f" FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.id IN ({marks})",
            list(by_id),
        ).fetchall()
        hits = [
            Hit(
                chunk_id=r["id"],
                collection=r["collection_id"],
                project=r["project_id"] or "",
                rel_path=r["rel_path"],
                abs_path=r["path"],
                anchor=r["anchor"] or "",
                text=r["text"],
                score=by_id[r["id"]],
                provenance=r["provenance"],
                indexed_at=r["indexed_at"],
            )
            for r in rows
        ]
        return sorted(hits, key=lambda h: -h.score)

    # ------------------------------------------------------------------ health

    def stats(self) -> dict[str, Any]:
        """What the index health view (RAG.md §9) renders."""
        per_collection = [
            dict(r)
            for r in self.db.execute(
                "SELECT collection_id, COUNT(*) AS documents, MAX(indexed_at) AS last_indexed,"
                " SUM(size) AS bytes FROM documents GROUP BY collection_id"
            )
        ]
        chunks = self.db.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        vectors = self.db.execute("SELECT COUNT(*) AS n FROM chunk_vectors").fetchone()["n"]
        failures = [
            dict(r)
            for r in self.db.execute(
                "SELECT rel_path, parse_error FROM documents WHERE parse_error IS NOT NULL"
            )
        ]
        return {
            "path": str(self.path),
            "file_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "collections": per_collection,
            "chunks": chunks,
            "vectors": vectors,
            "failures": failures,
        }

    def close(self) -> None:
        self.db.close()


def chunk_id(rel_path: str, ordinal: int, text: str) -> str:
    """`sha256(rel_path + ordinal + text)` — RAG.md §6.

    Keyed on the text, not only on the position, so an edit at the top of a file does not
    invalidate every chunk below it. That is the whole reason an incremental update can be
    seconds rather than a re-embed of the file.
    """
    import hashlib

    h = hashlib.sha256()
    h.update(rel_path.encode("utf-8"))
    h.update(str(ordinal).encode("ascii"))
    h.update(text.encode("utf-8"))
    return h.hexdigest()
