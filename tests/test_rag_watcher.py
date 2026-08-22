"""The watcher: what it drops, and what it waits for.

Both halves matter for the same reason. A watcher that filters too late spends minutes
hashing `node_modules` during an `npm install`; one that acts too early indexes a file an
editor is still writing. Neither failure raises anything.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

import pytest

from oracle.rag.collections import Collection, CollectionRegistry
from oracle.rag.store import KnowledgeStore
from oracle.rag.watcher import Watcher, debounce

DIM = 4


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "Projects" / "Asterim"
    (root / "src").mkdir(parents=True)
    (root / "src" / "token.ts").write_text("export class TokenService {}\n" + "// x\n" * 40)
    (root / "node_modules" / "left-pad").mkdir(parents=True)
    (root / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1;")
    (root / "Passwords").mkdir()
    (root / "Passwords" / "Passwords.md").write_text("hunter2")
    (root / "logo.png").write_bytes(b"\x89PNG")
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


class TestFiltering:
    def test_a_source_file_is_a_candidate(self, registry: CollectionRegistry, tree: Path) -> None:
        candidate = Watcher(registry).classify_event(tree / "Asterim" / "src" / "token.ts")
        assert candidate is not None
        assert candidate.rel_path == "src/token.ts"
        assert candidate.project == "Asterim"
        # The store keys on the project-prefixed path, and confusing the two silently
        # breaks both the unchanged check and the delete.
        assert candidate.key == "Asterim/src/token.ts"

    def test_node_modules_is_dropped_without_reading_it(
        self, registry: CollectionRegistry, tree: Path
    ) -> None:
        """The `npm install` case: thousands of events, none of them worth a hash."""
        path = tree / "Asterim" / "node_modules" / "left-pad" / "index.js"
        assert Watcher(registry).classify_event(path) is None

    def test_a_denied_path_is_dropped(self, registry: CollectionRegistry, tree: Path) -> None:
        path = tree / "Asterim" / "Passwords" / "Passwords.md"
        assert Watcher(registry).classify_event(path) is None

    def test_an_unindexable_type_is_dropped(self, registry: CollectionRegistry, tree: Path) -> None:
        assert Watcher(registry).classify_event(tree / "Asterim" / "logo.png") is None

    def test_a_path_outside_every_root_is_dropped(
        self, registry: CollectionRegistry, tmp_path: Path
    ) -> None:
        assert Watcher(registry).classify_event(tmp_path / "elsewhere" / "x.ts") is None

    def test_a_project_not_on_the_include_list_is_dropped(
        self, registry: CollectionRegistry, tree: Path
    ) -> None:
        other = tree / "NotIncluded"
        other.mkdir()
        (other / "a.ts").write_text("x")
        assert Watcher(registry).classify_event(other / "a.ts") is None


class TestReindex:
    @pytest.fixture
    def store(self, tmp_path: Path) -> KnowledgeStore:
        s = KnowledgeStore(tmp_path / "knowledge.db", DIM)
        s.bind("fake", DIM)
        return s

    def test_a_changed_file_is_indexed(
        self, registry: CollectionRegistry, tree: Path, store: KnowledgeStore
    ) -> None:
        watcher = Watcher(registry)
        candidate = watcher.classify_event(tree / "Asterim" / "src" / "token.ts")
        assert candidate is not None
        assert watcher.reindex(candidate, store, None) is True
        assert store.stats()["chunks"] > 0

    def test_an_unchanged_file_is_not_reindexed(
        self, registry: CollectionRegistry, tree: Path, store: KnowledgeStore
    ) -> None:
        """An editor touching a file without changing a byte must not cost an embed.

        This is why the gate is a content hash and not mtime — a checkout or a backup
        restore moves mtime across the whole tree.
        """
        watcher = Watcher(registry)
        candidate = watcher.classify_event(tree / "Asterim" / "src" / "token.ts")
        assert candidate is not None
        assert watcher.reindex(candidate, store, None) is True
        candidate.abs_path.touch()
        assert watcher.reindex(candidate, store, None) is False

    def test_a_deleted_file_is_removed_from_the_index(
        self, registry: CollectionRegistry, tree: Path, store: KnowledgeStore
    ) -> None:
        """A deleted file that stays in the index keeps being cited, and the citation
        points at nothing."""
        watcher = Watcher(registry)
        path = tree / "Asterim" / "src" / "token.ts"
        candidate = watcher.classify_event(path)
        assert candidate is not None
        watcher.reindex(candidate, store, None)
        assert store.stats()["chunks"] > 0

        path.unlink()
        assert watcher.reindex(candidate, store, None) is True
        assert store.stats()["chunks"] == 0


class TestDebounce:
    async def _drain(self, batches: list[list[Path]], window: float) -> list[set[Path]]:
        async def source() -> AsyncIterator[Iterable[Path]]:
            for batch in batches:
                yield batch
                await asyncio.sleep(0)

        return [group async for group in debounce(source(), window=window)]

    async def test_a_burst_becomes_one_group(self) -> None:
        """A `git checkout` touching two hundred files is one reindex, not two hundred."""
        paths = [[Path(f"a{i}.ts")] for i in range(50)]
        groups = await self._drain(paths, window=0.05)
        assert len(groups) == 1
        assert len(groups[0]) == 50

    async def test_repeated_edits_to_one_file_collapse(self) -> None:
        """An editor writes a temp file, renames, and touches metadata — three events,
        one file, one reindex."""
        groups = await self._drain([[Path("a.ts")]] * 5, window=0.05)
        assert groups == [{Path("a.ts")}]

    async def test_nothing_in_means_nothing_out(self) -> None:
        assert await self._drain([], window=0.05) == []
