"""Fixtures for the security suite.

The reparse-point tree is built with **real** junctions via `mklink /J`, not mocks. A
mock encodes the behaviour you assumed, which is exactly the bug this suite exists to
catch — `Path.is_symlink()` returning False for a junction would have been invisible
to a mocked test.

Junctions need no elevation; directory symlinks do. On a machine without Developer Mode
or admin (this one), symlink-based tests skip and junction-based tests still run —
which is the right priority, because an unprivileged attacker can only make junctions.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from oracle.policy.paths import PathResolver, Scope


def _try_junction(link: Path, target: Path) -> bool:
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0 and link.exists()


def _try_dir_symlink(link: Path, target: Path) -> bool:
    r = subprocess.run(
        ["cmd", "/c", "mklink", "/D", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0 and link.exists()


@dataclass
class Tree:
    base: Path
    allowed: Path
    outside: Path
    junction: Path | None
    symlink: Path | None


@pytest.fixture
def tree(tmp_path: Path) -> Iterator[Tree]:
    base = tmp_path / "sandbox"
    allowed = base / "allowed"
    outside = base / "outside"
    allowed.mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET")
    (allowed / "normal.txt").write_text("ok")
    (allowed / "sub").mkdir()

    junction = allowed / "junc"
    if not _try_junction(junction, outside):
        junction = None  # type: ignore[assignment]

    symlink = allowed / "dsym"
    if not _try_dir_symlink(symlink, outside):
        symlink = None  # type: ignore[assignment]

    yield Tree(base=base, allowed=allowed, outside=outside, junction=junction, symlink=symlink)


@pytest.fixture
def resolver(tree: Tree) -> PathResolver:
    return PathResolver(
        scopes=[Scope(name="allowed", root=tree.allowed, writable=True)],
        deny=["**/.ssh/**", "**/*.env", "**/.git/hooks/**"],
    )
