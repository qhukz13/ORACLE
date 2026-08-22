"""Red-team suite for the indexing gate.

Merge gate (docs/TESTING.md#3-security-tests-are-a-merge-gate). The collection registry
is the only thing standing between "ORACLE knows my projects" and "ORACLE read my
password file, embedded it, and will hand it to whatever asks a related question".

The concrete case this suite exists for was found by walking this machine on
2026-08-22: `C:/Users/qhukz/Documents/ObsidianNotes` is a declared notes root in
docs/RAG.md §2, and it contains a `Passwords/` folder holding `Passwords.md` and
`Bank accounts.md`. A root is not a promise that everything under it is indexable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oracle.rag.collections import (
    Collection,
    CollectionRegistry,
    ContentKind,
    WalkStats,
    classify,
    load_registry,
    prunable_dirs,
    walk,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_CONFIG = REPO_ROOT / "config/collections.yaml"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A notes root shaped like the real one: useful notes, and secrets beside them."""
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "Attention.md").write_text("# Attention\nscaled dot product")
    (tmp_path / "notes" / "Passwords").mkdir()
    (tmp_path / "notes" / "Passwords" / "Passwords.md").write_text("hunter2")
    (tmp_path / "notes" / "Passwords" / "Bank accounts.md").write_text("acct 123")
    (tmp_path / "notes" / ".env").write_text("API_KEY=sk-live-abc")
    (tmp_path / "notes" / "deploy.pem").write_text("-----BEGIN PRIVATE KEY-----")
    (tmp_path / "notes" / ".obsidian").mkdir()
    (tmp_path / "notes" / ".obsidian" / "workspace.json").write_text("{}")
    return tmp_path / "notes"


def _registry(root: Path, exclude: tuple[str, ...] = ("**/.obsidian/**",)) -> CollectionRegistry:
    return CollectionRegistry(
        deny=("**/Passwords/**", "**/*.env", "**/*.pem"),
        collections=(Collection(id="notes", kind="markdown", roots=(root,), exclude=exclude),),
    )


class TestDenyList:
    def test_denied_directory_is_not_indexed(self, vault: Path) -> None:
        """The headline case: a secrets folder inside a legitimately declared root."""
        docs = list(walk(_registry(vault)))
        paths = [d.path for d in docs]
        assert "Attention.md" in paths, "the fixture indexes nothing at all"
        assert not any("Passwords" in p for p in paths)

    @pytest.mark.parametrize("name", [".env", "deploy.pem"])
    def test_denied_file_patterns(self, vault: Path, name: str) -> None:
        assert name not in [d.path for d in walk(_registry(vault))]

    def test_deny_beats_an_include(self, vault: Path) -> None:
        """A collection cannot opt back in to something the top-level deny refused.

        Deny is evaluated first and there is no override, because the alternative is a
        configuration in which one careless line silently re-exposes every secret.
        """
        registry = _registry(vault, exclude=())
        assert not any("Passwords" in d.path for d in walk(registry))

    def test_refusals_are_counted_and_attributed_to_the_deny_list(self, vault: Path) -> None:
        """Refusals are observable, and a deny is distinguishable from a plain prune.

        `Passwords/` is skipped by the same directory-pruning machinery as
        `node_modules`, so without a separate counter the health view would report "2
        directories pruned" and the fact that the deny list fired at all would be
        invisible. That distinction is the whole reason the deny list is separate from
        `exclude`.
        """
        stats = WalkStats()
        list(walk(_registry(vault), stats=stats))
        assert stats.denied == 2, ".env and deploy.pem, refused by path"
        assert stats.denied_dirs == 1, "Passwords/, refused before descending into it"
        assert stats.pruned_dirs == 1, ".obsidian/, an ordinary exclusion"

    def test_deny_matches_before_the_file_is_opened(self, vault: Path) -> None:
        """The rule is applied to the path, not to the contents.

        Asserted by denying a file that cannot be read at all: if the walker had to open
        it to classify it, this would raise instead of skipping.
        """
        secret = vault / "unreadable.env"
        secret.write_bytes(b"\xff\xfe\x00binary")
        assert "unreadable.env" not in [d.path for d in walk(_registry(vault))]


class TestExclusionsAndPruning:
    def test_excluded_directory_is_not_indexed(self, vault: Path) -> None:
        assert not any(".obsidian" in d.path for d in walk(_registry(vault)))

    def test_build_output_never_reaches_the_file_check(self, tmp_path: Path) -> None:
        """`**/name/**` patterns prune during descent.

        Source2DemViewer holds 3,893 files under `target/` and is not a git repository,
        so the exclude glob is the only thing that stops them. Pruning has to happen on
        the directory, not on 3,893 individual paths.
        """
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.rs").write_text("fn main() {}")
        deep = tmp_path / "target" / "debug" / "incremental"
        deep.mkdir(parents=True)
        for i in range(50):
            (deep / f"{i}.rs").write_text("generated")

        stats = WalkStats()
        registry = CollectionRegistry(
            collections=(
                Collection(id="p", kind="code", roots=(tmp_path,), exclude=("**/target/**",)),
            )
        )
        docs = list(walk(registry, stats=stats))
        assert [d.path for d in docs] == ["src/main.rs"]
        # Pruned as one directory, so the 50 files were never even stat'd.
        assert stats.excluded == 0
        assert stats.pruned_dirs >= 1

    def test_prunable_dirs_only_accepts_whole_directory_patterns(self) -> None:
        """Anything the walker cannot prove is a directory exclusion falls through to
        the per-file check — slower, but never wrong."""
        assert prunable_dirs(("**/node_modules/**", "**/*.lock", "**/a*b/**")) == {"node_modules"}


class TestShippedConfig:
    """The config that actually ships has to hold these properties, not just a fixture."""

    def test_it_parses(self) -> None:
        registry = load_registry(SHIPPED_CONFIG)
        assert {c.id for c in registry.collections} == {"projects", "notes"}

    def test_the_documents_root_is_not_a_collection_root(self) -> None:
        """`Documents/` at large holds Paradox saves, League configs and Arma 3 data.

        Indexing it is not a feature on this machine (docs/RAG.md §1), and a root that
        broad would also re-admit everything the deny list exists to keep out.
        """
        for collection in load_registry(SHIPPED_CONFIG).collections:
            for root in collection.roots:
                assert root.as_posix().rstrip("/").lower() != "c:/users/qhukz/documents"

    def test_passwords_are_denied(self) -> None:
        assert "**/Passwords/**" in load_registry(SHIPPED_CONFIG).deny

    def test_no_collection_root_is_a_drive_root(self) -> None:
        for collection in load_registry(SHIPPED_CONFIG).collections:
            for root in collection.roots:
                assert len(root.parts) > 1, f"{root} is a whole drive"


class TestClassification:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("token.ts", ContentKind.CODE),
            ("notes.md", ContentKind.MARKDOWN),
            ("tsconfig.json", ContentKind.CONFIG),
            ("Dockerfile.relay", ContentKind.CONFIG),
            ("book.pdf", ContentKind.PDF),
            ("logo.png", None),
            ("archive.7z", None),
            ("model.safetensors", None),
        ],
    )
    def test_classify(self, name: str, expected: ContentKind | None) -> None:
        assert classify(Path(name)) is expected

    def test_config_is_lexical_only(self) -> None:
        """An embedding of a tsconfig.json is close to everything and means nothing."""
        assert not ContentKind.CONFIG.semantic
        assert ContentKind.CODE.semantic and ContentKind.MARKDOWN.semantic
