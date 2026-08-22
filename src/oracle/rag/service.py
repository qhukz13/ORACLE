"""The watcher's lifecycle owner: keeping the index current while the daemon runs.

`watcher.py` decides *what* to re-index; this decides *when*, *on whose thread*, and *who
gets told*. Three properties, each of which is a constraint from somewhere else in the
system rather than a preference:

* **Nothing CPU-bound touches the event loop.** Embedding is a synchronous ONNX forward
  pass measured at ~200 ms per batch on this machine, and a `git checkout` can queue two
  hundred files behind it. All of it runs in a worker thread, so a reindex cannot stall a
  websocket, a HALT, or a health probe. This is the acceptance criterion for P5-T2
  requirement 4, and it is why the whole module is shaped around one `to_thread` call.

* **The model is loaded on first use, not at startup.** The embedder is ~1 GB resident and
  several seconds to load. A daemon that runs all day without an edit in an indexed
  project should never pay for it, and paying for it during startup would delay the port
  opening.

* **HALT stops it, and only a human restarts it.** The service is spawned through
  `AppState.spawn`, so the existing HALT path — which cancels every tracked task — already
  reaches it (docs/SECURITY.md#emergency-stop-halt). That is deliberate reuse: a second
  stop mechanism is a second thing to get wrong. `resume` spawns it again.

The state it publishes is a `knowledge.state` event, because the UI cannot poll for
something that takes a variable number of seconds and the user needs to know why the fan
is running.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from oracle.logsink import get_logger
from oracle.rag.collections import CollectionRegistry
from oracle.rag.embedding import DEFAULT, Embedder
from oracle.rag.store import KnowledgeStore, SchemaMismatch
from oracle.rag.watcher import Candidate, Watcher, debounce

if TYPE_CHECKING:
    from oracle.rag.cache import EmbeddingCache

log = get_logger(__name__)

#: Emitted so the desktop app can show that the machine is busy on the user's behalf.
EVENT = "knowledge.state"


class IndexService:
    """Watches the declared collections and keeps `knowledge.db` current."""

    def __init__(
        self,
        registry: CollectionRegistry,
        data_dir: Path,
        *,
        publish: Callable[[str, dict[str, object]], Awaitable[None]],
        embed: bool = True,
    ) -> None:
        self.registry = registry
        self.data_dir = data_dir
        self.publish = publish
        self.embed = embed
        self.watcher = Watcher(registry)
        self._store: KnowledgeStore | None = None
        self._embedder: Embedder | None = None
        self._cache: EmbeddingCache | None = None
        self._loaded = False

    # -- the loop ----------------------------------------------------------

    async def run(self) -> None:
        """Watch until cancelled. Never raises: a dead watcher must not be silent."""
        roots = self.watcher.watch_roots()
        if not roots:
            log.info("rag.watch_disabled", reason="no enabled collection roots")
            return

        log.info("rag.watch_started", roots=len(roots))
        await self.publish(EVENT, {"state": "watching", "roots": len(roots)})
        try:
            async for batch in debounce(self._batches(roots)):
                await self._handle(batch)
        except asyncio.CancelledError:
            # The normal shutdown path, and the HALT path. Both are expected.
            log.info("rag.watch_stopped")
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("rag.watch_failed", error=str(exc))
            await self.publish(EVENT, {"state": "failed", "error": str(exc)})
        finally:
            # Synchronously, not through `to_thread`: this runs while the task is being
            # cancelled, and an `await` here would be cancelled in turn, leaking the sqlite
            # handles it exists to close. Closing two connections costs microseconds.
            self._close()

    async def _batches(self, roots: list[Path]) -> AsyncIterator[Iterable[Path]]:
        """`watchfiles` events, reduced to paths and filtered before anything opens them.

        Filtering here rather than after debouncing is what makes `npm install` cheap:
        thousands of events under `node_modules` are dropped on a path comparison, so the
        debounce queue never grows and no file is ever read.
        """
        from watchfiles import awatch

        async for changes in awatch(*roots, recursive=True):
            paths = [Path(raw) for _, raw in changes]
            kept = [p for p in paths if self.watcher.classify_event(p) is not None]
            if kept:
                yield kept

    async def _handle(self, batch: set[Path]) -> None:
        candidates: list[Candidate] = []
        for path in sorted(batch):
            candidate = self.watcher.classify_event(path)
            if candidate is not None:
                candidates.append(candidate)
        if not candidates:
            return

        await self.publish(EVENT, {"state": "indexing", "pending": len(candidates)})
        started = time.perf_counter()
        try:
            changed = await asyncio.to_thread(self._apply, candidates)
        except SchemaMismatch as exc:
            # The index was built by a different model. Not stale — wrong. Rebuilding it
            # from here would be a surprise reindex of the whole corpus, so it is reported
            # and left for the user to trigger (RAG.md §9).
            log.warning("rag.watch_schema_mismatch", error=str(exc))
            await self.publish(EVENT, {"state": "stale", "error": str(exc)})
            return
        elapsed = (time.perf_counter() - started) * 1000

        log.info("rag.watch_batch", seen=len(candidates), changed=changed, ms=round(elapsed))
        await self.publish(
            EVENT,
            {
                "state": "watching",
                "indexed": changed,
                "seen": len(candidates),
                "ms": round(elapsed),
            },
        )

    # -- the worker thread -------------------------------------------------

    def _apply(self, candidates: list[Candidate]) -> int:
        """Re-index a batch. Runs off the event loop; everything here may block."""
        self._ensure_loaded()
        assert self._store is not None
        changed = 0
        for candidate in candidates:
            try:
                if self.watcher.reindex(candidate, self._store, self._embedder, self._cache):
                    changed += 1
            except OSError as exc:
                # One unreadable file must not abandon the rest of the batch.
                log.warning("rag.watch_document_failed", path=candidate.key, error=str(exc))
        return changed

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        from oracle.rag.cache import EmbeddingCache, cache_path

        self._store = KnowledgeStore(self.data_dir / "knowledge.db", DEFAULT.out_dim)
        self._store.bind(DEFAULT.name, DEFAULT.out_dim)
        if self.embed:
            try:
                self._embedder = Embedder(DEFAULT)
                self._cache = EmbeddingCache(
                    cache_path(self.data_dir, DEFAULT.name, DEFAULT.out_dim),
                    DEFAULT.name,
                    DEFAULT.out_dim,
                )
            except FileNotFoundError as exc:
                # No model on disk. The lexical half of the index is still worth keeping
                # current, and saying so once beats failing every batch.
                log.warning("rag.watch_no_model", error=str(exc))
        self._loaded = True

    def _close(self) -> None:
        for resource in (self._cache, self._store):
            if resource is not None:
                with contextlib.suppress(Exception):
                    resource.close()
        self._cache = self._store = None
        self._embedder = None
        self._loaded = False
