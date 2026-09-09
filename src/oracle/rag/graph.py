"""The knowledge graph: edges, the offline layout, and incremental placement.

Design: [UI.md §11b](../../../docs/UI.md#11b-the-knowledge-graph--phase-11) · decision:
[ADR-0023](../../../docs/DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered)
· measurements: [OQ-22](../../../docs/OPEN_QUESTIONS.md#oq-22).

This module is the production port of `scripts/measure_graph.py`, which is why so few of its
constants are configurable: every one of them was chosen by a measurement rather than by taste, and
re-opening them in code would quietly re-open questions that were answered.

* **Semantic edges are on, at k=4 / thr=0.85.** Explicit wikilinks touch 11% of this corpus; without
  semantic edges 1,168 of 1,325 embeddable documents are orphans and the view is a scatter of dots.
* **The layout runs offline and the positions freeze.** The viewport never simulates.
* **Initial positions are seeded from a hash of each node's own id**, never its array index. That is
  not a detail: array-order seeding makes every position depend on how many documents exist and in
  what order they arrived, so indexing one file moves the whole map — which destroys the spatial
  memory ADR-0013 is built on, while still looking like a working layout.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from oracle.logsink import get_logger

if TYPE_CHECKING:  # pragma: no cover
    import sqlite3

    from oracle.rag.store import KnowledgeStore

log = get_logger(__name__)

#: OQ-22 measurement 3's recommendation. The useful band for the threshold is 0.80-0.90; 0.95 is
#: indistinguishable from having no semantic edges at all, and 0 is a hairball.
DEFAULT_K = 4
DEFAULT_THRESHOLD = 0.85
THRESHOLD_BAND = (0.80, 0.90)

#: Cold layout of 1,420 nodes / 3,103 edges took 27.8 s at this count, peak RSS 121 MB.
DEFAULT_ITERATIONS = 200
LAYOUT_SEED = 7

#: RAG.md §2 never embeds config — an embedding of a tsconfig matches everything and means nothing.
#: So a config document has no vector *by policy*, and counting it as an orphan would give UI.md
#: §11b's "neglect" question a false-positive floor the size of the config population. It is a third
#: state, distinct from both "connected" and "failed to index".
UNEMBEDDABLE_KINDS = frozenset({"config"})


@dataclass(frozen=True)
class GraphNode:
    id: str
    collection: str
    project: str | None
    rel_path: str
    kind: str
    indexed_at: str
    #: `unembeddable` is the third state: not connected, not broken, just not a thing we embed.
    state: str  # placed | unembeddable | failed
    degree: int = 0


@dataclass
class KnowledgeGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    #: Index pairs into `nodes`, undirected and de-duplicated.
    explicit_edges: list[tuple[int, int]] = field(default_factory=list)
    semantic_edges: list[tuple[int, int]] = field(default_factory=list)
    positions: dict[str, tuple[float, float, str]] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# edges
# ---------------------------------------------------------------------------


def resolve_wikilinks(
    documents: list[sqlite3.Row] | list[Any], links: list[sqlite3.Row] | list[Any]
) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """Resolve `links.to_path` (raw wikilink text) to document ids.

    **Basename, not path**, and that is the whole finding from the 2026-08-26 spike: a wikilink is
    written `[[Backpropagation]]`, not `[[08 - Neural Networks/Backpropagation.md]]`. Exact
    `rel_path` matching resolves *nothing* on this corpus, and a resolver that joined on it would
    produce an empty graph that reads as a bug in the layout rather than a bug in the join.

    An ambiguous basename **fails closed to no edge**. In a map whose stated purpose is "show me the
    shape of what I know", a wrong edge is worse than a missing one: it invents a relationship the
    reader then reasons from.
    """
    by_id = {d["id"]: d for d in documents}
    by_stem: defaultdict[str, list[Any]] = defaultdict(list)
    for d in documents:
        stem = d["rel_path"].rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        by_stem[stem].append(d)

    edges: list[tuple[str, str]] = []
    stats = {"exact": 0, "basename": 0, "ambiguous": 0, "dangling": 0, "self": 0}
    for link in links:
        src = link["from_document_id"]
        if src not in by_id:
            continue
        target = (link["to_path"] or "").strip()
        stem = target.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        hits = by_stem.get(stem, [])
        if not hits:
            stats["dangling"] += 1
            continue
        if len(hits) > 1:
            same = [h for h in hits if h["collection_id"] == by_id[src]["collection_id"]]
            if len(same) != 1:
                stats["ambiguous"] += 1
                continue
            hits = same
        dst = hits[0]["id"]
        if dst == src:
            stats["self"] += 1
            continue
        stats["basename"] += 1
        edges.append((src, dst))
    return edges, stats


def semantic_edges(
    ids: list[str], vecs: np.ndarray, k: int = DEFAULT_K, threshold: float = DEFAULT_THRESHOLD
) -> list[tuple[str, str]]:
    """Mutual-free kNN over cosine similarity, capped at `k` per node and floored at `threshold`.

    Undirected and de-duplicated: an edge is a relationship, and drawing `a→b` and `b→a` as two
    strokes would double the visual weight of exactly the pairs that agree most.
    """
    if len(ids) < 2:
        return []
    sims = vecs @ vecs.T
    np.fill_diagonal(sims, -1.0)
    cap = min(k, sims.shape[0] - 1)
    top = np.argpartition(-sims, cap - 1, axis=1)[:, :cap]
    out: set[tuple[str, str]] = set()
    for i, row in enumerate(top):
        for j in row:
            if sims[i, int(j)] >= threshold:
                a, b = sorted((ids[i], ids[int(j)]))
                out.add((a, b))
    return sorted(out)


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------


def _seed_positions(nodes: list[str], seed: int = LAYOUT_SEED) -> np.ndarray:
    """Starting points derived from each node's own id.

    `rng.normal(size=(n, 2))` looks equivalent and is not. It makes every node's initial position a
    function of how many nodes there are and in what order they arrived, so indexing one new
    document moves the starting point of every document after it — and a force layout amplifies a
    different start into a different picture. Measured on 2026-08-26: with array-order seeding,
    neighbour-set Jaccard@10 between an incrementally-grown map and a full re-layout was 0.249 and
    *did not vary with how much had been added*, which is the signature of a metric measuring its
    own noise. Hash seeding made it monotone immediately.
    """
    pos = np.empty((len(nodes), 2), dtype=np.float32)
    for i, node in enumerate(nodes):
        digest = hashlib.sha256(f"{seed}:{node}".encode()).digest()
        pos[i] = np.frombuffer(digest[:8], dtype=np.uint32).astype(np.float64) / 2**32 - 0.5
    return pos * 0.6


def layout(
    nodes: list[str],
    edges: list[tuple[str, str]],
    *,
    iterations: int = DEFAULT_ITERATIONS,
    seed: int = LAYOUT_SEED,
) -> tuple[np.ndarray, float]:
    """Fruchterman-Reingold, vectorised, deterministic.

    Deterministic because ADR-0013's whole argument is that a layout a person has learned must not
    move on them: same corpus, same seed, same picture.

    The repulsion term is the full NxN pair matrix. Honest at this corpus's scale (1.4k nodes,
    27.8 s, 121 MB) and quadratic in memory — measured projections put ADR-0023's 10k ceiling at
    ~21 min and ~800 MB, inside the time budget and outside the 500 MB one. A corpus 7x this size
    needs Barnes-Hut; this one does not, and adding it now would be untested complexity guarding a
    threshold nobody has crossed.
    """
    n = len(nodes)
    if n == 0:
        return np.zeros((0, 2), dtype=np.float32), 0.0
    index = {node: i for i, node in enumerate(nodes)}
    pos = _seed_positions(nodes, seed)

    pairs = [(index[a], index[b]) for a, b in edges if a in index and b in index]
    src = np.array([a for a, _ in pairs], dtype=np.int32)
    dst = np.array([b for _, b in pairs], dtype=np.int32)

    k = math.sqrt(1.0 / max(n, 1))
    t0 = time.perf_counter()
    temperature = 0.1
    for _ in range(iterations):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(delta, axis=-1)
        np.fill_diagonal(dist, np.inf)
        repel = (k * k) / dist
        disp = np.einsum("ijd,ij->id", delta / dist[..., None], repel)

        if len(src):
            d = pos[src] - pos[dst]
            dl = np.clip(np.linalg.norm(d, axis=1, keepdims=True), 1e-6, None)
            attract = (d / dl) * (dl * dl / k)
            np.add.at(disp, src, -attract)
            np.add.at(disp, dst, attract)

        length = np.clip(np.linalg.norm(disp, axis=1, keepdims=True), 1e-9, None)
        pos += (disp / length) * np.minimum(length, temperature)
        temperature *= 0.97
    return pos, time.perf_counter() - t0


def place_at_neighbour_centroid(
    positions: dict[str, tuple[float, float]],
    neighbours: list[str],
) -> tuple[float, float]:
    """Place one new document at the centroid of its nearest placed neighbours.

    **It moves nothing else**, which is ADR-0023's promise and the only reason incremental placement
    is allowed to exist: a map that reshuffles when a note is saved is a map nobody learns. Measured
    at p95 0.032 ms against a 250 ms budget.

    A newcomer with no placed neighbours goes to the origin rather than to a random point — the
    middle of the map is where the unrelated things already are, and a random position would invent
    a neighbourhood.
    """
    points = [positions[n] for n in neighbours if n in positions]
    if not points:
        return (0.0, 0.0)
    return (
        float(np.mean([p[0] for p in points])),
        float(np.mean([p[1] for p in points])),
    )


# ---------------------------------------------------------------------------
# building the whole thing from a store
# ---------------------------------------------------------------------------


def build(
    store: KnowledgeStore,
    *,
    k: int = DEFAULT_K,
    threshold: float = DEFAULT_THRESHOLD,
) -> KnowledgeGraph:
    """Assemble the graph the view draws, from persisted state only.

    This does **not** lay anything out. It reads the frozen positions, so it is cheap enough to
    serve on request; a document with no position renders as unplaced rather than being quietly
    given one, because silently laying out on a read path is how the viewport starts simulating.
    """
    documents = store.graph_documents()
    ids = [d["id"] for d in documents]
    index = {doc_id: i for i, doc_id in enumerate(ids)}

    explicit_pairs, link_stats = resolve_wikilinks(documents, store.wikilinks())
    vec_ids, vecs = store.document_vectors()
    semantic_pairs = semantic_edges(vec_ids, vecs, k, threshold)

    def to_index(pairs: list[tuple[str, str]]) -> list[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for a, b in pairs:
            if a in index and b in index and a != b:
                i, j = index[a], index[b]
                out.add((min(i, j), max(i, j)))
        return sorted(out)

    explicit = to_index(explicit_pairs)
    # An explicit link and an inferred one between the same pair is one relationship, and it is
    # the explicit one: drawing both would stack a dashed suggestion on top of a solid fact.
    explicit_set = set(explicit)
    semantic = [e for e in to_index(semantic_pairs) if e not in explicit_set]

    degree: defaultdict[int, int] = defaultdict(int)
    for i, j in explicit + semantic:
        degree[i] += 1
        degree[j] += 1

    positions = store.positions()
    with_vector = set(vec_ids)
    nodes: list[GraphNode] = []
    for i, d in enumerate(documents):
        if d["parse_error"]:
            state = "failed"
        elif d["kind"] in UNEMBEDDABLE_KINDS or d["id"] not in with_vector:
            state = "unembeddable"
        else:
            state = "placed"
        nodes.append(
            GraphNode(
                id=d["id"],
                collection=d["collection_id"],
                project=d["project_id"],
                rel_path=d["rel_path"],
                kind=d["kind"],
                indexed_at=d["indexed_at"],
                state=state,
                degree=degree[i],
            )
        )

    orphans = [n for n in nodes if n.degree == 0 and n.state == "placed"]
    stale = [n for n in nodes if n.id not in positions]
    return KnowledgeGraph(
        nodes=nodes,
        explicit_edges=explicit,
        semantic_edges=semantic,
        positions=positions,
        stats={
            "documents": len(nodes),
            "explicit_edges": len(explicit),
            "semantic_edges": len(semantic),
            "orphans": len(orphans),
            "unplaced": len(stale),
            "link_resolution": link_stats,
            "edge_model": {"k": k, "threshold": threshold},
        },
    )


def relayout(
    store: KnowledgeStore,
    *,
    k: int = DEFAULT_K,
    threshold: float = DEFAULT_THRESHOLD,
    iterations: int = DEFAULT_ITERATIONS,
) -> dict[str, Any]:
    """Run the full offline layout and freeze the result.

    **This is an explicit action, never a side effect of indexing.** It destroys spatial memory —
    every position may move — and OQ-22 measurement 4 says fidelity degrades as documents are added
    incrementally (Jaccard@10 0.477 at a 5% holdout against a 0.70 gate), so a re-layout must be
    *offered* on that evidence rather than buried in a background job. It costs 28 s, which is cheap
    enough to offer and far too visible to hide.
    """
    store.backfill_document_vectors()
    documents = store.graph_documents()
    explicit_pairs, _ = resolve_wikilinks(documents, store.wikilinks())
    vec_ids, vecs = store.document_vectors()
    semantic_pairs = semantic_edges(vec_ids, vecs, k, threshold)

    # Only documents with a vector are laid out. A config file has no vector by policy, and giving
    # it a force-directed position would place it by its *links alone* in the middle of a cluster it
    # has no measured relationship to.
    nodes = sorted(set(vec_ids))
    node_set = set(nodes)
    edges = [(a, b) for a, b in explicit_pairs + semantic_pairs if a in node_set and b in node_set]
    pos, elapsed = layout(nodes, edges, iterations=iterations)
    store.save_positions(
        {node: (float(pos[i][0]), float(pos[i][1])) for i, node in enumerate(nodes)},
        placed="layout",
    )
    log.info(
        "rag.graph.relayout",
        nodes=len(nodes),
        edges=len(edges),
        seconds=round(elapsed, 2),
    )
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "iterations": iterations,
        "seconds": round(elapsed, 3),
    }


def place_new_documents(store: KnowledgeStore, *, k: int = DEFAULT_K) -> int:
    """Place documents that have a vector but no position, without moving anything else."""
    unplaced = store.unplaced_documents()
    if not unplaced:
        return 0
    vec_ids, vecs = store.document_vectors()
    placed = {doc_id: (x, y) for doc_id, (x, y, _) in store.positions().items()}
    if not placed:
        return 0

    order = {doc_id: i for i, doc_id in enumerate(vec_ids)}
    placed_ids = [d for d in vec_ids if d in placed]
    placed_vecs = vecs[[order[d] for d in placed_ids]]

    new_positions: dict[str, tuple[float, float]] = {}
    for doc_id in unplaced:
        if doc_id not in order:
            continue
        sims = placed_vecs @ vecs[order[doc_id]]
        top = np.argsort(-sims)[: min(k, len(placed_ids))]
        new_positions[doc_id] = place_at_neighbour_centroid(
            placed, [placed_ids[int(i)] for i in top]
        )
    if new_positions:
        store.save_positions(new_positions, placed="incremental")
    log.info("rag.graph.placed", count=len(new_positions))
    return len(new_positions)
