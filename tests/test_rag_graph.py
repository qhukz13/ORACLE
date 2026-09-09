"""The knowledge graph: edges, the frozen layout, and incremental placement.

The failures this file is aimed at are the ones that leave a *working-looking* map. A layout that
reshuffles when a file is indexed still renders; a semantic edge drawn on top of an explicit one
still renders; a config file placed by its links alone still renders. Each of those is a picture
that quietly means something other than what the reader thinks, which is the specific way this
view can fail while passing every smoke test.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from oracle.rag.chunking import Chunk
from oracle.rag.collections import ContentKind, Document
from oracle.rag.graph import (
    _seed_positions,
    build,
    layout,
    place_at_neighbour_centroid,
    place_new_documents,
    relayout,
    resolve_wikilinks,
    semantic_edges,
)
from oracle.rag.store import KnowledgeStore

DIM = 4


def document(
    rel: str,
    *,
    project: str = "Asterim",
    collection: str = "projects",
    kind: ContentKind = ContentKind.MARKDOWN,
) -> Document:
    return Document(
        collection=collection,
        project=project,
        path=rel,
        abs_path=Path("C:/Projects") / rel,
        kind=kind,
        size=100,
        mtime_ns=1,
    )


def put(
    store: KnowledgeStore,
    doc: Document,
    texts: list[str],
    vecs: list[list[float]] | None = None,
    links: tuple[str, ...] = (),
) -> None:
    cs = [
        Chunk(doc=doc, ordinal=i, anchor=f"sym{i}", text=t, links=links)
        for i, t in enumerate(texts)
    ]
    store.put(
        doc,
        cs,
        np.array(vecs, dtype=np.float32) if vecs else None,
        content_hash=hashlib.sha256("".join(texts).encode()).hexdigest(),
        provenance="local_owned",
        indexed_at="2026-09-09T00:00:00Z",
        idents=list(texts),
        token_counts=[len(t.split()) for t in texts],
    )


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(tmp_path / "knowledge.db", DIM)


# ---------------------------------------------------------------------------
# document vectors
# ---------------------------------------------------------------------------


def test_put_pools_a_document_vector_and_normalises_it(store: KnowledgeStore) -> None:
    put(store, document("a.md"), ["one", "two"], [[3.0, 0, 0, 0], [0, 4.0, 0, 0]])
    ids, vecs = store.document_vectors()
    assert ids == ["projects/a.md"]
    # The mean is (1.5, 2, 0, 0); normalised it must be a unit vector, so that a dot product
    # against another document vector is a cosine and not a length contest.
    assert np.isclose(np.linalg.norm(vecs[0]), 1.0)


def test_a_document_without_vectors_gets_no_document_vector(store: KnowledgeStore) -> None:
    """Config is unembeddable by policy, and a zero vector is not a neutral stand-in.

    A zero vector is equidistant from everything: it would sit in the middle of the map claiming
    kinship with the whole corpus.
    """
    put(store, document("tsconfig.json", kind=ContentKind.CONFIG), ["{}"], None)
    assert store.document_vectors() == ([], pytest.approx(np.zeros((0, DIM))))


def test_deleting_a_document_takes_its_vector_and_position_with_it(store: KnowledgeStore) -> None:
    put(store, document("a.md"), ["one"], [[1.0, 0, 0, 0]])
    store.save_positions({"projects/a.md": (0.5, 0.5)}, placed="layout")
    assert store.document_vectors()[0] == ["projects/a.md"]

    store.delete_document("projects", "a.md")
    assert store.document_vectors()[0] == []
    assert store.positions() == {}


def test_backfill_fills_only_what_is_missing(store: KnowledgeStore) -> None:
    put(store, document("a.md"), ["one"], [[1.0, 0, 0, 0]])
    store.db.execute("DELETE FROM document_vectors")
    store.db.commit()
    put(store, document("b.md"), ["two"], [[0, 1.0, 0, 0]])

    assert store.backfill_document_vectors() == 1
    assert store.document_vectors()[0] == ["projects/a.md", "projects/b.md"]
    assert store.backfill_document_vectors() == 0


# ---------------------------------------------------------------------------
# edges
# ---------------------------------------------------------------------------


def test_wikilinks_resolve_on_basename_not_path(store: KnowledgeStore) -> None:
    """`[[Backpropagation]]`, not `[[08 - Neural Networks/Backpropagation.md]]`.

    A resolver that joined on `rel_path` produces an empty graph on the real corpus, and an empty
    graph reads as a broken layout rather than as a broken join.
    """
    put(store, document("notes/deep/Backpropagation.md"), ["x"], [[1.0, 0, 0, 0]])
    put(store, document("notes/Index.md"), ["y"], [[0, 1.0, 0, 0]], links=("Backpropagation",))

    edges, stats = resolve_wikilinks(store.graph_documents(), store.wikilinks())
    assert edges == [("projects/notes/Index.md", "projects/notes/deep/Backpropagation.md")]
    assert stats["basename"] == 1


def test_an_ambiguous_wikilink_produces_no_edge(store: KnowledgeStore) -> None:
    """Fails closed. A wrong edge invents a relationship the reader then reasons from."""
    put(store, document("one/Note.md", collection="a"), ["x"], [[1.0, 0, 0, 0]])
    put(store, document("two/Note.md", collection="a"), ["y"], [[0, 1.0, 0, 0]])
    put(store, document("Index.md", collection="a"), ["z"], [[0, 0, 1.0, 0]], links=("Note",))

    edges, stats = resolve_wikilinks(store.graph_documents(), store.wikilinks())
    assert edges == []
    assert stats["ambiguous"] == 1


def test_a_dangling_wikilink_is_counted_not_drawn(store: KnowledgeStore) -> None:
    put(store, document("Index.md"), ["z"], [[1.0, 0, 0, 0]], links=("Nonexistent",))
    edges, stats = resolve_wikilinks(store.graph_documents(), store.wikilinks())
    assert edges == []
    assert stats["dangling"] == 1


def test_semantic_edges_are_undirected_and_deduplicated() -> None:
    ids = ["a", "b"]
    vecs = np.array([[1.0, 0.0], [0.99, 0.14]], dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    assert semantic_edges(ids, vecs, k=2, threshold=0.5) == [("a", "b")]


def test_the_threshold_actually_excludes(store: KnowledgeStore) -> None:
    ids = ["a", "b"]
    vecs = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    assert semantic_edges(ids, vecs, k=2, threshold=0.85) == []


def test_an_explicit_edge_suppresses_the_semantic_one_for_the_same_pair(
    store: KnowledgeStore,
) -> None:
    """One relationship, drawn once, as the stronger of the two.

    Drawing both stacks a dashed suggestion on a solid fact and doubles the visual weight of
    exactly the pairs that agree most.
    """
    put(store, document("A.md"), ["x"], [[1.0, 0.0, 0, 0]], links=("B",))
    put(store, document("B.md"), ["y"], [[1.0, 0.001, 0, 0]])

    graph = build(store, k=2, threshold=0.5)
    assert len(graph.explicit_edges) == 1
    assert graph.semantic_edges == []


# ---------------------------------------------------------------------------
# the layout, and the property the whole design rests on
# ---------------------------------------------------------------------------


def test_a_documents_starting_point_does_not_depend_on_the_corpus_around_it() -> None:
    """The load-bearing property of ADR-0013, and the one that silently broke in the spike.

    Array-order seeding makes every position a function of how many documents exist and in what
    order they arrived, so indexing one file moves the whole map. It still renders. It still looks
    like a layout. It just cannot be learned, which was the entire reason for freezing positions.
    """
    small = _seed_positions(["b.md", "d.md"])
    grown = _seed_positions(["a.md", "b.md", "c.md", "d.md", "e.md"])

    assert np.array_equal(small[0], grown[1])  # b.md, at index 0 then index 1
    assert np.array_equal(small[1], grown[3])  # d.md, at index 1 then index 3


def test_the_layout_is_deterministic() -> None:
    nodes = [f"doc{i}.md" for i in range(12)]
    edges = [(nodes[i], nodes[(i + 1) % 12]) for i in range(12)]
    first, _ = layout(nodes, edges, iterations=20)
    second, _ = layout(nodes, edges, iterations=20)
    assert np.array_equal(first, second)


def test_the_layout_separates_two_disconnected_clusters() -> None:
    """The map has to show shape, or it is decoration with coordinates."""
    left = [f"l{i}" for i in range(6)]
    right = [f"r{i}" for i in range(6)]
    edges = [(a, b) for a in left for b in left if a < b]
    edges += [(a, b) for a in right for b in right if a < b]

    pos, _ = layout(left + right, edges, iterations=200)
    left_centre = pos[:6].mean(axis=0)
    right_centre = pos[6:].mean(axis=0)
    spread = max(np.linalg.norm(pos[:6] - left_centre, axis=1).max(), 1e-9)
    assert float(np.linalg.norm(left_centre - right_centre)) > spread


def test_layout_on_an_empty_corpus_does_not_explode() -> None:
    pos, seconds = layout([], [])
    assert pos.shape == (0, 2)
    assert seconds >= 0


# ---------------------------------------------------------------------------
# incremental placement
# ---------------------------------------------------------------------------


def test_placing_a_new_document_moves_nothing_else(store: KnowledgeStore) -> None:
    """ADR-0023's promise, and the only reason incremental placement is allowed to exist."""
    put(store, document("a.md"), ["x"], [[1.0, 0, 0, 0]])
    put(store, document("b.md"), ["y"], [[0, 1.0, 0, 0]])
    relayout(store, iterations=20)
    before = {k: (v[0], v[1]) for k, v in store.positions().items()}

    put(store, document("c.md"), ["z"], [[0.9, 0.1, 0, 0]])
    assert place_new_documents(store) == 1

    after = store.positions()
    for doc_id, (x, y) in before.items():
        assert (after[doc_id][0], after[doc_id][1]) == (x, y)
    assert after["projects/c.md"][2] == "incremental"


