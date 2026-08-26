"""The durable project entity (docs/PROJECT_STATE.md, ADR-0024).

What these pin, beyond the arithmetic, is the **split**: relational state is stored,
observed state is not. Several tests assert the *absence* of something — no branch column,
no cached dirty count — because the pressure to "just cache it, git is slow" arrives later
and a schema alone will not resist it.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from oracle.core.project_state import (
    FAILED_STATUSES,
    TERMINAL_STATUSES,
    DescriptionSource,
    ProjectNameTaken,
    ProjectStatus,
    ProjectStore,
    _cost_of,
    effective_status,
    observe,
)
from oracle.orchestration.models import TERMINAL, Task, TaskKind, TaskResult, TaskSpec, TaskStatus
from oracle.orchestration.store import TaskStore


@pytest.fixture
def store(conn: aiosqlite.Connection) -> ProjectStore:
    return ProjectStore(conn)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    d = tmp_path / "Asterim"
    d.mkdir()
    return d


# -- registration ---------------------------------------------------------------


async def test_registering_gives_a_row_with_an_id(store: ProjectStore, root: Path) -> None:
    project = await store.register("Asterim", root)
    assert project.id.startswith("pj_")
    assert project.name == "Asterim"
    assert project.status is ProjectStatus.IDLE
    assert await store.by_name("Asterim") == project


async def test_registering_twice_returns_the_same_row(store: ProjectStore, root: Path) -> None:
    """Idempotent by name, so a caller that registers on first use need not check first."""
    first = await store.register("Asterim", root)
    second = await store.register("Asterim", root)
    assert first.id == second.id
    assert len(await store.all()) == 1


async def test_a_root_that_does_not_exist_registers_as_missing(
    store: ProjectStore, tmp_path: Path
) -> None:
    """`MISSING` is information. Refusing to register would make a project that is
    temporarily on an unmounted drive unrepresentable."""
    project = await store.register("Ghost", tmp_path / "nope")
    assert project.status is ProjectStatus.MISSING


# -- identity -------------------------------------------------------------------


async def test_the_id_survives_a_rename(store: ProjectStore, root: Path) -> None:
    """The whole reason `id` is a separate column from `name`.

    Renaming a directory must not orphan the facts and attempts recorded against it, and
    those are keyed by the row, not by the label.
    """
    project = await store.register("Asterim", root)
    renamed = await store.rename(project.id, "Asterim2")

    assert renamed.id == project.id
    assert renamed.name == "Asterim2"
    assert await store.by_name("Asterim") is None
    assert (await store.get(project.id)) is not None


async def test_renaming_onto_a_taken_name_is_refused(store: ProjectStore, tmp_path: Path) -> None:
    """Two rows answering to one name would make classifier resolution ambiguous exactly
    where it must not be."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = await store.register("a", tmp_path / "a")
    await store.register("b", tmp_path / "b")

    with pytest.raises(ProjectNameTaken):
        await store.rename(a.id, "b")

    assert (await store.get(a.id)) is not None
    assert (await store.by_name("a")) is not None


async def test_renaming_to_its_own_name_is_not_a_clash(store: ProjectStore, root: Path) -> None:
    project = await store.register("Asterim", root)
    same = await store.rename(project.id, "Asterim")
    assert same.id == project.id


async def test_relocating_keeps_the_history(store: ProjectStore, tmp_path: Path) -> None:
    """A project that moved on disk is the same project. Re-registering under the new
    path would silently start a second history."""
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    project = await store.register("Asterim", old)
    moved = await store.relocate(project.id, new)

    assert moved.id == project.id
    assert moved.root == new
    assert moved.status is ProjectStatus.IDLE


# -- lifecycle ------------------------------------------------------------------


async def test_archiving_hides_but_does_not_delete(store: ProjectStore, root: Path) -> None:
    project = await store.register("Asterim", root)
    await store.archive(project.id)

    assert [p.name for p in await store.all()] == []
    assert [p.name for p in await store.all(include_archived=True)] == ["Asterim"]
    assert (await store.get(project.id)) is not None


