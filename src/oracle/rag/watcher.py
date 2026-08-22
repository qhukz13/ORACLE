"""Keeping the index current while the user works (RAG.md §6).

The order of operations is the design, and it is the same order the walker uses:

    watchfiles event
       |
       +- outside every collection root?  -> dropped, no path work at all
       +- denied or excluded?             -> dropped, file never opened
       +- not a type we index?            -> dropped
       +- survived                        -> queued, debounced, then re-indexed

**Everything cheap happens first.** `npm install` produces events in the thousands, and a
watcher that hashes before it filters spends minutes doing it. Nothing here reads a file
until the path has already earned it.

**Debounce, not throttle.** An editor writing a file produces several events — a
temporary file, a rename, a metadata touch. Re-indexing on the first one indexes a
half-written file. The queue waits for a quiet period per path before acting, so a burst
of edits to the same file costs one reindex.

`watchfiles` (Rust `notify`) rather than `watchdog`, for Windows reliability
(TECH_STACK §4).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from oracle.logsink import get_logger
from oracle.rag.chunking import chunk_document
from oracle.rag.collections import (
    Collection,
    CollectionRegistry,
    classify,
    prunable_dirs,
)
from oracle.rag.collections import _matches as matches_glob
from oracle.rag.embedding import Embedder
from oracle.rag.indexer import content_hash, embed_chunked, identifiers, provenance_of
from oracle.rag.store import KnowledgeStore

if TYPE_CHECKING:
    from oracle.rag.cache import EmbeddingCache

log = get_logger(__name__)

#: How long a path must be quiet before it is re-indexed. RAG.md §6 specifies 2 s.
DEBOUNCE_S = 2.0

#: A file being written by another process raises PermissionError on Windows. Retried
#: rather than logged as an error, because it is the *normal* case for a save in flight.
RETRIES = 3
RETRY_BACKOFF_S = 0.4


@dataclass(frozen=True)
class Candidate:
    """A changed path that survived filtering, resolved to its collection."""

    collection: Collection
    project: str
    #: Relative to the *project* directory — `src/token.ts`.
    rel_path: str
    abs_path: Path

    @property
    def key(self) -> str:
        """The identity the store uses: project-prefixed for a code collection.

        A separate property rather than an obvious `rel_path` because the two differ
        exactly where it is easiest to miss. Looking a document up by `rel_path` when it
        was stored under `Asterim/src/token.ts` finds nothing — so an edit re-embeds a
        file that had not changed, and a delete removes nothing while the stale chunks
        keep being retrieved. Both were live until a test caught them.
        """
        return (
            f"{self.project}/{self.rel_path}" if self.collection.include_projects else self.rel_path
        )


class Watcher:
    """Filters raw filesystem events down to documents worth re-indexing."""

    def __init__(self, registry: CollectionRegistry) -> None:
        self.registry = registry
        self._roots: list[tuple[Collection, Path, str]] = []
        # The directory names implied by the patterns, extracted once. `classify_event`
        # used to derive these per *event* and then reach straight for `fnmatch`: 5000
        # paths from an `npm install` cost 2.6 s, and `_batches` runs on the event loop,
        # so that was 2.6 s of the daemon answering nothing. A set lookup over path
        # components settles almost every one of those events instead; the globs are still
        # there, they are just no longer the first thing tried. Measured in
        # `tests/test_rag_service.py`, which is why it is a test and not a comment.
        self._deny_dirs = frozenset(prunable_dirs(registry.deny))
        self._exclude_dirs: dict[str, frozenset[str]] = {}
        for collection in registry.collections:
            if not collection.enabled:
                continue
            self._exclude_dirs[collection.id] = frozenset(prunable_dirs(collection.exclude))
            for root in collection.roots:
                if not collection.include_projects:
                    self._roots.append((collection, root, root.name))
                    continue
                for name in collection.include_projects:
                    unit = root / name
                    if unit.is_dir():
                        self._roots.append((collection, unit, name))

    def watch_roots(self) -> list[Path]:
        return [unit for _, unit, _ in self._roots]

    def classify_event(self, path: Path) -> Candidate | None:
        """The whole filter, in the order that keeps it cheap. None means "ignore"."""
        for collection, unit, project in self._roots:
            try:
                rel = path.relative_to(unit).as_posix()
            except ValueError:
                continue

            absolute = path.as_posix()
            parents = rel.split("/")[:-1]
            # Deny stays ahead of exclude, and both stay ahead of the file-type check, so
            # a denied path is never opened and the deny list is what reports it. Splitting
            # each into "is it under a named directory" and "does it match a pattern" is a
            # speed change, not a policy one: the same patterns decide the same paths.
            if any(part in self._deny_dirs for part in parents) or matches_glob(
                rel, absolute, self.registry.deny
            ):
                # Not logged with the path at info level: a denied path is one we are
                # deliberately not looking at, and echoing it into the log undoes some of
                # the point of denying it.
                log.debug("rag.watch_denied", collection=collection.id)
                return None
            if any(part in self._exclude_dirs[collection.id] for part in parents) or matches_glob(
                rel, absolute, collection.exclude
            ):
                return None
            if classify(path) is None:
                return None
            return Candidate(collection, project, rel, path)
        return None

    def reindex(
        self,
        candidate: Candidate,
        store: KnowledgeStore,
        embedder: Embedder | None,
        cache: EmbeddingCache | None = None,
    ) -> bool:
        """Re-index one document, or drop it if it is gone. True if anything changed."""
        if not candidate.abs_path.exists():
            store.delete_document(candidate.collection.id, candidate.key)
            store.db.commit()
            log.info("rag.watch_removed", path=candidate.key)
            return True

        text = _read_with_retry(candidate.abs_path)
        if text is None:
            return False

        digest = content_hash(text)
        known = store.known_hashes(candidate.collection.id).get(candidate.key)
        if known == digest:
            # An editor touched the file without changing a byte. mtime moved; the
            # content did not; there is nothing to do and nothing to embed.
            log.debug("rag.watch_unchanged", path=candidate.key)
            return False

        from oracle.rag.collections import Document

        stat = candidate.abs_path.stat()
        kind = classify(candidate.abs_path)
        assert kind is not None  # classify_event already refused None
        doc = Document(
            collection=candidate.collection.id,
            project=candidate.project,
            path=candidate.key,
            abs_path=candidate.abs_path,
            kind=kind,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
        chunks = chunk_document(doc, text, obsidian=candidate.collection.obsidian)
        if not chunks:
            return False

        vectors = None
        if embedder is not None and doc.semantic:
            # Through the cache, like the batch indexer. An edit that adds one function to
            # a file leaves every other chunk's text identical, so the common save costs
            # one forward pass rather than one per chunk in the file.
            vectors, _, _ = embed_chunked(embedder, [c.text for c in chunks], cache, batch=16)

        store.put(
            doc,
            chunks,
            vectors,
            content_hash=digest,
            provenance=provenance_of(doc),
            indexed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            idents=[identifiers(c.text) for c in chunks],
            token_counts=[max(1, round(len(c.text) / 3.6)) for c in chunks],
        )
        log.info("rag.watch_reindexed", path=doc.path, chunks=len(chunks))
        return True


def _read_with_retry(path: Path) -> str | None:
    """Read a file another process may still be writing.

    A save in flight raises `PermissionError` on Windows, and that is the normal case for
    a watcher — not an error worth logging as one. Backoff and retry; give up quietly.
    """
    for attempt in range(RETRIES):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if attempt == RETRIES - 1:
                log.debug("rag.watch_locked", path=str(path))
                return None
            import time

            time.sleep(RETRY_BACKOFF_S * (attempt + 1))
        except (OSError, UnicodeDecodeError):
            return None
    return None


async def debounce(
    batches: AsyncIterator[Iterable[Path]], window: float = DEBOUNCE_S
) -> AsyncIterator[set[Path]]:
    """Collect paths until they have been quiet for `window` seconds, then emit them.

    Per batch rather than per path: `watchfiles` already groups events, and one quiet
    period across the group is what makes a `git checkout` of two hundred files a single
    reindex rather than two hundred.
    """
    pending: set[Path] = set()
    iterator = batches.__aiter__()
    nxt: asyncio.Task[Iterable[Path]] | None = None

    while True:
        if nxt is None:
            nxt = asyncio.ensure_future(anext(iterator))
        try:
            paths = await asyncio.wait_for(asyncio.shield(nxt), timeout=window if pending else None)
        except TimeoutError:
            if pending:
                yield pending
                pending = set()
            continue
        except StopAsyncIteration:
            break
        nxt = None
        pending.update(paths)

    if nxt is not None:
        nxt.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await nxt
    if pending:
        yield pending
