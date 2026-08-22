"""Building and updating the index (RAG.md §6).

The loop is: walk, hash, skip what has not changed, chunk, embed, upsert, prune orphans.
The interesting part is what is *skipped*, because that is what makes an incremental
update seconds rather than an hour:

    ignored path?        -> dropped during the walk, never opened
    hash unchanged?      -> dropped before chunking, never embedded
    changed              -> re-chunked and re-embedded, then the document is replaced

**Content hash, not mtime.** `mtime` alone is unreliable on Windows — a checkout, a
restore from backup or a touch all move it without changing a byte, and re-embedding a
project because git checked it out would cost the better part of an hour. The hash is
what decides; the mtime is stored only for diagnostics.

Measured cost of a full build on this machine: `multilingual-e5-base` runs at ~2.7
chunks/s on 24 Haswell threads, so ~9.4k chunks is roughly **an hour**. That number is
why `--changed-only` is the normal path and a full rebuild is an explicit act.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from oracle.logsink import get_logger
from oracle.rag.cache import EmbeddingCache, text_hash
from oracle.rag.chunking import Chunk, chunk_document
from oracle.rag.collections import (
    CollectionRegistry,
    ContentKind,
    Document,
    WalkStats,
    walk,
)
from oracle.rag.embedding import PASSAGE, Embedder
from oracle.rag.pdf import extract as extract_pdf
from oracle.rag.store import KnowledgeStore

log = get_logger(__name__)

#: Files written *for* an agent to obey. Read, never executed, and always attributed to
#: the project rather than to ORACLE: this is `local_foreign` content, it taints any turn
#: built from it, and the whole point of the taint machinery is that a note saying
#: "ignore previous instructions" changes nothing (SECURITY.md §6).
_AGENT_DOCS = frozenset({"agents.md", "claude.md", ".cursorrules", "conventions.md"})

#: Path segments that mean "someone else wrote this". `node_modules` is excluded from the
#: walk entirely, but vendored trees are not always excluded and are not ours.
_FOREIGN_SEGMENTS = frozenset({"vendor", "vendored", "third_party", "thirdparty", "external"})

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PARTS = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


@dataclass
class IndexStats:
    documents: int = 0
    unchanged: int = 0
    chunks: int = 0
    embedded: int = 0
    pruned: int = 0
    failed: int = 0
    #: Chunks whose vector came from the cache instead of the model. On a re-chunk this
    #: is most of them, and it is the difference between 43 minutes and a few.
    cached: int = 0
    seconds: float = 0.0
    walk: WalkStats = field(default_factory=WalkStats)

    @property
    def chunks_per_second(self) -> float:
        return self.embedded / self.seconds if self.seconds else 0.0

    @property
    def cache_hit_rate(self) -> float:
        total = self.cached + self.embedded
        return self.cached / total if total else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "documents": self.documents,
            "unchanged": self.unchanged,
            "chunks": self.chunks,
            "embedded": self.embedded,
            "pruned": self.pruned,
            "failed": self.failed,
            "cached": self.cached,
            "seconds": round(self.seconds, 1),
            "chunks_per_second": round(self.chunks_per_second, 2),
            "walk": self.walk.as_dict(),
        }


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def provenance_of(doc: Document) -> str:
    """`local_owned` or `local_foreign` (SECURITY.md §6).

    Foreign means "written by someone who is not the user", which is the property that
    matters for injection: an `AGENTS.md` in a third-party checkout is a file full of
    imperative instructions aimed squarely at an agent. Marking it foreign is what makes
    the gate escalate rather than obey.
    """
    if doc.kind is ContentKind.PDF:
        # Nobody writes a PDF in Obsidian. Every one in this corpus is something the user
        # acquired — a textbook, a paper, a datasheet — so it is text by someone else, and
        # that is exactly what `local_foreign` means. The rule is a generalisation, and it
        # is the one that fails safe: being wrong here escalates the policy tier of a plan
        # built on it and never relaxes it (SECURITY.md §6).
        return "local_foreign"
    lowered = doc.path.lower()
    if Path(lowered).name in _AGENT_DOCS and doc.project != "ORACLE":
        return "local_foreign"
    if any(segment in _FOREIGN_SEGMENTS for segment in lowered.split("/")):
        return "local_foreign"
    return "local_owned"


def identifiers(text: str) -> str:
    """Identifiers exploded into parts, for the `ident` FTS column (OQ-08).

    `entitlementGuard` becomes `entitlementGuard entitlement Guard`, so a search for
    `entitlement` finds it. unicode61 already splits on `_`, so snake_case needs nothing
    here; camelCase is the case it cannot do and cannot be configured to do.
    """
    out: list[str] = []
    for token in _IDENT.findall(text):
        parts = _PARTS.findall(token)
        if len(parts) > 1:
            out.append(token)
            out.extend(parts)
    return " ".join(dict.fromkeys(out))


def _read(doc: Document) -> str | None:
    """The document's text, or None if it has none we can get at."""
    if doc.kind is ContentKind.PDF:
        return extract_pdf(doc.abs_path)
    try:
        return doc.abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("rag.unreadable", path=doc.path, error=str(exc))
        return None