async def test_touch_activates_an_idle_project(store: ProjectStore, root: Path) -> None:
    project = await store.register("Asterim", root)
    await store.touch(project.id)
    after = await store.get(project.id)

    assert after is not None
    assert after.status is ProjectStatus.ACTIVE
    assert after.last_touched is not None


async def test_touch_does_not_resurrect_an_archived_project(
    store: ProjectStore, root: Path
) -> None:
    """Archiving is a human's decision; a background task must not undo it."""
    project = await store.register("Asterim", root)
    await store.archive(project.id)
    await store.touch(project.id)
    after = await store.get(project.id)

    assert after is not None
    assert after.status is ProjectStatus.ARCHIVED


async def test_presence_is_reconciled_in_both_directions(
    store: ProjectStore, tmp_path: Path
) -> None:
    """A root deleted while the daemon was down becomes MISSING at boot, and comes back
    when the directory does — rather than staying wrong until someone restarts."""
    root = tmp_path / "Asterim"
    root.mkdir()
    project = await store.register("Asterim", root)

    root.rmdir()
    changed = await store.refresh_presence()
    assert [p.status for p in changed] == [ProjectStatus.MISSING]

    root.mkdir()
    changed = await store.refresh_presence()
    assert [p.status for p in changed] == [ProjectStatus.IDLE]

    final = await store.get(project.id)
    assert final is not None and final.id == project.id


async def test_refresh_presence_leaves_archived_rows_alone(
    store: ProjectStore, tmp_path: Path
) -> None:
    """An archived project whose directory is gone is not news."""
    root = tmp_path / "Old"
    root.mkdir()
    project = await store.register("Old", root)
    await store.archive(project.id)
    root.rmdir()

    assert await store.refresh_presence() == []
    after = await store.get(project.id)
    assert after is not None and after.status is ProjectStatus.ARCHIVED


# -- the briefing pointer -------------------------------------------------------


async def test_the_briefing_pointer_only_moves_forward(store: ProjectStore, root: Path) -> None:
    """Monotonic, so a late acknowledgement carrying a lower sequence cannot rewind a
    pointer a later one already advanced — which would re-brief work already seen."""
    project = await store.register("Asterim", root)
    await store.acknowledge_briefing(project.id, 120)
    await store.acknowledge_briefing(project.id, 40)
    after = await store.get(project.id)

    assert after is not None
    assert after.briefed_through_seq == 120


async def test_the_briefing_pointer_starts_at_zero(store: ProjectStore, root: Path) -> None:
    """A new project's whole history is unbriefed, which is the honest starting point."""
    project = await store.register("Asterim", root)
    assert project.briefed_through_seq == 0


# -- counters -------------------------------------------------------------------


def _task(task_id: str, project: str, status: TaskStatus, *, cost: dict | None = None) -> Task:
    result = (
        TaskResult.model_validate({"ok": status is TaskStatus.SUCCEEDED, "cost": cost})
        if cost is not None
        else None
    )
    return Task(
        id=task_id,
        root_id="tk_root",
        kind=TaskKind.TOOL,
        status=status,
        spec=TaskSpec(objective="do a thing", role="coder", project=project),
        result=result,
    )


async def test_counters_are_rebuilt_from_the_task_table(
    conn: aiosqlite.Connection, store: ProjectStore, root: Path
) -> None:
    project = await store.register("Asterim", root)
    await TaskStore(conn).save_all(
        [
            _task("tk_1", "Asterim", TaskStatus.RUNNING),
            _task("tk_2", "Asterim", TaskStatus.READY),
            _task("tk_3", "Asterim", TaskStatus.FAILED, cost={"tokens": 100, "usd": 0.25}),
            _task("tk_4", "Asterim", TaskStatus.TIMEOUT),
            _task("tk_5", "Asterim", TaskStatus.SUCCEEDED, cost={"tokens": 40, "usd": 0.1}),
            # A different project's task must not land in this project's counters.
            _task("tk_6", "GameRecs", TaskStatus.RUNNING),
        ]
    )

    counted = await store.recount(project.id)
    assert counted.open_tasks == 2
    assert counted.failed_tasks == 2
    assert counted.tokens_spent == 140
    assert counted.usd_spent == pytest.approx(0.35)


