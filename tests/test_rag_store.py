"""`knowledge.db`: schema, incremental replacement, and filtered retrieval.

The failures this file is aimed at are the ones that leave a working-looking index:
orphaned vectors that still match and still cite a file that no longer says that, a
post-filtered KNN that silently returns three results when asked for ten, and an index
built by one embedding model queried by another.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from oracle.rag.chunking import Chunk
from oracle.rag.collections import ContentKind, Document
from oracle.rag.store import KnowledgeStore, SchemaMismatch, chunk_id

DIM = 4


def document(rel: str, project: str = "Asterim", collection: str = "projects") -> Document:
    return Document(
        collection=collection,
        project=project,
        path=rel,
        abs_path=Path("C:/Projects") / rel,
        kind=ContentKind.CODE,
        size=100,
        mtime_ns=1,
    )


def chunks_for(doc: Document, texts: list[str], links: tuple[str, ...] = ()) -> list[Chunk]:
    return [
        Chunk(doc=doc, ordinal=i, anchor=f"sym{i}", text=t, links=links)
        for i, t in enumerate(texts)
    ]


def put(
    store: KnowledgeStore,
    doc: Document,
    texts: list[str],
    vecs: list[list[float]] | None = None,
    links: tuple[str, ...] = (),
) -> list[Chunk]:
    cs = chunks_for(doc, texts, links)
    vectors = np.array(vecs, dtype=np.float32) if vecs else None
    store.put(
        doc,
        cs,
        vectors,
        content_hash=hashlib.sha256("".join(texts).encode()).hexdigest(),
        provenance="local_owned",
        indexed_at="2026-08-22T00:00:00Z",
        idents=[t for t in texts],
        token_counts=[len(t.split()) for t in texts],
    )
    return cs


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    s = KnowledgeStore(tmp_path / "knowledge.db", DIM)
    s.bind("test-model", DIM)
    return s


class TestBinding:
    def test_a_different_model_refuses_to_open(self, tmp_path: Path) -> None:
        """An index built by one model and queried by another does not fail — it returns
        confident nonsense. This is the only place that can notice."""
        path = tmp_path / "knowledge.db"
        KnowledgeStore(path, DIM).bind("e5-base", DIM)
        with pytest.raises(SchemaMismatch, match="delete it and reindex"):
            KnowledgeStore(path, DIM).bind("bge-m3", DIM)

    def test_rebinding_the_same_model_is_fine(self, tmp_path: Path) -> None:
        path = tmp_path / "knowledge.db"
        KnowledgeStore(path, DIM).bind("e5-base", DIM)
        KnowledgeStore(path, DIM).bind("e5-base", DIM)

    def test_a_different_chunker_refuses_too(self, tmp_path: Path) -> None:
        """A boundary change is the same class of problem as a model change: the vectors
        mean something else now. Incremental indexing does not rebuild rows it already
        has, so without this an index ends up half cut one way and half the other — and
        nothing fails, retrieval just quietly gets worse (measured 2026-08-26)."""
        path = tmp_path / "knowledge.db"
        KnowledgeStore(path, DIM).bind("e5-base", DIM)
        store = KnowledgeStore(path, DIM)
        store.db.execute("UPDATE meta SET value = '1' WHERE key = 'chunker_version'")
        store.db.commit()
        store.close()
        with pytest.raises(SchemaMismatch, match="delete it and reindex"):
            KnowledgeStore(path, DIM).bind("e5-base", DIM)

    def test_the_chunker_version_is_recorded(self, tmp_path: Path) -> None:
        """A guard nobody writes is a guard nobody has: assert the row exists, so a
        future `bind()` that stops writing it fails here rather than in six months."""
        from oracle.rag.chunking import CHUNKER_VERSION

        path = tmp_path / "knowledge.db"
        store = KnowledgeStore(path, DIM)
        store.bind("e5-base", DIM)
        row = store.db.execute("SELECT value FROM meta WHERE key = 'chunker_version'").fetchone()
        assert row is not None and row["value"] == str(CHUNKER_VERSION)


class TestReplacement:
    def test_reindexing_a_shrunken_document_drops_the_old_chunks(
        self, store: KnowledgeStore
    ) -> None:
        """Replace, never merge.

        A document edited down from three chunks to one would otherwise keep the two
        that no longer exist, and they would keep being retrieved and keep citing text
        that is not in the file any more.
        """
        doc = document("a.ts")
        put(store, doc, ["alpha one", "beta two", "gamma three"], [[1, 0, 0, 0]] * 3)
        assert store.stats()["chunks"] == 3

        put(store, doc, ["alpha one"], [[1, 0, 0, 0]])
        assert store.stats()["chunks"] == 1
        assert store.stats()["vectors"] == 1

    def test_deleting_a_document_leaves_no_orphan_vectors(self, store: KnowledgeStore) -> None:
        """`chunk_vectors` and `chunks_fts` are virtual tables. The foreign-key cascade
        on `chunks` does not reach them, so deleting only the row would leave vectors
        that still match queries."""
        doc = document("a.ts")
        put(store, doc, ["alpha one", "beta two"], [[1, 0, 0, 0], [0, 1, 0, 0]])
        store.delete_document("projects", "a.ts")
        store.db.commit()

        stats = store.stats()
        assert stats["chunks"] == 0
        assert stats["vectors"] == 0
        assert store.search_lexical("alpha") == []
        assert store.search_dense(np.array([1, 0, 0, 0], dtype=np.float32)) == []

    def test_prune_removes_documents_no_longer_on_disk(self, store: KnowledgeStore) -> None:
        put(store, document("a.ts"), ["alpha one"], [[1, 0, 0, 0]])
        put(store, document("b.ts"), ["beta two"], [[0, 1, 0, 0]])
        assert store.prune("projects", keep=["a.ts"]) == 1
        assert [h.rel_path for h in store.search_lexical("alpha OR beta")] == ["a.ts"]

    def test_the_fts_rowid_is_pinned_to_the_chunk_rowid(self, store: KnowledgeStore) -> None:
        """Deleting from FTS goes by rowid, so the two tables must agree.

        `chunk_id` is an UNINDEXED FTS column, so deleting by it scans the whole index
        once per document. Deleting by rowid is a lookup — but only if the rowids match.
        If they ever drift, a delete removes *some other chunk's* FTS row: the deleted
        chunk stays searchable and an unrelated one silently stops being. Neither raises.
        """
        put(store, document("a.ts"), ["alpha one", "beta two"], [[1, 0, 0, 0]] * 2)
        pairs = store.db.execute(
            "SELECT c.rowid AS crow, f.rowid AS frow, c.id"
            " FROM chunks c JOIN chunks_fts f ON f.chunk_id = c.id"
        ).fetchall()
        assert len(pairs) == 2
        for row in pairs:
            assert row["crow"] == row["frow"]

    def test_replacing_a_document_leaves_the_others_searchable(self, store: KnowledgeStore) -> None:
        """The failure a drifting rowid would cause, asserted end to end."""
        put(store, document("a.ts"), ["alpha unique"], [[1, 0, 0, 0]])
        put(store, document("b.ts"), ["beta unique"], [[0, 1, 0, 0]])
        put(store, document("a.ts"), ["alpha rewritten"], [[1, 0, 0, 0]])

        assert [h.rel_path for h in store.search_lexical("beta")] == ["b.ts"]
        assert [h.rel_path for h in store.search_lexical("rewritten")] == ["a.ts"]
        assert store.search_lexical("unique AND alpha") == []

    def test_chunk_id_is_content_addressed(self) -> None:
        """Keyed on the text, not only the position — so an edit at the top of a file
        does not invalidate every chunk below it (RAG.md §6)."""
        assert chunk_id("a.ts", 0, "same") == chunk_id("a.ts", 0, "same")
        assert chunk_id("a.ts", 0, "same") != chunk_id("a.ts", 1, "same")
        assert chunk_id("a.ts", 0, "same") != chunk_id("b.ts", 0, "same")
        assert chunk_id("a.ts", 0, "same") != chunk_id("a.ts", 0, "different")


class TestRetrieval:
    def test_dense_returns_nearest_first_with_a_similarity_not_a_distance(
        self, store: KnowledgeStore
    ) -> None:
        doc = document("a.ts")
        put(store, doc, ["exact match", "orthogonal"], [[1, 0, 0, 0], [0, 1, 0, 0]])
        hits = store.search_dense(np.array([1, 0, 0, 0], dtype=np.float32))
        assert [h.text for h in hits] == ["exact match", "orthogonal"]
        # Larger is more relevant, everywhere in this module.
        assert hits[0].score > hits[1].score
        assert hits[0].score == pytest.approx(1.0, abs=1e-5)

    def test_metadata_filter_is_applied_inside_the_knn_scan(self, store: KnowledgeStore) -> None:
        """Filtering after the scan asks for k, throws most away, and returns a handful.

        Asserted by making every *unwanted* document a closer match than the wanted one:
        a post-filter would return nothing here, because the top-k would be entirely
        made of rows the filter then removes.
        """
        for i in range(6):
            put(store, document(f"noise{i}.ts", project="GameRecs"), ["noise"], [[1, 0, 0, 0]])
        put(store, document("wanted.ts", project="Asterim"), ["wanted"], [[0.2, 0.98, 0, 0]])

        hits = store.search_dense(np.array([1, 0, 0, 0], dtype=np.float32), k=3, project="Asterim")
        assert [h.text for h in hits] == ["wanted"]

    def test_lexical_finds_an_identifier_through_the_ident_column(
        self, store: KnowledgeStore
    ) -> None:
        """OQ-08: unicode61 cannot split camelCase, so `ident` carries the parts."""
        doc = document("guard.ts")
        cs = chunks_for(doc, ["export function entitlementGuard(key) {}"])
        store.put(
            doc,
            cs,
            None,
            content_hash="h",
            provenance="local_owned",
            indexed_at="2026-08-22T00:00:00Z",
            idents=["entitlement Guard"],
            token_counts=[6],
        )
        assert [h.rel_path for h in store.search_lexical("entitlement")] == ["guard.ts"]
        assert [h.rel_path for h in store.search_lexical("entitlementGuard")] == ["guard.ts"]

    def test_a_malformed_query_degrades_to_empty_rather_than_raising(
        self, store: KnowledgeStore
    ) -> None:
        """A user's question is not FTS5 syntax. A lexical miss must degrade to
        dense-only, never to a failed turn."""
        put(store, document("a.ts"), ["alpha one"], [[1, 0, 0, 0]])
        assert store.search_lexical('unbalanced "quote AND (') == []

    def test_every_hit_carries_a_real_citation(self, store: KnowledgeStore) -> None:
        """Acceptance criterion: every retrieved chunk carries a real, clickable source."""
        put(store, document("apps/server/a.ts"), ["alpha one"], [[1, 0, 0, 0]])
        hit = store.search_dense(np.array([1, 0, 0, 0], dtype=np.float32))[0]
        assert hit.rel_path == "apps/server/a.ts"
        assert hit.abs_path.endswith("a.ts")
        assert hit.project == "Asterim"
        assert hit.collection == "projects"
        assert hit.anchor == "sym0"
        assert hit.provenance == "local_owned"
        assert hit.indexed_at


class TestHealth:
    def test_stats_report_what_is_indexed(self, store: KnowledgeStore) -> None:
        put(store, document("a.ts"), ["alpha one", "beta two"], [[1, 0, 0, 0], [0, 1, 0, 0]])
        put(store, document("n.md", collection="notes", project="vault"), ["note"], [[0, 0, 1, 0]])
        stats = store.stats()
        assert stats["chunks"] == 3
        assert stats["vectors"] == 3
        assert {c["collection_id"] for c in stats["collections"]} == {"projects", "notes"}
        assert stats["file_bytes"] > 0

    def test_wikilinks_are_recorded_for_one_hop_expansion(self, store: KnowledgeStore) -> None:
        put(store, document("n.md"), ["see also"], [[1, 0, 0, 0]], links=("Embeddings",))
        rows = store.db.execute("SELECT to_path FROM links").fetchall()
        assert [r["to_path"] for r in rows] == ["Embeddings"]
