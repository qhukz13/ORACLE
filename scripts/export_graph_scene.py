#!/usr/bin/env python
"""OQ-22 measurement 2, part one: freeze the measured scene into something a window can draw.

    uv run python scripts/export_graph_scene.py

[OQ-22](docs/OPEN_QUESTIONS.md#oq-22) has four measurements. Three ran on 2026-08-26. The fourth —
canvas vs SVG — was explicitly *not* answered, because it needs `requestAnimationFrame` deltas from
a compositing window on this GPU and the spike had no displayed one. `measure_graph.py` closes by
saying it "emits the frozen positions that harness consumes". This script is the join between the
two: it turns those positions into the exact scene the harness draws, so the rendering question is
asked at the node and edge count measurement 3 actually chose.

**The scene is rebuilt from the frozen artifacts, not from today's index.** The positions come from
`oq22-graph.positions.npz` and the semantic edges from the cached document vectors in
`oq22-docvecs.npz` — both fingerprinted `e342f8a5…`. Only the explicit wikilink edges need the live
`knowledge.db`, and they are filtered to nodes the frozen layout knows about. If the corpus has
drifted since (it has: `C:/Projects` indexes itself, so every commit moves the fingerprint), the
drift is recorded in the output rather than silently changing what gets measured. A rendering
benchmark whose scene changes between runs is not a benchmark.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from measure_graph import census, open_readonly, resolve_links, semantic_edges  # noqa: E402

#: Measurement 3's recommendation, and the only edge model the view is allowed to ship with by
#: default. Hardcoded rather than exposed: this script's job is to reproduce *the measured scene*.
K, THRESHOLD = 4, 0.85


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="D:/ORACLE/data/knowledge.db")
    ap.add_argument("--positions", default="logs/measurements/oq22-graph.positions.npz")
    ap.add_argument("--vectors", default="logs/measurements/oq22-docvecs.npz")
    ap.add_argument("--out", default="apps/desktop/public/bench-scene.json")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    pos_blob = np.load(ROOT / args.positions, allow_pickle=False)
    ids: list[str] = [str(i) for i in pos_blob["ids"]]
    pos = np.asarray(pos_blob["pos"], dtype=np.float32)
    collections: list[str] = [str(c) for c in pos_blob["collection"]]
    index = {doc_id: i for i, doc_id in enumerate(ids)}
    print(f"frozen layout: {len(ids)} nodes")

    vec_blob = np.load(ROOT / args.vectors, allow_pickle=False)
    vec_ids: list[str] = [str(i) for i in vec_blob["ids"]]
    vectors = np.asarray(vec_blob["vectors"], dtype=np.float32)
    layout_fingerprint = str(vec_blob["fingerprint"])
    print(f"cached vectors: {len(vec_ids)} documents  fingerprint {layout_fingerprint[:16]}")

    semantic = semantic_edges(vec_ids, vectors, K, THRESHOLD)
    print(f"semantic edges (k={K} thr={THRESHOLD}): {len(semantic)}")

    # -- explicit edges: the only part that needs the live index ------------
    explicit: list[tuple[str, str]] = []
    live_fingerprint = None
    db_path = Path(args.db)
    if db_path.exists():
        db = open_readonly(db_path)
        c = census(db)
        live_fingerprint = c.fingerprint
        raw, stats = resolve_links(c)
        explicit = [(a, b) for a, b in raw if a in index and b in index]
        print(f"explicit edges: {len(raw)} resolved, {len(explicit)} within the frozen layout")
        if live_fingerprint != layout_fingerprint:
            print(
                "  note: the live index has drifted from the frozen layout "
                f"({live_fingerprint[:16]} vs {layout_fingerprint[:16]}); "
                f"{len(raw) - len(explicit)} edges dropped as out-of-scene"
            )
        print(f"  link resolution: {stats}")
    else:
        print(f"no index at {db_path}; exporting semantic edges only")

    # Undirected, de-duplicated, and index-encoded. An edge is a relationship: drawing a→b and b→a
    # as two strokes would double the visual weight of exactly the pairs that agree most, which is
    # measure_graph.semantic_edges()'s reasoning applied to the explicit half too.
    pairs: set[tuple[int, int]] = set()
    for a, b in list(semantic) + explicit:
        if a in index and b in index:
            i, j = index[a], index[b]
            if i != j:
                pairs.add((min(i, j), max(i, j)))
    edges = sorted(pairs)
    print(f"scene: {len(ids)} nodes, {len(edges)} edges")

    flat: list[int] = []
    for i, j in edges:
        flat.append(i)
        flat.append(j)

    scene = {
        "_": "OQ-22 measurement 2 input. Regenerate: uv run python scripts/export_graph_scene.py",
        "fingerprint": layout_fingerprint,
        "live_fingerprint": live_fingerprint,
        "edge_model": {"k": K, "threshold": THRESHOLD},
        "counts": {"nodes": len(ids), "edges": len(edges), "explicit": len(explicit)},
        "collections": sorted(set(collections)),
        # Parallel arrays, not objects: 1,420 `{"id":…,"x":…}` records cost ~3x the bytes and give
        # the harness nothing it does not get from an index.
        #
        # **Document ids are deliberately not exported.** The renderer draws circles and lines and
        # never reads a label, so the ids would be dead weight in the scene — and they are the
        # user's own vault paths ("notes/AI-ML-Vault/08 - Neural Networks/…"). This file is
        # committed so the benchmark has a stable input, and `C:/Projects` is itself indexed, so
        # committing the paths would fold the private half of the corpus into the lexical index as
        # plain text. A node here is an index and a position; that is all a render benchmark needs.
        "n": len(ids),
        "collection": [sorted(set(collections)).index(c) for c in collections],
        "x": [round(float(v), 4) for v in pos[:, 0]],
        "y": [round(float(v), 4) for v in pos[:, 1]],
        "edges": flat,
    }

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(scene, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}  ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
