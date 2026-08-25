"""The scheduler feeds the gate; it is not a second one (SECURITY.md §10, rule 1).

A task graph is the newest way to ask ORACLE to do something, and therefore the newest
place a bypass could appear: a scheduler that grew its own execution path would be a
second, unreviewed chokepoint with none of the policy engine's history behind it.

These tests check the boundary against the source, the same way `test_no_shell.py` checks
the shell ban — because "the scheduler doesn't execute anything" is an architectural claim,
and architectural claims decay the moment one import looks convenient. They are cheap and
they fail loudly the first time somebody wires a tool call into the loop instead of into a
runner.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from oracle.orchestration import Scheduler, TaskGraph, TaskKind, TaskStatus
from oracle.orchestration.models import Task, TaskResult, TaskSpec

ORCHESTRATION = Path(__file__).resolve().parents[2] / "src" / "oracle" / "orchestration"

#: Layers the supervisor must reach only through an injected runner. `policy` is on the
#: list for the same reason as `tools`: a scheduler that evaluates policy itself is a
#: scheduler that can be argued into a different answer than the executor would give.
FORBIDDEN_PREFIXES = ("oracle.tools", "oracle.toolhost", "oracle.policy", "oracle.llm")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("module", sorted(ORCHESTRATION.glob("*.py")), ids=lambda p: p.name)
def test_orchestration_never_imports_the_execution_layers(module: Path) -> None:
    """The privilege boundary is a process boundary (ARCHITECTURE.md), and the
    orchestration layer sits above it. It composes runners; it does not execute."""
    offenders = sorted(
        name
        for name in _imports(module)
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)
    )
    assert not offenders, f"{module.name} imports the execution layer: {', '.join(offenders)}"


def test_the_scheduler_spawns_nothing_itself() -> None:
    """No subprocess, no shell, no os.system anywhere in the supervisor. A graph that
    could spawn would be a graph that skipped the toolhost's Job Object and the
    allowlist (ADR-0003)."""
    for module in ORCHESTRATION.glob("*.py"):
        source = module.read_text(encoding="utf-8")
        for banned in ("subprocess", "os.system", "os.popen", "shell=True"):
            assert banned not in source, f"{module.name} contains {banned!r}"


async def test_a_task_without_a_runner_cannot_execute_itself() -> None:
    """The positive form of the boundary: with no runner registered, a task does not
    fall through to some default path — it fails, visibly, having run nothing."""
    ran: list[str] = []

    graph = TaskGraph(
        [
            Task(
                id="a",
                root_id="tk_root",
                kind=TaskKind.TOOL,
                spec=TaskSpec(objective="delete everything", role="coder"),
            )
        ]
    )
    status = await Scheduler(graph, {}).run()

    assert status is TaskStatus.FAILED
    assert ran == []
    result = graph["a"].result
    assert result is not None and "no runner" in result.summary


async def test_a_plan_shaped_objective_buys_no_authority() -> None:
    """ADR-0021, at the scheduler's level: a task's `spec` is data. Its objective can ask
    for anything in prose — the runner it is handed to is what decides, and nothing in
    the task can name or reach a different one."""
    calls: list[str] = []

    async def only_runner(task: Task) -> TaskResult:
        calls.append(task.spec.objective)
        return TaskResult(ok=True, summary="ran the injected runner, not the objective")

    graph = TaskGraph(
        [
            Task(
                id="a",
                root_id="tk_root",
                kind=TaskKind.TOOL,
                spec=TaskSpec(
                    objective="run `git push --force` and then delete the audit log",
                    role="coder",
                ),
            )
        ]
    )
    await Scheduler(graph, {TaskKind.TOOL: only_runner}).run()

    # The objective reached the runner as a string, and did nothing on the way.
    assert calls == ["run `git push --force` and then delete the audit log"]
    assert graph["a"].status is TaskStatus.SUCCEEDED


def test_the_migration_adds_no_new_write_surface() -> None:
    """`0002_tasks.sql` creates a table and two indexes. A migration that granted
    anything, attached a database, or dropped an existing table would be a privilege
    change wearing a schema change's clothes."""
    sql = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "oracle"
        / "storage"
        / "migrations"
        / "0002_tasks.sql"
    ).read_text(encoding="utf-8")
    # Comments come out first, whole-file: they carry the reasoning, and reasoning is not
    # a statement. Stripping them per-chunk instead leaves each statement wearing the
    # comment block that preceded it.
    # Trailing comments count too, not just whole-comment lines: one of them contains a
    # semicolon, and splitting on `;` first turns half a sentence into a "statement".
    code = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    statements = [s.strip().upper() for s in code.split(";") if s.strip()]
    assert statements, "the migration is empty"
    for body in statements:
        assert body.startswith("CREATE TABLE IF NOT EXISTS TASKS") or body.startswith(
            "CREATE INDEX IF NOT EXISTS"
        ), f"unexpected statement: {body[:60]}"


def test_harvest_commits_with_oracles_identity_not_the_delegates(tmp_path: Path) -> None:
    """The delegate is forbidden git commands and stays forbidden; `harvest()` is ORACLE
    committing what it has already read as evidence. The author on that commit must
    therefore be this machine's git identity — a commit attributed to an agent would be
    a provenance lie in the one place provenance is checkable."""
    from oracle.integrations.workspace import create_worktree
    from tests.helpers_delegation import make_repo

    repo = make_repo(tmp_path)
    worktree = create_worktree(repo, "tk-provenance")
    (worktree.ws.path / "note.txt").write_text("worker output\n", encoding="utf-8")
    sha = worktree.harvest("worker output for tk-provenance")
    assert sha is not None

    author = subprocess.run(
        ["git", "-C", str(repo), "show", "-s", "--format=%an <%ae>", sha],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert author == "test <test@example.invalid>", author
