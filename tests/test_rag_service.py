"""The watcher under the daemon: does a save land, and does the loop stay alive?

`test_rag_watcher.py` covers what gets filtered and when it fires. This covers the two
claims that only hold once the thing is actually running inside the event loop, and both
are P5-T2 acceptance criteria that say **measured, not asserted by inspection**:

* a file saved in an indexed project becomes retrievable within 10 s, and
* a burst on the scale of an `npm install` does not stall the event loop.

Hermetic: the collection roots are `tmp_path`, so the suite never watches the developer's
real projects, and `embed=False` keeps the ~1 GB embedding model out of a unit run. That
narrows what the first test proves to the lexical half — which is the half that carries a
freshly saved file anyway, and the dense half shares every line of the path up to the
forward pass.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from oracle.rag.collections import Collection, CollectionRegistry
from oracle.rag.service import IndexService

#: The acceptance criterion. The 2 s debounce is inside it, not on top of it.
BUDGET_S = 10.0


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "Projects" / "Asterim"
    (root / "src").mkdir(parents=True)
    (root / "src" / "existing.ts").write_text("export const already = 1;\n")
    return tmp_path / "Projects"


@pytest.fixture
def registry(tree: Path) -> CollectionRegistry:
    return CollectionRegistry(
        deny=("**/Passwords/**",),
        collections=(
            Collection(
                id="projects",
                kind="code",
                roots=(tree,),
                include_projects=("Asterim",),
                exclude=("**/node_modules/**",),
            ),
        ),
    )


def service(registry: CollectionRegistry, tmp_path: Path) -> tuple[IndexService, list[dict]]:
    published: list[dict] = []

    async def publish(_type: str, payload: dict) -> None:
        published.append(payload)

    return (
        IndexService(registry, tmp_path / "data", publish=publish, embed=False),
        published,
    )


async def until(predicate, budget: float = BUDGET_S) -> float:
    """Wait for `predicate`, returning how long it took. Raises on the budget."""
    started = time.perf_counter()
    while time.perf_counter() - started < budget:
        if predicate():
            return time.perf_counter() - started
        await asyncio.sleep(0.05)
    raise AssertionError(f"not satisfied within {budget}s")


class TestASaveLands:
    async def test_a_new_file_is_retrievable_within_the_budget(
        self, registry: CollectionRegistry, tree: Path, tmp_path: Path
    ) -> None:
        svc, published = service(registry, tmp_path)
        task = asyncio.create_task(svc.run())
        await until(lambda: any(p.get("state") == "watching" for p in published))

        (tree / "Asterim" / "src" / "vault.ts").write_text(
            "export function deriveVaultKey(salt: Buffer): Buffer {\n"
            "  return pbkdf2Sync(passphrase, salt, 100000, 32, 'sha512');\n"
            "}\n"
        )

        def found() -> bool:
            store = getattr(svc, "_store", None)
            return store is not None and bool(store.search_lexical("deriveVaultKey", 5))

        elapsed = await until(found)
        assert elapsed < BUDGET_S

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_it_says_what_it_is_doing(
        self, registry: CollectionRegistry, tree: Path, tmp_path: Path
    ) -> None:
        """The UI cannot poll for something of unpredictable duration, so the service
        publishes. A user whose fan spins up is owed an explanation."""
        svc, published = service(registry, tmp_path)
        task = asyncio.create_task(svc.run())
        await until(lambda: any(p.get("state") == "watching" for p in published))
        (tree / "Asterim" / "src" / "b.ts").write_text("export const b = 2;\n")

        await until(lambda: any(p.get("state") == "indexing" for p in published))
        await until(lambda: any("indexed" in p for p in published))

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestTheLoopStaysAlive:
    async def test_a_slow_batch_does_not_stall_the_event_loop(
        self, registry: CollectionRegistry, tree: Path, tmp_path: Path
    ) -> None:
        """The reason `_apply` is behind `to_thread` at all.

        A synchronous ONNX forward pass is the real blocker; a synchronous sleep is a
        faithful stand-in for one, and is what lets this run without the model. If the
        batch were applied on the loop, the ticker below would simply stop — so the
        measurement is of loop *lag*, not of how long the batch took.
        """
        svc, _ = service(registry, tmp_path)

        def slow(candidates: list) -> int:
            time.sleep(1.0)
            return len(candidates)

        svc._apply = slow  # type: ignore[method-assign]

        lags: list[float] = []

        async def ticker() -> None:
            while True:
                mark = time.perf_counter()
                await asyncio.sleep(0.01)
                lags.append(time.perf_counter() - mark - 0.01)

        tick = asyncio.create_task(ticker())
        started = time.perf_counter()
        # The real `_handle`, not a re-implementation of it: the structure under test is
        # the `to_thread` hop inside it.
        await svc._handle({tree / "Asterim" / "src" / "existing.ts"})
        blocked = time.perf_counter() - started
        tick.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tick

        assert blocked >= 1.0, "the stand-in for the forward pass really did take a second"
        # Generous, deliberately: this suite shares a machine with whatever else is
        # running, and the failure it must catch is a *stopped* loop — hundreds of
        # milliseconds — not scheduler jitter.
        assert max(lags) < 0.30, f"event loop stalled for {max(lags) * 1000:.0f} ms"

    async def test_an_npm_install_is_filtered_before_anything_is_opened(
        self, registry: CollectionRegistry, tree: Path, tmp_path: Path
    ) -> None:
        """The cheap half of the same claim: the events never reach the queue.

        Five thousand paths under `node_modules`, none of which exist on disk — which is
        the point. If filtering needed to stat or read them, this could not pass.
        """
        svc, _ = service(registry, tmp_path)
        modules = tree / "Asterim" / "node_modules"
        paths = [modules / f"pkg{i}" / "index.js" for i in range(5000)]

        started = time.perf_counter()
        kept = [p for p in paths if svc.watcher.classify_event(p) is not None]
        elapsed = time.perf_counter() - started

        assert kept == []
        assert elapsed < 2.0, f"filtering 5000 paths took {elapsed:.1f}s"