def test_a_newcomer_lands_among_its_neighbours_not_at_random(store: KnowledgeStore) -> None:
    placed = {"near": (10.0, 10.0), "also-near": (10.0, 12.0), "far": (-40.0, -40.0)}
    assert place_at_neighbour_centroid(placed, ["near", "also-near"]) == (10.0, 11.0)


def test_a_newcomer_with_no_placed_neighbours_goes_to_the_origin() -> None:
    """Rather than to a random point, which would invent a neighbourhood it does not have."""
    assert place_at_neighbour_centroid({"a": (5.0, 5.0)}, []) == (0.0, 0.0)


def test_relayout_is_stable_across_runs(store: KnowledgeStore) -> None:
    put(store, document("a.md"), ["x"], [[1.0, 0, 0, 0]])
    put(store, document("b.md"), ["y"], [[0, 1.0, 0, 0]])
    relayout(store, iterations=30)
    first = store.positions()
    relayout(store, iterations=30)
    assert store.positions() == first


# ---------------------------------------------------------------------------
# what the view is handed
# ---------------------------------------------------------------------------


def test_unembeddable_is_a_third_state_not_an_orphan(store: KnowledgeStore) -> None:
    """Counting config as orphaned gives the "neglect" question a false-positive floor the size
    of the config population — a permanent pile of things that are not actually neglected."""
    put(store, document("tsconfig.json", kind=ContentKind.CONFIG), ["{}"], None)
    put(store, document("lonely.md"), ["x"], [[1.0, 0, 0, 0]])

    graph = build(store)
    states = {n.rel_path: n.state for n in graph.nodes}
    assert states["tsconfig.json"] == "unembeddable"
    assert states["lonely.md"] == "placed"
    assert graph.stats["orphans"] == 1  # only lonely.md


