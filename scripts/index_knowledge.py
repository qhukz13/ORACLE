#!/usr/bin/env python
"""Build or update `knowledge.db`, and report against the Phase 5 acceptance criteria.

    uv run python scripts/index_knowledge.py --full        # rebuild from scratch
    uv run python scripts/index_knowledge.py               # incremental
    uv run python scripts/index_knowledge.py --stats       # what is indexed, no work
    uv run python scripts/index_knowledge.py --measure     # recall + latency on the fixtures

A *measurement* script as much as a build one: the acceptance criteria for this phase are
numbers about the real corpus (full build time, incremental time, retrieval p95,
recall@5), and none of them can be asserted in a unit test — they need the actual index
over the actual machine.

A full build re-embeds everything and costs roughly an hour on this CPU. The incremental
path is the normal one, and it is what the `< 5 s` criterion is about.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oracle.config import Settings
from oracle.logsink import configure
from oracle.rag.cache import EmbeddingCache, cache_path, warm_from_index
from oracle.rag.collections import load_registry
from oracle.rag.embedding import E5_BASE, Embedder
from oracle.rag.indexer import index
from oracle.rag.retrieval import retrieve
from oracle.rag.store import KnowledgeStore

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures/retrieval/cases.yaml"
COLLECTIONS = ROOT / "config/collections.yaml"

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"


def measure(store: KnowledgeStore, embedder: Embedder, limit: int = 5) -> int:
    """Recall@5 and retrieval latency over the fixture set. Returns an exit code."""
    cases = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))["cases"]
    latencies: list[float] = []
    hits = 0
    misses: list[str] = []
    by_kind: dict[str, list[bool]] = {}

    for case in cases:
        started = time.perf_counter()
        found = retrieve(case["q"], store, embedder, limit=limit)
        latencies.append((time.perf_counter() - started) * 1000)

        paths = [h.rel_path for h in found.hits]
        ok = any(any(e in p or p.endswith(e) for e in case["expect_any"]) for p in paths)
        by_kind.setdefault(case["kind"], []).append(ok)
        if ok:
            hits += 1
        else:
            misses.append(case["id"])

    recall = hits / len(cases)
    p50 = statistics.median(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1]

    print(f"\n{B}retrieval{X}  ({len(cases)} fixtures, top {limit})")
    mark = G if recall >= 0.80 else R
    print(f"  recall@{limit}  {mark}{recall:.0%}{X}   gate is 80%")
    for kind, results in sorted(by_kind.items()):
        share = sum(results) / len(results)
        print(f"    {kind:<10} {share:.0%}  ({sum(results)}/{len(results)})")
    if misses:
        print(f"  {Y}misses:{X} {misses}")

    lat = G if p95 < 400 else R
    print(f"  latency   p50 {p50:.0f} ms · {lat}p95 {p95:.0f} ms{X}   gate is p95 < 400 ms")
    return 0 if recall >= 0.80 and p95 < 400 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="re-embed everything (~1 hour)")
    ap.add_argument("--collection", default=None)
    ap.add_argument("--stats", action="store_true", help="report what is indexed, do nothing")
    ap.add_argument("--measure", action="store_true", help="run the fixture set after indexing")
    ap.add_argument("--no-embed", action="store_true", help="lexical half only")
    ap.add_argument("--no-cache", action="store_true", help="ignore the embedding cache")
    ap.add_argument(
        "--warm-cache",
        action="store_true",
        help="seed the cache from vectors this index already holds, then exit",
    )
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    configure(None, "warning")  # the table is the output; the log is not

    settings = Settings()
    db_path = Path(args.db) if args.db else settings.data_dir / "knowledge.db"
    store = KnowledgeStore(db_path, E5_BASE.out_dim)
    store.bind(E5_BASE.name, E5_BASE.out_dim)

    if args.stats:
        print(json.dumps(store.stats(), indent=2, ensure_ascii=False))
        return 0

    embedder = None
    if not args.no_embed:
        try:
            embedder = Embedder(E5_BASE)
        except FileNotFoundError as exc:
            print(f"{Y}  {exc}{X}\n  continuing with the lexical index only.")

    cache = None
    if embedder is not None and not args.no_cache:
        cache = EmbeddingCache(
            cache_path(settings.data_dir, E5_BASE.name, E5_BASE.out_dim),
            E5_BASE.name,
            E5_BASE.out_dim,
        )

    if args.warm_cache:
        if cache is None:
            print(f"{R}  --warm-cache needs the embedding model and the cache enabled{X}")
            return 1
        added = warm_from_index(cache, store.db)
        print(f"  seeded {added} vectors -> {cache.size()} entries, {cache.path}")
        return 0

    registry = load_registry(COLLECTIONS)
    print(f"{B}indexing{X}  {'full rebuild' if args.full else 'incremental'} -> {db_path}")

    last = [time.perf_counter()]

    def progress(documents: int, chunks: int) -> None:
        now = time.perf_counter()
        if now - last[0] >= 10.0:
            print(f"{D}  {documents} documents, {chunks} chunks…{X}", flush=True)
            last[0] = now

    stats = index(
        registry,
        store,
        embedder,
        only=args.collection,
        full=args.full,
        cache=cache,
        progress=progress,
    )

    minutes = stats.seconds / 60
    budget = G if minutes < 10 else R
    print(
        f"  {stats.documents} documents ({stats.unchanged} unchanged), {stats.chunks} chunks, "
        f"{stats.embedded} embedded, {stats.cached} from cache, {stats.pruned} pruned, "
        f"{stats.failed} unreadable"
    )
    if cache is not None:
        print(
            f"{D}  cache: {stats.cache_hit_rate:.0%} hit, {cache.size()} entries, "
            f"{cache.path.stat().st_size / 1e6:.0f} MB{X}"
        )
    print(
        f"  {budget}{stats.seconds:.1f}s ({minutes:.1f} min){X} at "
        f"{stats.chunks_per_second:.1f} chunks/s   gate is < 10 min for a full build"
    )
    print(f"{D}  walk: {stats.walk.as_dict()}{X}")
    print(f"{D}  index file: {store.stats()['file_bytes'] / 1e6:.0f} MB{X}")

    if args.measure:
        if embedder is None:
            print(f"{R}  cannot measure recall without the embedding model{X}")
            return 1
        return measure(store, embedder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