async def test_recount_is_idempotent_and_authoritative(
    conn: aiosqlite.Connection, store: ProjectStore, root: Path
) -> None:
    """The stored counter is a projection. Recompute is always right, so a corrupted
    counter is repaired by running this rather than by trusting it."""
    project = await store.register("Asterim", root)
    await TaskStore(conn).save_all([_task("tk_1", "Asterim", TaskStatus.RUNNING)])
    await store.recount(project.id)

    await conn.execute("UPDATE projects SET open_tasks = 99 WHERE id = ?", (project.id,))
    await conn.commit()

    repaired = await store.recount(project.id)
    assert repaired.open_tasks == 1
    assert (await store.recount(project.id)).open_tasks == 1


async def test_a_project_with_no_tasks_counts_zero(store: ProjectStore, root: Path) -> None:
    project = await store.register("Asterim", root)
    counted = await store.recount(project.id)
    assert (counted.open_tasks, counted.failed_tasks, counted.tokens_spent) == (0, 0, 0)


async def test_the_project_column_is_generated_from_spec(
    conn: aiosqlite.Connection, store: ProjectStore, root: Path
) -> None:
    """The index column *is* `spec`, not a second copy of it — so it cannot drift from
    the task row, and nothing on the write path has to remember to keep it in step."""
    await store.register("Asterim", root)
    await TaskStore(conn).save_all([_task("tk_1", "Asterim", TaskStatus.RUNNING)])

    async with conn.execute("SELECT project FROM tasks WHERE id = 'tk_1'") as cur:
        row = await cur.fetchone()
    assert row is not None and row["project"] == "Asterim"


async def test_a_malformed_result_does_not_break_a_rebuild(
    conn: aiosqlite.Connection, store: ProjectStore, root: Path
) -> None:
    """One unreadable row from an old schema must not make the whole briefing
    unavailable — this runs over every task a project ever had."""
    project = await store.register("Asterim", root)
    await TaskStore(conn).save_all([_task("tk_1", "Asterim", TaskStatus.SUCCEEDED)])
    await conn.execute("UPDATE tasks SET result = 'not json' WHERE id = 'tk_1'")
    await conn.commit()

    counted = await store.recount(project.id)
    assert counted.tokens_spent == 0


@pytest.mark.parametrize(
    "raw",
    [None, "", "not json", "[]", '{"cost": null}', '{"cost": "free"}', '{"cost": {}}'],
)
def test_cost_extraction_is_total(raw: str | None) -> None:
    assert _cost_of(raw) == (0, 0.0)


def test_cost_extraction_reads_a_real_result() -> None:
    raw = json.dumps({"ok": True, "cost": {"tokens": 7, "usd": 1.5}})
    assert _cost_of(raw) == (7, 1.5)


# -- the vocabulary must not drift ----------------------------------------------


def test_terminal_set_matches_orchestration() -> None:
    """`TERMINAL_STATUSES` is duplicated here rather than imported, because dependencies
    point downward and this module must not reach up into the supervisor. The duplication
    is only safe if a test proves it cannot drift."""
    assert TERMINAL_STATUSES == {str(s) for s in TERMINAL}


def test_failed_statuses_are_a_subset_of_terminal() -> None:
    assert FAILED_STATUSES < TERMINAL_STATUSES


def test_timeout_is_still_not_failed_in_the_task_table() -> None:
    """The project counter folds them together on purpose — both mean "a person is
    needed" — but the underlying vocabulary must keep them apart, because a timed-out
    worker may well have done the work."""
    assert TaskStatus.TIMEOUT is not TaskStatus.FAILED