def test_a_document_that_failed_to_parse_says_so(store: KnowledgeStore) -> None:
    put(store, document("broken.md"), ["x"], [[1.0, 0, 0, 0]])
    store.db.execute("UPDATE documents SET parse_error = 'boom'")
    store.db.commit()
    assert [n.state for n in build(store).nodes] == ["failed"]


def test_build_reports_unplaced_documents_rather_than_laying_them_out(
    store: KnowledgeStore,
) -> None:
    """Laying out on a read path is how the viewport starts simulating."""
    put(store, document("a.md"), ["x"], [[1.0, 0, 0, 0]])
    graph = build(store)
    assert graph.positions == {}
    assert graph.stats["unplaced"] == 1


def test_a_document_that_can_never_be_placed_is_not_counted_as_unplaced(
    store: KnowledgeStore,
) -> None:
    """Config has no vector by policy, so it has no position — ever.

    Counting it as unplaced puts a permanent "N documents have no settled position — Re-layout"
    banner in the view, offering a 30-second action that cannot change the number. Measured on the
    real corpus 2026-09-09: 106 of 1,561 documents, every one of them config. A prompt that can
    never be satisfied trains the reader to ignore the one that matters.
    """
    put(store, document("a.md"), ["x"], [[1.0, 0, 0, 0]])
    put(store, document("tsconfig.json", kind=ContentKind.CONFIG), ["{}"], None)
    relayout(store, iterations=10)

    graph = build(store)
    assert graph.stats["unplaced"] == 0
    assert {n.state for n in graph.nodes} == {"placed", "unembeddable"}


def test_relayout_reports_the_backfill_separately_from_the_layout(store: KnowledgeStore) -> None:
    """One total would make the steady-state cost look four times worse than it is: the backfill
    is a one-time 88 s on an index built before the table existed, the layout is 34 s every time."""
    put(store, document("a.md"), ["x"], [[1.0, 0, 0, 0]])
    store.db.execute("DELETE FROM document_vectors")
    store.db.commit()

    result = relayout(store, iterations=10)
    assert result["backfilled"] == 1
    assert "backfill_seconds" in result
    assert result["seconds"] >= 0


def test_build_on_an_empty_index_is_an_empty_graph(store: KnowledgeStore) -> None:
    graph = build(store)
    assert graph.nodes == []
    assert graph.stats["documents"] == 0
