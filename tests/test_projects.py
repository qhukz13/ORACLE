"""Project detection.

Classification is by **marker file**, and these tests are built from marker files
rather than from the real `C:\\Projects` — a test that depends on this machine's
directories passes for the wrong reason and fails on any other machine. The live
classification of all seven real projects is recorded in the development log instead.

The invariant worth protecting: **an unclassifiable project reports `unknown` and
offers no commands.** Guessing `npm test` for a project with no test script produces a
confusing failure and teaches the user that the tool cannot be trusted.
"""

from __future__ import annotations

import json
from pathlib import Path

from oracle.core.projects import (
    ProjectKind,
    detect_all,
    detect_project,
    discover_projects,
    read_agent_docs,
)


def _pkg(path: Path, scripts: dict[str, str], deps: dict[str, str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"name": path.parent.name, "scripts": scripts, "devDependencies": deps or {}}),
        encoding="utf-8",
    )


class TestKinds:
    def test_python_project(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        info = detect_project(tmp_path, "Demo")
        assert info.kinds == (ProjectKind.PYTHON,)
        assert info.test[0].program == "uv"
        assert info.test[0].args == ("run", "pytest", "-q")

    def test_python_offers_the_bare_interpreter_as_a_fallback(self, tmp_path: Path) -> None:
        """The pinned `python` is ORACLE's own venv interpreter, so `uv run` is
        preferred — but if uv is not installed, something still has to run."""
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        info = detect_project(tmp_path)
        assert [t.program for t in info.test] == ["uv", "python"]

    def test_node_project_offers_only_declared_scripts(self, tmp_path: Path) -> None:
        _pkg(tmp_path / "package.json", {"test": "vitest run"})
        info = detect_project(tmp_path, "Web")
        assert info.kinds == (ProjectKind.NODE,)
        assert info.test[0].args == ("test", "--silent")
        # No build script declared, so no build command is invented.
        assert info.build == ()

    def test_rust_project(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
        info = detect_project(tmp_path)
        assert info.kinds == (ProjectKind.RUST,)
        assert info.build[0].args == ("build",)

    def test_roblox_project(self, tmp_path: Path) -> None:
        (tmp_path / "default.project.json").write_text("{}", encoding="utf-8")
        info = detect_project(tmp_path)
        assert info.kinds == (ProjectKind.ROBLOX,)

    def test_documentation_only_project(self, tmp_path: Path) -> None:
        """A real shape: 96 markdown files, some images, no build system."""
        (tmp_path / "notes.md").write_text("# hi", encoding="utf-8")
        (tmp_path / "design").mkdir()
        (tmp_path / "design" / "spline.json").write_text("{}", encoding="utf-8")
        assert detect_project(tmp_path).kinds == (ProjectKind.DOCS,)

    def test_empty_directory_is_unknown_not_guessed(self, tmp_path: Path) -> None:
        info = detect_project(tmp_path)
        assert info.kinds == (ProjectKind.UNKNOWN,)
        assert info.test == () and info.build == () and info.lint == ()

    def test_source_code_with_no_marker_is_unknown(self, tmp_path: Path) -> None:
        (tmp_path / "main.c").write_text("int main(){}", encoding="utf-8")
        assert detect_project(tmp_path).kinds == (ProjectKind.UNKNOWN,)


class TestMonorepos:
    def test_nested_packages_are_found_one_level_down(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        _pkg(tmp_path / "apps" / "desktop" / "package.json", {"test": "vitest", "build": "vite"})
        info = detect_project(tmp_path, "ORACLE")

        assert info.kinds == (ProjectKind.PYTHON, ProjectKind.NODE)
        assert info.primary is ProjectKind.PYTHON
        # The root's own suite comes first: a sub-package is a component, not the project.
        assert info.test[0].subdir == ""
        assert any(t.subdir == "apps/desktop" for t in info.test)
        assert info.build[0].subdir == "apps/desktop"

    def test_a_crate_workspace_is_registered_once(self, tmp_path: Path) -> None:
        """Every member of a workspace builds from the root. Registering each one would
        run the same suite N times."""
        (tmp_path / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        for member in ("parser", "viewer"):
            (tmp_path / "crates" / member).mkdir(parents=True)
            (tmp_path / "crates" / member / "Cargo.toml").write_text(
                "[package]\n", encoding="utf-8"
            )
        info = detect_project(tmp_path)
        assert len([t for t in info.test if t.kind is ProjectKind.RUST]) == 1

    def test_build_output_directories_are_never_walked(self, tmp_path: Path) -> None:
        """Source2DemViewer's `target/` alone holds 3,915 files. A classification that
        crawls it is not a classification, it is a disk scan."""
        (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        buried = tmp_path / "target" / "debug" / "deps" / "nested"
        buried.mkdir(parents=True)
        _pkg(buried / "package.json", {"test": "should-never-be-found"})
        info = detect_project(tmp_path)
        assert ProjectKind.NODE not in info.kinds


class TestAgentDocs:
    def test_agent_instructions_are_found_and_readable(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text("Use tabs.", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("Read AGENTS.md.", encoding="utf-8")
        info = detect_project(tmp_path)
        assert info.agent_docs == ("AGENTS.md", "CLAUDE.md")

        docs = read_agent_docs(info)
        assert docs["AGENTS.md"] == "Use tabs."

    def test_agent_docs_are_truncated(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("x" * 10_000, encoding="utf-8")
        docs = read_agent_docs(detect_project(tmp_path), max_chars=100)
        assert len(docs["AGENTS.md"]) == 100


class TestRegistry:
    def test_discovery_skips_dotfiles_and_build_output(self, tmp_path: Path) -> None:
        for name in ("Alpha", "Beta", ".git", "node_modules"):
            (tmp_path / name).mkdir()
        (tmp_path / "loose.txt").write_text("x", encoding="utf-8")
        assert discover_projects(tmp_path) == ["Alpha", "Beta"]

    def test_a_missing_root_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert discover_projects(tmp_path / "nope") == []

    def test_detect_all_keys_by_name(self, tmp_path: Path) -> None:
        (tmp_path / "Alpha").mkdir()
        (tmp_path / "Alpha" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        found = detect_all(tmp_path)
        assert set(found) == {"Alpha"}
        assert found["Alpha"].primary is ProjectKind.RUST