# -- observed state is NOT stored -----------------------------------------------


async def test_the_projects_table_stores_nothing_git_owns(conn: aiosqlite.Connection) -> None:
    """The load-bearing assertion of this whole subsystem (PROJECT_STATE.md §2).

    A cached branch name is wrong the moment someone switches branches in their editor,
    silently, with no event that could correct it. If a column named below ever appears
    here, the design has been inverted and the sidebar has started lying.
    """
    async with conn.execute("PRAGMA table_info(projects)") as cur:
        columns = {row["name"] for row in await cur.fetchall()}

    forbidden = {
        "branch",
        "upstream",
        "ahead",
        "behind",
        "dirty",
        "clean",
        "last_commit",
        "test_command",
        "build_command",
        "kinds",
        "file_count",
    }
    assert not (columns & forbidden), f"observed state must not be persisted: {columns & forbidden}"


class _FakeOutcome:
    def __init__(self, ok: bool, result: object = None, message: str = "") -> None:
        self.ok = ok
        self.result = result
        self.error = None if ok else type("E", (), {"message": message})()


class _FakeExecutor:
    """Records what was asked of the tool layer. `observe()` must reach git through
    contracts and never around them."""

    def __init__(self, outcomes: dict[str, _FakeOutcome]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, tool_id: str, args: dict, **_: object) -> _FakeOutcome:
        self.calls.append((tool_id, args))
        return self.outcomes.get(tool_id, _FakeOutcome(False, message="not stubbed"))


def _status_result(**over: object) -> object:
    base = {
        "branch": "main",
        "upstream": "origin/main",
        "ahead": 3,
        "behind": 0,
        "staged": ["a"],
        "unstaged": ["b", "c"],
        "untracked": [],
        "conflicted": [],
        "clean": False,
    }
    base.update(over)
    return type("R", (), base)()


async def test_observe_reads_git_through_the_tool_layer(store: ProjectStore, root: Path) -> None:
    project = await store.register("Asterim", root)
    head = type(
        "C", (), {"short": "abc1234", "subject": "fix auth", "date": "2026-08-26T10:00:00Z"}
    )()
    executor = _FakeExecutor(
        {
            "git.status": _FakeOutcome(True, _status_result()),
            "git.log": _FakeOutcome(True, type("L", (), {"commits": [head]})()),
        }
    )

    obs = await observe(executor, project)  # type: ignore[arg-type]

    assert [tool for tool, _ in executor.calls] == ["git.status", "git.log"]
    assert obs.branch == "main"
    assert obs.ahead == 3
    assert obs.dirty == 3
    assert obs.clean is False
    assert obs.last_commit == ("abc1234", "fix auth", "2026-08-26T10:00:00Z")
    assert obs.error is None
    assert obs.is_repo


async def test_observe_reports_a_missing_root_as_a_field(
    store: ProjectStore, tmp_path: Path
) -> None:
    """`MISSING` in the sidebar is information; a crashed sidebar is not."""
    project = await store.register("Ghost", tmp_path / "nope")
    executor = _FakeExecutor({})

    obs = await observe(executor, project)  # type: ignore[arg-type]

    assert obs.error == "root does not exist"
    assert executor.calls == [], "a project with no directory must not spawn git"
    assert not obs.is_repo


async def test_observe_reports_a_non_repository_as_a_field(store: ProjectStore, root: Path) -> None:
    """A directory that is not a repository is the common case, not a failure of
    ORACLE's, and it must still render."""
    project = await store.register("Asterim", root)
    executor = _FakeExecutor({"git.status": _FakeOutcome(False, message="not a git repository")})

    obs = await observe(executor, project)  # type: ignore[arg-type]

    assert obs.error == "not a git repository"
    assert obs.branch is None
    assert obs.detected is not None, "classification still works without git"


