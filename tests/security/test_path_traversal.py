"""Red-team suite for the path canonicaliser.

Merge gate from Phase 2 on (docs/TESTING.md#3-security-tests-are-a-merge-gate). Every
case here corresponds to a real Windows behaviour measured on this machine, not to a
threat imagined in the abstract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from oracle.policy.paths import (
    PathRejected,
    PathResolver,
    Reason,
    Scope,
    contains,
)

from .conftest import Tree

# --------------------------------------------------------------- reparse points


class TestReparsePoints:
    def test_junction_escape_is_denied(self, resolver: PathResolver, tree: Tree) -> None:
        """The headline case. A junction inside an allowed root pointing outside it.

        Unprivileged users CAN create junctions on Windows, so this is the realistic
        escape, not the symlink one."""
        if tree.junction is None:
            pytest.skip("could not create a junction on this volume")
        assert (tree.junction / "secret.txt").read_text() == "SECRET", (
            "fixture is not a real escape"
        )

        with pytest.raises(PathRejected) as exc:
            resolver.resolve(str(tree.junction / "secret.txt"))
        assert exc.value.reason is Reason.OUTSIDE_SCOPE

    def test_junction_is_invisible_to_is_symlink(self, tree: Tree) -> None:
        """Documents WHY the resolver must not shortcut on `is_symlink()`.

        If this ever starts returning True, the shortcut becomes tempting again — the
        assertion is here to keep the reason visible."""
        if tree.junction is None:
            pytest.skip("could not create a junction on this volume")
        assert tree.junction.is_symlink() is False
        assert Path(tree.junction).is_symlink() is False

    def test_symlink_escape_is_denied(self, resolver: PathResolver, tree: Tree) -> None:
        if tree.symlink is None:
            pytest.skip("symlink creation needs admin or Developer Mode")
        with pytest.raises(PathRejected) as exc:
            resolver.resolve(str(tree.symlink / "secret.txt"))
        assert exc.value.reason is Reason.OUTSIDE_SCOPE

    def test_path_through_junction_that_stays_inside_is_allowed(
        self, resolver: PathResolver, tree: Tree
    ) -> None:
        """Guards against over-blocking: a reparse point is not automatically an escape."""
        inner = tree.allowed / "inner"
        inner.mkdir()
        (inner / "f.txt").write_text("x")
        import subprocess

        link = tree.allowed / "inner_link"
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(inner)], capture_output=True, text=True
        )
        if r.returncode != 0:
            pytest.skip("could not create a junction")
        resolved = resolver.resolve(str(link / "f.txt"))
        assert resolved.real == (inner / "f.txt").resolve()


# ------------------------------------------------------------------- traversal


class TestTraversal:
    @pytest.mark.parametrize(
        "attack",
        [
            r"..\..\..\Windows\System32\config\SAM",
            r"sub\..\..\outside\secret.txt",
            r"sub/../../outside/secret.txt",
        ],
    )
    def test_dotdot_escapes_denied(self, resolver: PathResolver, tree: Tree, attack: str) -> None:
        with pytest.raises(PathRejected):
            resolver.resolve(attack, cwd=tree.allowed)

    @pytest.mark.parametrize("attack", [r"....//....//outside//secret.txt", r"...\...\outside"])
    def test_dot_runs_are_literal_names_not_traversal(
        self, resolver: PathResolver, tree: Tree, attack: str
    ) -> None:
        """`....` is a classic bypass string, but on Win32 it is an ordinary (if odd)
        directory name, not a parent reference. The right assertion is therefore *no
        escape*, not *rejection* — asserting rejection would bake in a false belief
        about how Windows resolves these."""
        try:
            resolved = resolver.resolve(attack, cwd=tree.allowed)
        except PathRejected:
            return
        assert contains(tree.allowed, resolved.real)

    def test_traversal_that_lands_back_inside_is_allowed(
        self, resolver: PathResolver, tree: Tree
    ) -> None:
        """A false positive here would make the sandbox unusable for ordinary paths."""
        resolved = resolver.resolve(str(tree.allowed / "sub" / ".." / "normal.txt"))
        assert resolved.real.name == "normal.txt"

    def test_absolute_path_outside_scope_denied(self, resolver: PathResolver) -> None:
        with pytest.raises(PathRejected) as exc:
            resolver.resolve(r"C:\Windows\System32\drivers\etc\hosts")
        assert exc.value.reason is Reason.OUTSIDE_SCOPE

    def test_relative_path_without_cwd_is_refused(self, resolver: PathResolver) -> None:
        """Never guess a base directory: it is how a 'safe' relative path escapes."""
        with pytest.raises(PathRejected) as exc:
            resolver.resolve("normal.txt")
        assert exc.value.reason is Reason.NOT_ABSOLUTE


# ------------------------------------------------------- windows-specific forms


class TestWindowsForms:
    @pytest.mark.parametrize(
        "attack",
        [
            r"\\?\C:\Windows\System32",
            r"\\.\C:",
            r"\\localhost\C$\Windows",
            r"\\server\share\file.txt",
            "//server/share/file.txt",
        ],
    )
    def test_unc_and_device_paths_denied(self, resolver: PathResolver, attack: str) -> None:
        """MEASURED: realpath returns these unchanged, so they must be rejected by
        inspection rather than normalised away."""
        with pytest.raises(PathRejected) as exc:
            resolver.resolve(attack)
        assert exc.value.reason is Reason.DEVICE_PATH

    def test_alternate_data_stream_denied(self, resolver: PathResolver, tree: Tree) -> None:
        """MEASURED: an ADS write succeeds, the file's size is unchanged, and realpath
        does NOT strip the stream. Only an explicit check catches it."""
        with pytest.raises(PathRejected) as exc:
            resolver.resolve(str(tree.allowed / "normal.txt") + ":hidden")
        assert exc.value.reason is Reason.ALTERNATE_DATA_STREAM

    def test_drive_letter_colon_is_not_mistaken_for_a_stream(
        self, resolver: PathResolver, tree: Tree
    ) -> None:
        assert resolver.resolve(str(tree.allowed / "normal.txt")).existed

    def test_8dot3_short_name_is_expanded_before_matching(
        self, resolver: PathResolver, tree: Tree
    ) -> None:
        """MEASURED: 8.3 aliases are enabled on this volume and realpath expands them.
        Resolving before matching is what makes that safe."""
        import subprocess

        longdir = tree.outside / "Program Files Like"
        longdir.mkdir()
        (longdir / "x.txt").write_text("secret")
        r = subprocess.run(
            ["cmd", "/c", "dir", "/X", str(tree.outside)], capture_output=True, text=True
        )
        short = next((p for line in r.stdout.splitlines() for p in line.split() if "~" in p), None)
        if not short:
            pytest.skip("8.3 name generation is disabled on this volume")
        with pytest.raises(PathRejected) as exc:
            resolver.resolve(str(tree.outside / short / "x.txt"))
        assert exc.value.reason is Reason.OUTSIDE_SCOPE

    @pytest.mark.parametrize("suffix", [".", " ", "...", ". . ."])
    def test_trailing_dots_and_spaces_cannot_dodge_a_deny_rule(
        self, tmp_path: Path, suffix: str
    ) -> None:
        """MEASURED: Windows strips trailing dots/spaces, so `.env.` opens `.env`.
        Matching the deny rule AFTER resolution is what closes this."""
        root = tmp_path / "proj"
        root.mkdir()
        (root / ".env").write_text("SECRET=1")
        resolver = PathResolver([Scope("proj", root, writable=True)], deny=["**/*.env"])

        with pytest.raises(PathRejected) as exc:
            resolver.resolve(str(root / ".env"))
        assert exc.value.reason is Reason.DENIED

        with pytest.raises(PathRejected) as exc:
            resolver.resolve(str(root / (".env" + suffix)))
        assert exc.value.reason is Reason.DENIED, f"deny rule dodged with suffix {suffix!r}"

    def test_case_insensitive_containment(self, resolver: PathResolver, tree: Tree) -> None:
        upper = str(tree.allowed).upper() + "\\NORMAL.TXT"
        assert resolver.resolve(upper).real.name.lower() == "normal.txt"

    def test_sibling_prefix_is_not_containment(self, tmp_path: Path) -> None:
        """`C:\\Projects-evil` must not count as inside `C:\\Projects`. A string
        `startswith` check gets this wrong; component comparison does not."""
        good = tmp_path / "Projects"
        evil = tmp_path / "Projects-evil"
        good.mkdir()
        evil.mkdir()
        (evil / "x.txt").write_text("x")
        assert not contains(good, evil / "x.txt")

        resolver = PathResolver([Scope("p", good, writable=True)])
        with pytest.raises(PathRejected) as exc:
            resolver.resolve(str(evil / "x.txt"))
        assert exc.value.reason is Reason.OUTSIDE_SCOPE


# ------------------------------------------------------------------- deny rules


class TestDenyRules:
    @pytest.mark.parametrize("rel", [".ssh/id_rsa", "app/.env", ".git/hooks/pre-commit"])
    def test_deny_beats_allow(self, tmp_path: Path, rel: str) -> None:
        root = tmp_path / "proj"
        (root / Path(rel)).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
        resolver = PathResolver(
            [Scope("proj", root, writable=True)],
            deny=["**/.ssh/**", "**/*.env", "**/.git/hooks/**"],
        )
        with pytest.raises(PathRejected) as exc:
            resolver.resolve(str(root / rel))
        assert exc.value.reason is Reason.DENIED

    def test_longest_scope_wins(self, tmp_path: Path) -> None:
        """A nested read-only scope must beat a broad writable one, or a narrow
        exception silently inherits write access."""
        outer = tmp_path / "outer"
        inner = outer / "vendor"
        inner.mkdir(parents=True)
        (inner / "f.txt").write_text("x")
        resolver = PathResolver(
            [Scope("outer", outer, writable=True), Scope("vendor", inner, writable=False)]
        )
        resolved = resolver.resolve(str(inner / "f.txt"))
        assert resolved.scope.name == "vendor"
        assert resolved.writable is False


# ---------------------------------------------------------------------- syntax


class TestSyntaxRejection:
    @pytest.mark.parametrize(
        "raw,reason",
        [
            ("", Reason.EMPTY),
            ("   ", Reason.EMPTY),
            ("C:\\a\x00b", Reason.NUL_BYTE),
            ("C:\\proj\\*.txt", Reason.WILDCARD),
            ("C:\\proj\\?.txt", Reason.WILDCARD),
            ("%USERPROFILE%\\.ssh\\id_rsa", Reason.ENV_EXPANSION),
            ("$HOME/.ssh/id_rsa", Reason.ENV_EXPANSION),
        ],
    )
    def test_rejected_before_resolution(
        self, resolver: PathResolver, raw: str, reason: Reason
    ) -> None:
        with pytest.raises(PathRejected) as exc:
            resolver.resolve(raw)
        assert exc.value.reason is reason


# ----------------------------------------------------------------------- TOCTOU


class TestTOCTOU:
    def test_swapping_a_path_for_a_junction_after_approval_is_caught(
        self, resolver: PathResolver, tree: Tree
    ) -> None:
        """Closes the window between 'the user approved this' and 'we ran it'."""
        import shutil
        import subprocess

        victim = tree.allowed / "swapme"
        victim.mkdir()
        (victim / "f.txt").write_text("benign")
        resolved = resolver.resolve(str(victim))
        resolver.recheck(resolved)  # fine so far

        shutil.rmtree(victim)
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(victim), str(tree.outside)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            pytest.skip("could not create a junction")

        with pytest.raises(PathRejected) as exc:
            resolver.recheck(resolved)
        assert exc.value.reason is Reason.CHANGED_UNDER_US


# ------------------------------------------------------------------- property


class TestProperties:
    @settings(max_examples=300, deadline=None)
    @given(
        st.lists(
            st.sampled_from(
                [
                    "a",
                    "B",
                    "sub",
                    "normal.txt",
                    "C:",
                    "~",
                    "PROGRA~1",
                    ".",
                    "..",
                    "...",
                    "....",
                    "\\",
                    "/",
                    "\\\\",
                    ":",
                    "*",
                    "?",
                    "%USERPROFILE%",
                    "$HOME",
                    " ",
                    "\x00",
                    "outside",
                ]
            ),
            min_size=0,
            max_size=14,
        ).map("".join)
    )
    def test_never_escapes_the_scope(
        self, tmp_path_factory: pytest.TempPathFactory, raw: str
    ) -> None:
        """The input space is far too large to enumerate by hand. Whatever comes in,
        the resolver either raises or returns something provably inside a scope."""
        root = tmp_path_factory.mktemp("prop")
        resolver = PathResolver([Scope("root", root, writable=True)])
        try:
            resolved = resolver.resolve(raw, cwd=root)
        except PathRejected:
            return
        except (OSError, ValueError):
            return  # OS-level rejection is also a refusal
        assert contains(root, resolved.real), f"{raw!r} escaped to {resolved.real}"
