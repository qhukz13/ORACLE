"""Registering a project must grant nothing (docs/PROJECT_STATE.md §7, ADR-0024).

A `projects` row is a **label on work**. Filesystem scopes live in `config/policy.yaml`,
where a human edits them and git records the edit. If registering a project could widen a
scope — even by one directory — then "discover the projects on this machine" would be
privilege escalation with a friendly name, reachable from an HTTP endpoint.

The second half of this file checks the other claim the subsystem makes: that
`ProjectObservation` reaches git **through the tool layer**. That is an architectural
claim, and architectural claims decay the moment one `subprocess` import looks convenient
— so it is checked against the source, the same way `test_no_shell.py` checks the shell
ban and `test_orchestration_boundary.py` checks the scheduler.
"""

from __future__ import annotations

import ast
from pathlib import Path

import aiosqlite
import pytest

from oracle.core.project_state import ProjectStore, observe
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.policy.paths import PathRejected

MODULE = Path(__file__).resolve().parents[2] / "src" / "oracle" / "core" / "project_state.py"

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
  deny_always:
    - "**/*.env"
tools:
  fs.read:  {{ tier: T0, scopes: [projects] }}
  git.status: {{ tier: T0, scopes: [projects] }}
  git.log:  {{ tier: T0, scopes: [projects] }}
"""


@pytest.fixture
def scoped_root(tmp_path: Path) -> Path:
    r = tmp_path / "Projects"
    (r / "Asterim").mkdir(parents=True)
    return r


@pytest.fixture
def engine(tmp_path: Path, scoped_root: Path) -> PolicyEngine:
    p = tmp_path / "policy.yaml"
    p.write_text(POLICY.format(root=scoped_root.as_posix()), encoding="utf-8")
    return PolicyEngine(load_policy(p))


def _scope_roots(engine: PolicyEngine) -> set[str]:
    return {f"{scope.name}:{scope.key}:{scope.writable}" for scope in engine.policy.scopes}


class TestRegistrationGrantsNothing:
    async def test_registering_does_not_widen_a_scope(
        self, conn: aiosqlite.Connection, engine: PolicyEngine, tmp_path: Path
    ) -> None:
        """The headline assertion. Register a project that sits **outside** every scope
        and the policy engine's roots must be byte-for-byte what they were."""
        outside = tmp_path / "Elsewhere" / "Secret"
        outside.mkdir(parents=True)
        before = _scope_roots(engine)

        await ProjectStore(conn).register("Secret", outside)

        assert _scope_roots(engine) == before

    async def test_a_path_denied_before_registration_is_denied_after(
        self, conn: aiosqlite.Connection, engine: PolicyEngine, tmp_path: Path
    ) -> None:
        """Comparing the scope list would miss a widening that happened somewhere else,
        so this asks the question that actually matters: can the path be resolved?"""
        outside = tmp_path / "Elsewhere" / "Secret"
        outside.mkdir(parents=True)
        target = outside / "notes.txt"
        target.write_text("private", encoding="utf-8")

        with pytest.raises(PathRejected):
            engine.resolve_path(str(target))

        await ProjectStore(conn).register("Secret", outside)

        with pytest.raises(PathRejected):
            engine.resolve_path(str(target))

    async def test_registration_writes_only_to_the_projects_table(
        self, conn: aiosqlite.Connection, scoped_root: Path
    ) -> None:
        """A registration that also touched `policy`-adjacent state would be a grant in
        disguise. There is no such table, and this pins that there is not."""
        await ProjectStore(conn).register("Asterim", scoped_root / "Asterim")

        async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cur:
            tables = {row["name"] for row in await cur.fetchall()}

        assert "projects" in tables
        assert not (tables & {"scopes", "grants", "permissions", "policy"})


class TestObservationCrossesTheGate:
    """`observe()` may not reach git except through a tool contract."""

    def test_the_module_never_imports_a_process_launcher(self) -> None:
        forbidden = {"subprocess", "os", "asyncio.subprocess", "multiprocessing", "shutil"}
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
        assert not (found & forbidden), f"project_state must not import {found & forbidden}"

    def test_the_module_calls_no_process_spawning_builtin(self) -> None:
        """Belt and braces against a lazy import inside a function body: no call anywhere
        in the module may name one of these, however it was reached."""
        banned = {"system", "popen", "spawn", "spawnv", "execv", "run", "Popen", "check_output"}
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        offenders = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in banned
        ]
        assert offenders == [], f"process spawn reached directly: {offenders}"

    async def test_observation_only_ever_asks_for_read_only_git_contracts(
        self, conn: aiosqlite.Connection, scoped_root: Path
    ) -> None:
        """Every tool `observe()` reaches for must be one of the two T0 read contracts.

        A future edit that added, say, `git.stash` to "clean the tree before looking" would
        turn a page-load into a mutation of someone's working copy.
        """
        project = await ProjectStore(conn).register("Asterim", scoped_root / "Asterim")
        asked: list[str] = []

        class Recorder:
            async def execute(self, tool_id: str, args: dict, **_: object) -> object:
                asked.append(tool_id)
                return type("O", (), {"ok": False, "result": None, "error": None})()

        await observe(Recorder(), project)  # type: ignore[arg-type]

        assert asked
        assert set(asked) <= {"git.status", "git.log"}

    async def test_a_denied_git_call_is_reported_not_raised(
        self, conn: aiosqlite.Connection, scoped_root: Path
    ) -> None:
        """A project outside the policy scope resolves to a denial. The surface that
        renders it must still render."""
        project = await ProjectStore(conn).register("Asterim", scoped_root / "Asterim")

        class Denier:
            async def execute(self, tool_id: str, args: dict, **_: object) -> object:
                err = type("E", (), {"message": "path rejected: outside_scope"})()
                return type("O", (), {"ok": False, "result": None, "error": err})()

        obs = await observe(Denier(), project)  # type: ignore[arg-type]

        assert obs.error == "path rejected: outside_scope"
        assert obs.branch is None