async def test_observe_survives_a_repository_with_no_commits(
    store: ProjectStore, root: Path
) -> None:
    """An empty repository has no HEAD. That is a state, not a failure."""
    project = await store.register("Asterim", root)
    executor = _FakeExecutor(
        {
            "git.status": _FakeOutcome(True, _status_result(clean=True, staged=[], unstaged=[])),
            "git.log": _FakeOutcome(True, type("L", (), {"commits": []})()),
        }
    )

    obs = await observe(executor, project)  # type: ignore[arg-type]

    assert obs.last_commit is None
    assert obs.branch == "main"
    assert obs.error is None


async def test_description_provenance_is_recorded(store: ProjectStore, root: Path) -> None:
    """A description ORACLE derived from a README carries that README's taint. A reader
    must be able to tell it from one a person wrote without guessing."""
    project = await store.register(
        "Asterim", root, description="a game", description_source=DescriptionSource.DERIVED
    )
    assert project.description_source is DescriptionSource.DERIVED
    stored = await store.by_name("Asterim")
    assert stored is not None and stored.description_source is DescriptionSource.DERIVED


# -- existence is observed, not remembered --------------------------------------


async def test_effective_status_corrects_a_stale_row(store: ProjectStore, tmp_path: Path) -> None:
    """`refresh_presence()` reconciles at boot. A directory deleted *while the daemon
    runs* must not leave a surface saying `idle` about something that is gone."""
    root = tmp_path / "Asterim"
    root.mkdir()
    project = await store.register("Asterim", root)
    assert effective_status(project) is ProjectStatus.IDLE

    root.rmdir()
    assert effective_status(project) is ProjectStatus.MISSING

    root.mkdir()
    assert effective_status(project) is ProjectStatus.IDLE


async def test_effective_status_never_overrides_archived(
    store: ProjectStore, tmp_path: Path
) -> None:
    """A project deliberately set aside is archived whether or not its directory
    survives. Reporting it as missing would invite someone to "fix" it."""
    root = tmp_path / "Old"
    root.mkdir()
    project = await store.register("Old", root)
    archived = await store.archive(project.id)
    root.rmdir()

    assert effective_status(archived) is ProjectStatus.ARCHIVED


async def test_effective_status_preserves_active(store: ProjectStore, root: Path) -> None:
    project = await store.register("Asterim", root)
    await store.touch(project.id)
    active = await store.get(project.id)
    assert active is not None
    assert effective_status(active) is ProjectStatus.ACTIVE


async def test_the_counter_rebuild_uses_the_index(
    conn: aiosqlite.Connection, store: ProjectStore, root: Path
) -> None:
    """PROJECT_STATE.md claims the index "is what makes the counters rebuildable cheaply".

    Unverified, that is a sentence. A generated column is only indexable if the query uses
    the same expression the index was built on, so a future edit that filtered on
    `json_extract(spec, ...)` by hand — or dropped the index — would silently turn every
    briefing render into a full scan of the task table.
    """
    await store.register("Asterim", root)
    async with conn.execute(
        "EXPLAIN QUERY PLAN SELECT status, result FROM tasks WHERE project = ?", ("Asterim",)
    ) as cur:
        plan = " ".join(str(row["detail"]) for row in await cur.fetchall())

    assert "ix_tasks_project" in plan, plan
    assert "SCAN" not in plan, plan


async def test_generated_columns_are_hidden_from_table_info(
    conn: aiosqlite.Connection,
) -> None:
    """A trap worth pinning rather than rediscovering.

    `PRAGMA table_info` omits generated columns entirely — only `table_xinfo` lists them,
    with `hidden=2` for VIRTUAL. Anything that introspects the schema to decide whether a
    migration applied will conclude, wrongly, that it did not.
    """
    async with conn.execute("PRAGMA table_info(tasks)") as cur:
        plain = {row["name"] for row in await cur.fetchall()}
    async with conn.execute("PRAGMA table_xinfo(tasks)") as cur:
        extended = {row["name"]: row["hidden"] for row in await cur.fetchall()}

    assert "project" not in plain
    assert extended["project"] == 2
