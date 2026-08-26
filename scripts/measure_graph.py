#!/usr/bin/env python
"""OQ-22: does the knowledge-graph view hold its budgets on this corpus and this machine?

    uv run python scripts/measure_graph.py                 # the whole spike
    uv run python scripts/measure_graph.py --census        # what is in the index, no work
    uv run python scripts/measure_graph.py --skip-scaling  # skip the N-scaling projection

[OQ-22](docs/OPEN_QUESTIONS.md#oq-22) commits the design to numbers nobody had measured on this
corpus, and [UI.md §11b](docs/UI.md#11b-the-knowledge-graph--phase-11) describes a view of *"every
indexed document across the Obsidian vaults, project docs and PDFs"*. This script exists to find out
whether either sentence survives contact with the actual index, **before** a layout pass or a canvas
renderer is written on top of them.

**Measurement 3 runs first**, against OQ-22's own ordering, and the reason is in
`logs/development/2026-08-26-p11-graph-data.md`: the edge model decides how many nodes the graph
has, and the node count is what the canvas-vs-SVG question is asked at. Measuring rendering at 166
nodes and concluding "SVG is fine" would be measuring a graph the product may not ship.

Three things this script will not do:

* **It does not write to the index.** It opens a read-only URI (`?mode=ro`), because the live
  `knowledge.db` is the one the daemon is serving from and a measurement that mutates its subject
  is not a measurement.
* **It does not re-embed anything.** Document vectors are mean-pooled from `chunk_vectors` and
  cached to an `.npz` beside the results — the read out of `vec0` is 45 s and the arithmetic on top
  of it is a quarter of a second, so caching is the difference between a spike you can iterate on
  and one you run twice.
* **It does not measure rendering.** That is measurement 2, it needs WebView2 and a GPU, and it
  belongs in the desktop harness. This script emits the frozen positions that harness consumes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"

#: `RAG.md` §2 never embeds config — an embedding of a tsconfig matches everything and means
#: nothing. So a config document has no vector *by policy*, and counting it as an orphan would give
#: UI.md §11b's "neglect" question a false-positive floor the size of the config population. It is a
#: third state, distinct from both "connected" and "failed to index".
UNEMBEDDABLE_KINDS = frozenset({"config"})


# ---------------------------------------------------------------------------
# The index, read-only
# ---------------------------------------------------------------------------


def open_readonly(path: Path) -> sqlite3.Connection:
    """The live index, opened so it cannot be written to even by accident."""
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    import sqlite_vec

    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    return db


@dataclass
class Census:
    docs: list[sqlite3.Row]
    by_id: dict[str, sqlite3.Row]
    links: list[sqlite3.Row]
    #: sha256 over (id, kind, collection) for every document, in id order. Cited in the dev log so
    #: a later run can say whether it measured the same corpus — `C:/Projects` is itself indexed,
    #: so editing a tracked file changes what this script sees.
    fingerprint: str = ""
    counts: dict[str, Any] = field(default_factory=dict)


def census(db: sqlite3.Connection) -> Census:
    docs = db.execute(
        "SELECT id, collection_id, project_id, rel_path, kind, parse_error FROM documents"
        " ORDER BY id"
    ).fetchall()
    links = db.execute("SELECT from_document_id, to_path, kind FROM links").fetchall()

    h = hashlib.sha256()
    for d in docs:
        h.update(f"{d['id']}|{d['kind']}|{d['collection_id']}\n".encode())

    by_kind: defaultdict[str, int] = defaultdict(int)
    by_coll: defaultdict[str, int] = defaultdict(int)
    for d in docs:
        by_kind[d["kind"]] += 1
        by_coll[d["collection_id"]] += 1

    return Census(
        docs=docs,
        by_id={d["id"]: d for d in docs},
        links=links,
        fingerprint=h.hexdigest(),
        counts={
            "documents": len(docs),
            "links": len(links),
            "by_kind": dict(by_kind),
            "by_collection": dict(by_coll),
            "unembeddable": sum(1 for d in docs if d["kind"] in UNEMBEDDABLE_KINDS),
            "parse_error": sum(1 for d in docs if d["parse_error"]),
        },
    )


# ---------------------------------------------------------------------------
# Explicit edges: the resolver the layout and the inspector must share
# ---------------------------------------------------------------------------


def resolve_links(c: Census) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """`links.to_path` is raw wikilink text, not a document id. Resolve it, or say why not.

    **Basename, not path**, and that is the whole finding: a wikilink is written
    `[[Backpropagation]]`, not `[[08 - Neural Networks/Backpropagation.md]]`. Exact `rel_path`
    matching resolves *nothing* on this corpus — a resolver that joined on it would produce an empty
    graph and read as a bug in the layout rather than a bug in the join.

    An ambiguous basename **fails closed to no edge**. In a map whose stated purpose is "show me the
    shape of what I know", a wrong edge is worse than a missing one: it invents a relationship the
    reader then reasons from.
    """
    by_stem: defaultdict[str, list[sqlite3.Row]] = defaultdict(list)
    for d in c.docs:
        stem = d["rel_path"].rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        by_stem[stem].append(d)

    edges: list[tuple[str, str]] = []
    stats = {"exact": 0, "basename": 0, "ambiguous": 0, "dangling": 0, "self": 0}
    for link in c.links:
        src = link["from_document_id"]
        if src not in c.by_id:
            continue
        target = (link["to_path"] or "").strip()
        stem = target.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        hits = by_stem.get(stem, [])
        if not hits:
            stats["dangling"] += 1
            continue
        if len(hits) > 1:
            # Prefer a candidate in the same collection; if that is still ambiguous, refuse.
            same = [h for h in hits if h["collection_id"] == c.by_id[src]["collection_id"]]
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


# ---------------------------------------------------------------------------
# Document vectors: the 45-second read, cached
# ---------------------------------------------------------------------------


def document_vectors(
    db: sqlite3.Connection, c: Census, cache: Path, *, refresh: bool = False
) -> tuple[list[str], np.ndarray, dict[str, float]]:
    """Mean-pooled chunk vectors, one per document, L2-normalised.

    The timing here is the finding, not the vectors: reading the embeddings out of the `vec0`
    virtual table dominates everything downstream by two orders of magnitude. Any design that
    re-derives document vectors on each incremental index blows TESTING.md §6's `< 5 s` budget on
    I/O alone — so the cache is not an optimisation for this script, it is the shape the product
    needs, measured.
    """
    timing: dict[str, float] = {}
    if cache.exists() and not refresh:
        blob = np.load(cache, allow_pickle=False)
        if str(blob["fingerprint"]) == c.fingerprint:
            print(f"{D}  reusing cached document vectors ({cache.name}){X}")
            return list(blob["ids"]), np.asarray(blob["vectors"]), {"cached": 1.0}
        print(f"{Y}  cached vectors were built from a different corpus; re-reading{X}")

    t0 = time.perf_counter()
    rows = db.execute(
        "SELECT c.document_id AS doc, v.embedding AS emb"
        " FROM chunk_vectors v JOIN chunks c ON c.id = v.chunk_id"
    ).fetchall()
    timing["read_vec0_s"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    acc: dict[str, list[np.ndarray]] = defaultdict(list)
    for r in rows:
        acc[r["doc"]].append(np.frombuffer(r["emb"], dtype=np.float32))
    ids = sorted(acc)
    stacked = np.vstack([np.mean(acc[i], axis=0) for i in ids]).astype(np.float32)
    stacked /= np.clip(np.linalg.norm(stacked, axis=1, keepdims=True), 1e-9, None)
    timing["pool_s"] = time.perf_counter() - t0

    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        cache, ids=np.array(ids), vectors=stacked, fingerprint=np.array(c.fingerprint)
    )
    print(
        f"  read {len(rows)} chunk vectors in {timing['read_vec0_s']:.1f}s, "
        f"pooled to {len(ids)} documents in {timing['pool_s']:.2f}s"
    )
    return ids, stacked, timing


def semantic_edges(
    ids: list[str], vecs: np.ndarray, k: int, threshold: float
) -> list[tuple[str, str]]:
    """Mutual-free kNN over cosine similarity, capped at `k` per node and floored at `threshold`.

    Undirected and de-duplicated: an edge is a relationship, and drawing `a→b` and `b→a` as two
    strokes would double the visual weight of exactly the pairs that agree most.
    """
    sims = vecs @ vecs.T
    np.fill_diagonal(sims, -1.0)
    cap = min(k, sims.shape[0] - 1)
    top = np.argpartition(-sims, cap - 1, axis=1)[:, :cap]
    out: set[tuple[str, str]] = set()
    for i, row in enumerate(top):
        for j in row:
            if sims[i, j] >= threshold:
                a, b = sorted((ids[i], ids[int(j)]))
                out.add((a, b))
    return sorted(out)


# ---------------------------------------------------------------------------
# The four questions UI.md §11b says the view exists to answer
# ---------------------------------------------------------------------------


def graph_metrics(
    nodes: list[str], edges: list[tuple[str, str]], c: Census, *, sample: int = 250
) -> dict[str, Any]:
    """Shape, neglect and reach — as numbers, per configuration.

    `reach` is sampled rather than exhaustive: a 2-hop BFS from every node is O(N·E) and the answer
    does not change in the third decimal. The sample is deterministic so two runs of this script
    compare.
    """
    adj: defaultdict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)

    embeddable = [n for n in nodes if c.by_id[n]["kind"] not in UNEMBEDDABLE_KINDS]
    orphans = [n for n in embeddable if not adj[n]]

    cross = sum(1 for a, b in edges if c.by_id[a]["collection_id"] != c.by_id[b]["collection_id"])

    # Connected components, over the whole node set.
    seen: set[str] = set()
    components: list[int] = []
    for n in nodes:
        if n in seen:
            continue
        stack, size = [n], 0
        seen.add(n)
        while stack:
            cur = stack.pop()
            size += 1
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        components.append(size)
    components.sort(reverse=True)

    rng = np.random.default_rng(0xC0FFEE)
    picks = (
        list(rng.choice(np.array(embeddable), size=min(sample, len(embeddable)), replace=False))
        if embeddable
        else []
    )
    two_hop: list[int] = []
    for n in picks:
        one = adj[str(n)]
        reach = set(one)
        for nb in one:
            reach |= adj[nb]
        reach.discard(str(n))
        two_hop.append(len(reach))

    total = max(len(nodes), 1)
    return {
        "edges": len(edges),
        "cross_collection_edges": cross,
        "orphans": len(orphans),
        "orphan_share_of_embeddable": round(len(orphans) / max(len(embeddable), 1), 4),
        "components": len(components),
        "largest_component": components[0] if components else 0,
        "largest_component_share": round((components[0] if components else 0) / total, 4),
        "avg_degree": round(2 * len(edges) / total, 2),
        "two_hop_median": int(np.median(two_hop)) if two_hop else 0,
        "two_hop_median_share": round(float(np.median(two_hop)) / total, 4) if two_hop else 0.0,
        "two_hop_p90": int(np.percentile(two_hop, 90)) if two_hop else 0,
    }


# ---------------------------------------------------------------------------
# Measurement 1: layout
# ---------------------------------------------------------------------------


def layout(
    nodes: list[str], edges: list[tuple[str, str]], *, iterations: int = 200, seed: int = 7
) -> tuple[np.ndarray, float]:
    """Fruchterman-Reingold, vectorised, deterministic.

    Deterministic because ADR-0013's whole argument is that a layout a person has learned must not
    move on them: same corpus, same seed, same picture. The repulsion term is the full NxN pair
    matrix — honest at this corpus's scale and quadratic in memory, which is exactly what the
    scaling projection below is for.
    """
    n = len(nodes)
    index = {node: i for i, node in enumerate(nodes)}
    rng = np.random.default_rng(seed)
    pos = rng.normal(scale=0.3, size=(n, 2)).astype(np.float32)

    src = np.array([index[a] for a, _ in edges], dtype=np.int32)
    dst = np.array([index[b] for _, b in edges], dtype=np.int32)

    area = 1.0
    k = math.sqrt(area / max(n, 1))
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


def place_incrementally(
    pos: np.ndarray, nodes: list[str], newcomer_neighbours: list[int]
) -> tuple[np.ndarray, float]:
    """A new document lands at the centroid of its nearest neighbours and moves nothing else.

    "Moves nothing else" is ADR-0023's promise and the reason incremental placement is allowed to
    exist at all: a map that reshuffles when a note is saved is a map nobody learns.
    """
    t0 = time.perf_counter()
    point = (
        pos[newcomer_neighbours].mean(axis=0)
        if newcomer_neighbours
        else np.zeros(2, dtype=np.float32)
    )
    return point, time.perf_counter() - t0


# ---------------------------------------------------------------------------
# Measurement 4: stability, as a holdout rather than as a week of waiting
# ---------------------------------------------------------------------------


def neighbour_sets(pos: np.ndarray, k: int = 10) -> list[set[int]]:
    d = np.linalg.norm(pos[:, None, :] - pos[None, :, :], axis=-1)
    np.fill_diagonal(d, np.inf)
    cap = min(k, len(pos) - 1)
    return [set(int(j) for j in row[:cap]) for row in np.argsort(d, axis=1)]


def jaccard(a: list[set[int]], b: list[set[int]]) -> float:
    scores = [len(x & y) / len(x | y) if (x | y) else 1.0 for x, y in zip(a, b, strict=True)]
    return float(np.mean(scores)) if scores else 0.0


def holdout_stability(
    nodes: list[str], edges: list[tuple[str, str]], fraction: float, seed: int = 11
) -> dict[str, Any]:
    """Lay out without a slice, place the slice incrementally, then re-layout everything.

    OQ-22 asks whether the map "stays recognisable after a week of real edits", which cannot be
    answered inside a phase. This is the same question with the waiting removed: how far does the
    incrementally-placed map drift from the one a full re-layout would have produced, as a function
    of how much arrived since?
    """
    rng = np.random.default_rng(seed)
    n = len(nodes)
    held = set(rng.choice(n, size=max(1, int(n * fraction)), replace=False).tolist())
    kept = [i for i in range(n) if i not in held]
    kept_nodes = [nodes[i] for i in kept]
    kept_set = set(kept_nodes)
    kept_edges = [(a, b) for a, b in edges if a in kept_set and b in kept_set]

    base, _ = layout(kept_nodes, kept_edges)
    full, _ = layout(nodes, edges)

    # The pre-existing nodes must not have moved: incremental placement adds, never reshuffles.
    kept_index = {node: i for i, node in enumerate(kept_nodes)}
    incremental = np.zeros((n, 2), dtype=np.float32)
    for i, node in enumerate(nodes):
        if node in kept_index:
            incremental[i] = base[kept_index[node]]
    adj: defaultdict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    for i in sorted(held):
        anchors = [kept_index[x] for x in adj[nodes[i]] if x in kept_index]
        incremental[i], _ = place_incrementally(base, kept_nodes, anchors)

    return {
        "fraction": fraction,
        "held_out": len(held),
        "jaccard_at_10": round(jaccard(neighbour_sets(incremental), neighbour_sets(full)), 4),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="D:/ORACLE/data/knowledge.db")
    ap.add_argument("--out", default="logs/measurements/oq22-graph")
    ap.add_argument("--cache", default="logs/measurements/oq22-docvecs.npz")
    ap.add_argument("--census", action="store_true", help="what is in the index, then stop")
    ap.add_argument("--refresh", action="store_true", help="ignore the cached document vectors")
    ap.add_argument("--skip-scaling", action="store_true", help="skip the N-scaling projection")
    ap.add_argument("--iterations", type=int, default=200)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"{R}no index at {db_path}{X}")
        return 2
    db = open_readonly(db_path)
    result: dict[str, Any] = {"db": str(db_path)}

    # -- the corpus ---------------------------------------------------------
    print(f"{B}corpus{X}")
    c = census(db)
    result["census"] = c.counts
    result["fingerprint"] = c.fingerprint
    print(f"  {c.counts['documents']} documents  {c.counts['by_collection']}")
    print(f"  kinds: {c.counts['by_kind']}")
    print(
        f"  unembeddable by policy (config): {c.counts['unembeddable']}"
        f"   parse_error: {c.counts['parse_error']}"
    )
    print(f"{D}  fingerprint {c.fingerprint[:16]}{X}")

    # -- explicit edges -----------------------------------------------------
    explicit, link_stats = resolve_links(c)
    result["explicit_links"] = {"resolved": len(explicit), **link_stats}
    linked = {n for e in explicit for n in e}
    print(f"\n{B}explicit edges (wikilinks){X}")
    print(f"  {len(c.links)} rows -> {len(explicit)} edges   {link_stats}")
    print(
        f"  documents touched: {len(linked)} of {c.counts['documents']} "
        f"({len(linked) / max(c.counts['documents'], 1):.1%})"
    )
    by_coll_linked: defaultdict[str, int] = defaultdict(int)
    for n in linked:
        by_coll_linked[c.by_id[n]["collection_id"]] += 1
    print(f"  by collection: {dict(by_coll_linked)}")
    result["explicit_links"]["documents_touched"] = len(linked)
    result["explicit_links"]["by_collection"] = dict(by_coll_linked)

    if args.census:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out + ".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 0

    # -- document vectors ---------------------------------------------------
    print(f"\n{B}document vectors{X}")
    ids, vecs, vec_timing = document_vectors(db, c, Path(args.cache), refresh=args.refresh)
    result["vector_timing"] = vec_timing
    nodes = [d["id"] for d in c.docs]
    print(f"  {len(ids)} of {len(nodes)} documents have a vector")

    # -- MEASUREMENT 3: the edge model, first ------------------------------
    print(f"\n{B}measurement 3 - the edge model{X}   (k first; threshold is not the knob)")
    baseline = graph_metrics(nodes, explicit, c)
    result["sweep"] = [{"config": "explicit-only", **baseline}]
    hdr = (
        f"  {'config':<18}{'edges':>7}{'cross':>7}{'orph':>7}{'comp':>6}"
        f"{'largest':>9}{'deg':>7}{'2hop':>7}{'2hop%':>8}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    def row(label: str, m: dict[str, Any]) -> None:
        print(
            f"  {label:<18}{m['edges']:>7}{m['cross_collection_edges']:>7}{m['orphans']:>7}"
            f"{m['components']:>6}{m['largest_component_share']:>9.1%}{m['avg_degree']:>7.1f}"
            f"{m['two_hop_median']:>7}{m['two_hop_median_share']:>8.1%}"
        )

    row("explicit-only", baseline)
    for k in (2, 3, 4, 6, 8):
        for thr in (0.80, 0.85, 0.90, 0.95):
            sem = semantic_edges(ids, vecs, k, thr)
            merged = sorted({tuple(sorted(e)) for e in explicit} | set(sem))
            m = graph_metrics(nodes, [(a, b) for a, b in merged], c)
            result["sweep"].append({"config": f"k={k} thr={thr}", "k": k, "threshold": thr, **m})
            row(f"k={k} thr={thr:.2f}", m)

    # -- MEASUREMENT 1: layout cost ----------------------------------------
    print(f"\n{B}measurement 1 - layout cost{X}")
    chosen = semantic_edges(ids, vecs, 4, 0.85)
    graph_edges = sorted({tuple(sorted(e)) for e in explicit} | set(chosen))
    pos, secs = layout(nodes, [(a, b) for a, b in graph_edges], iterations=args.iterations)
    rss = _peak_rss_mb()
    print(
        f"  cold layout: {len(nodes)} nodes, {len(graph_edges)} edges, "
        f"{args.iterations} iterations -> {G}{secs:.1f}s{X}   peak RSS ~{rss:.0f} MB"
    )
    result["layout"] = {
        "nodes": len(nodes),
        "edges": len(graph_edges),
        "iterations": args.iterations,
        "cold_s": round(secs, 2),
        "peak_rss_mb": round(rss, 1),
    }

    adj: defaultdict[str, set[str]] = defaultdict(set)
    for a, b in graph_edges:
        adj[a].add(b)
        adj[b].add(a)
    index = {n: i for i, n in enumerate(nodes)}
    incr = []
    for n in nodes[:200]:
        anchors = [index[x] for x in adj[n]]
        _, took = place_incrementally(pos, nodes, anchors)
        incr.append(took * 1000)
    p95 = float(np.percentile(incr, 95)) if incr else 0.0
    gate = G if p95 <= 250 else R
    print(
        f"  incremental placement: p50 {np.median(incr):.3f} ms  "
        f"{gate}p95 {p95:.3f} ms{X}   gate <= 250 ms"
    )
    result["layout"]["incremental_p95_ms"] = round(p95, 4)

    if not args.skip_scaling:
        print(f"\n{D}  scaling (synthetic, to find where the full-matrix approach stops){X}")
        result["scaling"] = []
        for n in (500, 1000, 2000, 4000):
            fake = [f"n{i}" for i in range(n)]
            fe = [(f"n{i}", f"n{(i * 7 + 3) % n}") for i in range(n * 3)]
            _, s = layout(fake, fe, iterations=30)
            projected = s * (args.iterations / 30)
            print(
                f"    N={n:<6} 30 iters {s:6.2f}s   -> {args.iterations} iters ~{projected:6.1f}s"
                f"   pair matrix {n * n * 2 * 4 / 1e6:.0f} MB"
            )
            result["scaling"].append(
                {"n": n, "iters30_s": round(s, 2), "projected_s": round(projected, 1)}
            )

    # -- MEASUREMENT 4: stability ------------------------------------------
    print(f"\n{B}measurement 4 - incremental stability (holdout){X}")
    result["stability"] = []
    for frac in (0.05, 0.10, 0.20):
        st = holdout_stability(nodes, [(a, b) for a, b in graph_edges], frac)
        mark = G if st["jaccard_at_10"] >= 0.7 else R
        print(
            f"  holdout {frac:.0%}: {mark}Jaccard@10 {st['jaccard_at_10']:.3f}{X}"
            f"   gate >= 0.70 at 5%"
        )
        result["stability"].append(st)

    # -- positions, for measurement 2's harness -----------------------------
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out.with_suffix(".positions.npz"),
        ids=np.array(nodes),
        pos=pos,
        collection=np.array([c.by_id[n]["collection_id"] for n in nodes]),
    )
    out.with_suffix(".json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n{D}  wrote {out.with_suffix('.json')} and {out.with_suffix('.positions.npz')}{X}")
    print(f"{D}  measurement 2 (canvas vs SVG) consumes the positions file and needs WebView2{X}")
    return 0


def _peak_rss_mb() -> float:
    """Peak resident set, or -1 where the platform will not say.

    Reported rather than assumed: OQ-22 sets a 500 MB gate, and a gate measured as "we did not
    check" is not a gate.
    """
    try:
        import psutil  # type: ignore[import-not-found]

        return float(psutil.Process(os.getpid()).memory_info().peak_wset) / 1e6  # type: ignore[attr-defined]
    except Exception:  # noqa: S110 - psutil is optional; the POSIX path is tried next
        pass
    try:
        import resource

        return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024
    except Exception:
        return -1.0


if __name__ == "__main__":
    sys.exit(main())