def embed_chunked(
    embedder: Embedder,
    texts: list[str],
    cache: EmbeddingCache | None,
    batch: int,
) -> tuple[np.ndarray, int, int]:
    """Vectors for `texts`, reusing whatever the cache already knows.

    Returns `(vectors, cached, embedded)`. Order is preserved, and the cache is consulted
    per *text* rather than per document: a file that gained one function reuses the
    vectors for every function it did not.
    """
    if cache is None or not texts:
        # An empty batch defers to the embedder for its empty shape rather than guessing
        # one: `np.vstack([])` raises, and this function does not know the dimension.
        return embedder.encode(texts, PASSAGE, batch=batch), 0, len(texts)

    hashes = [text_hash(t) for t in texts]
    known = cache.get_many(hashes)
    missing = [i for i, h in enumerate(hashes) if h not in known]

    fresh: dict[int, np.ndarray] = {}
    if missing:
        computed = embedder.encode([texts[i] for i in missing], PASSAGE, batch=batch)
        fresh = dict(zip(missing, computed, strict=True))
        # Written before the caller can fail. A crash mid-index should not throw away
        # forward passes that have already been paid for.
        cache.put_many((hashes[i], v) for i, v in fresh.items())

    vectors = np.vstack([fresh[i] if i in fresh else known[hashes[i]] for i in range(len(texts))])
    return vectors, len(texts) - len(missing), len(missing)


def index(
    registry: CollectionRegistry,
    store: KnowledgeStore,
    embedder: Embedder | None,
    *,
    only: str | None = None,
    full: bool = False,
    batch: int = 16,
    cache: EmbeddingCache | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> IndexStats:
    """Bring the index up to date with what is on disk.

    `embedder=None` builds the lexical half only. That is not a testing affordance —
    it is the degraded mode: if the ONNX model is missing, BM25 alone is a materially
    worse search but it is a working one, and refusing to index at all would be worse
    than both (ARCHITECTURE.md §8).
    """
    started = time.perf_counter()
    stats = IndexStats()
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    for collection in registry.collections:
        if not collection.enabled or (only is not None and collection.id != only):
            continue
        known = {} if full else store.known_hashes(collection.id)
        seen: list[str] = []

        for doc in walk(registry, only=collection.id, stats=stats.walk):
            seen.append(doc.path)
            text = _read(doc)
            if text is None:
                stats.failed += 1
                continue

            digest = content_hash(text)
            if known.get(doc.path) == digest:
                stats.unchanged += 1
                continue

            chunks = chunk_document(doc, text, obsidian=collection.obsidian)
            if not chunks:
                continue

            vectors = None
            if embedder is not None and doc.semantic:
                vectors, hit, missed = embed_chunked(
                    embedder, [c.text for c in chunks], cache, batch
                )
                stats.cached += hit
                stats.embedded += missed

            store.put(
                doc,
                chunks,
                vectors,
                content_hash=digest,
                provenance=provenance_of(doc),
                indexed_at=now,
                idents=[identifiers(c.text) for c in chunks],
                token_counts=[_approx_tokens(c.text) for c in chunks],
            )
            stats.documents += 1
            stats.chunks += len(chunks)
            if progress is not None:
                progress(stats.documents, stats.chunks)

        stats.pruned += store.prune(collection.id, seen)

    # Once per build, because the fusion gate needs it per query and it costs a full scan
    # (see `KnowledgeStore.record_script_census`).
    store.record_script_census()

    stats.seconds = time.perf_counter() - started
    log.info("rag.indexed", **stats.as_dict())
    return stats


def _approx_tokens(text: str) -> int:
    """Characters over 3.6. Stored for budgeting, never for chunking decisions.

    An exact count would mean running the tokenizer a second time over every chunk, and
    nothing downstream needs better than an estimate — the Context Assembler's budget is
    checked against the real tokenizer at assembly time (AGENT_RUNTIME.md §5).
    """
    return max(1, round(len(text) / 3.6))


def iter_documents(registry: CollectionRegistry, only: str | None = None) -> Iterator[Document]:
    """Public alias for the walk, so callers do not import `collections` directly."""
    yield from walk(registry, only=only)


def chunks_of(doc: Document, *, obsidian: bool = False) -> list[Chunk]:
    """Read and chunk one document, or return nothing if it cannot be read."""
    text = _read(doc)
    return chunk_document(doc, text, obsidian=obsidian) if text is not None else []


def embed_chunks(embedder: Embedder, chunks: list[Chunk], batch: int = 16) -> np.ndarray:
    """Embed chunk texts as passages. A thin, correctly-prefixed wrapper.

    It exists so that no caller anywhere writes `encode(texts, QUERY)` for a passage:
    the wrong prefix does not raise, it just retrieves worse (RAG.md §4).
    """
    return embedder.encode([c.text for c in chunks], PASSAGE, batch=batch)


def kind_counts(registry: CollectionRegistry) -> dict[str, int]:
    """What the walk would yield, by content kind. For the index health view."""
    counts: dict[str, int] = {}
    for doc in walk(registry):
        key = doc.kind.value if isinstance(doc.kind, ContentKind) else str(doc.kind)
        counts[key] = counts.get(key, 0) + 1
    return counts
