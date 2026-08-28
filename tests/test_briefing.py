"""The briefing (docs/PROJECT_STATE.md §6, UI.md §7b).

Four rules carry the design, and each has a test here that would fail if somebody
"simplified" it later:

  * the watermark advances on **acknowledgement**, never on render;
  * `waiting` is **current state**, not a delta — acknowledging must never bury a block;
  * **no model** is on this path;
  * **empty is a real state**, not a placeholder and never a fabricated summary.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from oracle.core.briefing import (
    MAX_HIGHLIGHTS,
    SYSTEM_SEQ_KEY,
    Briefing,
    ProjectBrief,
    SystemBrief,
    TaskLine,
    acknowledge,
    build,
    get_meta,
    render,
    set_meta,
    wire,
)
from oracle.core.eventlog import EventLog
from oracle.core.events import Event
from oracle.core.project_state import Project, ProjectStore
from oracle.orchestration.models import Task, TaskKind, TaskResult, TaskSpec, TaskStatus
from oracle.orchestration.store import TaskStore


@pytest.fixture
def projects(conn: aiosqlite.Connection) -> ProjectStore:
    return ProjectStore(conn)


@pytest.fixture
def tasks(conn: aiosqlite.Connection) -> TaskStore:
    return TaskStore(conn)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    d = tmp_path / "Asterim"
    d.mkdir()
    return d


def _task(
    task_id: str,
    project: str,
    status: TaskStatus,
    *,
    objective: str = "do a thing",
    cost: dict | None = None,
    error: str | None = None,
    started: str | None = "2026-08-26T10:00:00.000Z",
    finished: str | None = "2026-08-26T10:02:00.000Z",
) -> Task:
    payload: dict = {"ok": status is TaskStatus.SUCCEEDED}
    if cost is not None:
        payload["cost"] = cost
    if error is not None:
        payload["error"] = {"kind": "failed", "message": error}
    return Task(
        id=task_id,
        root_id="tk_root",
        kind=TaskKind.TOOL,
        status=status,
        spec=TaskSpec(objective=objective, role="coder", project=project),
        started_at=started,
        finished_at=finished,
        result=TaskResult.model_validate(payload) if len(payload) > 1 else None,
    )


async def _touch(log: EventLog, task_id: str) -> int:
    """One `task.updated` for a task, which is what puts it inside a delta."""
    ev = await log.append(
        Event(type="task.updated", task_id=task_id, trace_id="t", actor="system", payload={})
    )
    return ev.seq


async def _setup(
    conn: aiosqlite.Connection, projects: ProjectStore, root: Path
) -> tuple[EventLog, Project]:
    log = EventLog(conn)
    await log.load_head()
    project = await projects.register("Asterim", root)
    return log, project


# -- the delta ------------------------------------------------------------------


async def test_a_finished_task_appears(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.SUCCEEDED)])
    await _touch(log, "tk_1")

    briefing = await build(conn, projects, through_seq=log.last_seq)

    assert [b.project for b in briefing.projects] == ["Asterim"]
    assert briefing.projects[0].completed == 1
    assert briefing.projects[0].failed == 0


async def test_work_below_the_watermark_is_not_repeated(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """The whole point of the pointer. A briefing that keeps showing yesterday is one
    nobody reads."""
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.SUCCEEDED)])
    seq = await _touch(log, "tk_1")

    await acknowledge(conn, projects, through_seq=seq)

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert briefing.projects == ()
    assert briefing.empty


async def test_new_work_after_an_acknowledgement_appears(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.SUCCEEDED)])
    await acknowledge(conn, projects, through_seq=await _touch(log, "tk_1"))

    await tasks.save_all([_task("tk_2", "Asterim", TaskStatus.FAILED, error="boom")])
    await _touch(log, "tk_2")

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert briefing.projects[0].failed == 1
    assert briefing.projects[0].completed == 0


async def test_another_projects_work_is_not_borrowed(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, tmp_path: Path
) -> None:
    log = EventLog(conn)
    await log.load_head()
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    await projects.register("A", tmp_path / "A")
    await projects.register("B", tmp_path / "B")
    await tasks.save_all([_task("tk_1", "A", TaskStatus.SUCCEEDED)])
    await _touch(log, "tk_1")

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert [b.project for b in briefing.projects] == ["A"]


# -- rule 1: rendering does not consume ------------------------------------------


async def test_rendering_does_not_advance_the_watermark(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """**The load-bearing rule.** Glance at the screen, walk away, come back — it is
    still there. A briefing that clears itself on sight is a notification, and
    notifications are how people miss things."""
    log, project = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.SUCCEEDED)])
    await _touch(log, "tk_1")

    first = await build(conn, projects, through_seq=log.last_seq)
    second = await build(conn, projects, through_seq=log.last_seq)

    assert first.projects[0].completed == second.projects[0].completed == 1
    after = await projects.get(project.id)
    assert after is not None and after.briefed_through_seq == 0


async def test_an_unacknowledged_briefing_survives_a_restart(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """The watermark is a column, not a process variable. Rebuilding every collaborator
    is the closest a test gets to a daemon restart against the same database."""
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.SUCCEEDED)])
    await _touch(log, "tk_1")
    await build(conn, projects, through_seq=log.last_seq)

    reborn = ProjectStore(conn)
    briefing = await build(conn, reborn, through_seq=log.last_seq)
    assert briefing.projects[0].completed == 1


async def test_acknowledging_one_project_leaves_the_others(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, tmp_path: Path
) -> None:
    log = EventLog(conn)
    await log.load_head()
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    a = await projects.register("A", tmp_path / "A")
    await projects.register("B", tmp_path / "B")
    await tasks.save_all(
        [_task("tk_a", "A", TaskStatus.SUCCEEDED), _task("tk_b", "B", TaskStatus.SUCCEEDED)]
    )
    await _touch(log, "tk_a")
    await _touch(log, "tk_b")

    await acknowledge(conn, projects, through_seq=log.last_seq, project_id=a.id)

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert [b.project for b in briefing.projects] == ["B"]


async def test_a_stale_acknowledgement_cannot_rewind(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """A slow client returning with an old `through_seq` must not re-show work a later
    acknowledgement already covered."""
    log, project = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.SUCCEEDED)])
    await _touch(log, "tk_1")

    await acknowledge(conn, projects, through_seq=log.last_seq)
    await acknowledge(conn, projects, through_seq=1)

    after = await projects.get(project.id)
    assert after is not None and after.briefed_through_seq == log.last_seq


# -- rule 2: waiting is current, not a delta -------------------------------------


async def test_a_waiting_task_appears_even_below_the_watermark(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """**Acknowledging must never bury a block.**

    Everything else here answers "what changed since seq N". A task parked on an approval
    does not: it is the thing that most needs a person, and if it vanished because they
    dismissed a briefing once, the feature would be actively harmful.
    """
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.WAITING, finished=None)])
    await _touch(log, "tk_1")
    await acknowledge(conn, projects, through_seq=log.last_seq)

    briefing = await build(conn, projects, through_seq=log.last_seq)

    assert briefing.projects[0].waiting == 1
    assert briefing.projects[0].needs_you
    assert not briefing.empty


async def test_a_waiting_task_with_no_events_at_all_still_appears(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """It is current state, so it does not need an event to have carried it."""
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.WAITING, finished=None)])

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert briefing.projects[0].waiting == 1


async def test_projects_needing_attention_sort_first(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, tmp_path: Path
) -> None:
    log = EventLog(conn)
    await log.load_head()
    for name in ("A", "B", "C"):
        (tmp_path / name).mkdir()
        await projects.register(name, tmp_path / name)
    await tasks.save_all(
        [
            _task("tk_a", "A", TaskStatus.SUCCEEDED),
            _task("tk_b", "B", TaskStatus.WAITING, finished=None),
            _task("tk_c", "C", TaskStatus.FAILED, error="x"),
        ]
    )
    for task_id in ("tk_a", "tk_b", "tk_c"):
        await _touch(log, task_id)

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert [b.project for b in briefing.projects] == ["B", "C", "A"]


async def test_waiting_lines_sort_above_failures_within_a_project(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all(
        [
            _task("tk_ok", "Asterim", TaskStatus.SUCCEEDED, objective="ok"),
            _task("tk_bad", "Asterim", TaskStatus.FAILED, objective="bad", error="x"),
            _task("tk_wait", "Asterim", TaskStatus.WAITING, objective="wait", finished=None),
        ]
    )
    for task_id in ("tk_ok", "tk_bad", "tk_wait"):
        await _touch(log, task_id)

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert [line.objective for line in briefing.projects[0].highlights][:2] == ["wait", "bad"]


# -- rule 4: empty is a real state ------------------------------------------------


async def test_nothing_at_all_is_an_honest_answer(
    conn: aiosqlite.Connection, projects: ProjectStore, root: Path
) -> None:
    log, _ = await _setup(conn, projects, root)
    briefing = await build(conn, projects, through_seq=log.last_seq)

    assert briefing.empty
    assert briefing.projects == ()
    assert render(briefing).startswith("Nothing ran")


def test_an_empty_briefing_renders_no_fabrication() -> None:
    text = render(Briefing(through_seq=0))
    assert text == "Nothing ran yet."


# -- bounds ----------------------------------------------------------------------


async def test_the_highlight_list_is_capped_and_says_so(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """Away for a week is not forty lines. Silent truncation would read as "this is
    everything"."""
    log, _ = await _setup(conn, projects, root)
    made = [_task(f"tk_{i}", "Asterim", TaskStatus.SUCCEEDED) for i in range(12)]
    await tasks.save_all(made)
    for task in made:
        await _touch(log, task.id)

    briefing = await build(conn, projects, through_seq=log.last_seq)
    brief = briefing.projects[0]

    assert len(brief.highlights) == MAX_HIGHLIGHTS
    assert brief.more == 12 - MAX_HIGHLIGHTS
    assert brief.completed == 12, "counts are not truncated, only the named lines are"
    assert f"and {brief.more} more" in render(briefing)


async def test_work_above_through_seq_is_excluded(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """`through_seq` is pinned by the caller so that work arriving mid-render is not
    marked as seen when the reader acknowledges what they actually saw."""
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.SUCCEEDED)])
    pinned = await _touch(log, "tk_1")

    await tasks.save_all([_task("tk_2", "Asterim", TaskStatus.FAILED, error="later")])
    await _touch(log, "tk_2")

    briefing = await build(conn, projects, through_seq=pinned)
    assert briefing.projects[0].completed == 1
    assert briefing.projects[0].failed == 0


# -- cost and timings -------------------------------------------------------------


async def test_cost_and_elapsed_are_summed(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all(
        [
            _task("tk_1", "Asterim", TaskStatus.SUCCEEDED, cost={"tokens": 100, "usd": 0.25}),
            _task("tk_2", "Asterim", TaskStatus.SUCCEEDED, cost={"tokens": 40, "usd": 0.1}),
        ]
    )
    await _touch(log, "tk_1")
    await _touch(log, "tk_2")

    brief = (await build(conn, projects, through_seq=log.last_seq)).projects[0]
    assert brief.tokens == 140
    assert brief.usd == pytest.approx(0.35)
    assert brief.elapsed_s == pytest.approx(240.0)


async def test_a_task_with_no_timestamps_contributes_zero(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """A wrong duration is a nuisance; an exception on the glance surface is an outage."""
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all(
        [_task("tk_1", "Asterim", TaskStatus.RUNNING, started=None, finished=None)]
    )
    await _touch(log, "tk_1")

    brief = (await build(conn, projects, through_seq=log.last_seq)).projects[0]
    assert brief.elapsed_s == 0.0
    assert brief.in_flight == 1, "a running task is news even before it has an outcome"


async def test_a_malformed_result_does_not_break_the_briefing(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.SUCCEEDED)])
    await conn.execute("UPDATE tasks SET result = 'not json' WHERE id = 'tk_1'")
    await conn.commit()
    await _touch(log, "tk_1")

    brief = (await build(conn, projects, through_seq=log.last_seq)).projects[0]
    assert brief.completed == 1
    assert brief.tokens == 0


# -- the system section -----------------------------------------------------------


async def test_a_clean_restart_is_reported_as_clean(
    conn: aiosqlite.Connection, projects: ProjectStore, root: Path
) -> None:
    log, _ = await _setup(conn, projects, root)
    await log.append(
        Event(
            type="system.boot",
            trace_id="t",
            actor="system",
            payload={"unclean": False, "last_event": "system.shutdown"},
        )
    )

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert briefing.system.restarted_at is not None
    assert briefing.system.unclean is False
    assert "restarted at" in render(briefing)
    assert "unexpectedly" not in render(briefing)


async def test_a_dead_daemon_briefs_itself(
    conn: aiosqlite.Connection, projects: ProjectStore, root: Path
) -> None:
    """ADR-0025's named risk is a background service failing invisibly at 04:00. This is
    the mitigation: the boot event carries whether the previous run ended cleanly, so the
    briefing can say so instead of showing a silent gap that looks like an idle night."""
    log, _ = await _setup(conn, projects, root)
    await log.append(
        Event(
            type="system.boot",
            trace_id="t",
            actor="system",
            payload={"unclean": True, "last_event": "tool.finished"},
        )
    )

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert briefing.system.unclean
    assert not briefing.empty
    assert "stopped unexpectedly" in render(briefing)


async def test_degradations_are_named_not_counted(
    conn: aiosqlite.Connection, projects: ProjectStore, root: Path
) -> None:
    """ "1 degradation" tells nobody whether to act. The reason does."""
    log, _ = await _setup(conn, projects, root)
    for _ in range(3):
        await log.append(
            Event(
                type="system.degraded",
                trace_id="t",
                actor="system",
                payload={"reason": "Ollama is not reachable"},
            )
        )

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert briefing.system.degraded == ("Ollama is not reachable",), "repeats collapse"
    assert "Ollama is not reachable" in render(briefing)


async def test_the_system_section_has_its_own_watermark(
    conn: aiosqlite.Connection, projects: ProjectStore, root: Path
) -> None:
    """A daemon restart belongs to no project, so without a watermark of its own it would
    reappear in every briefing forever — the notification a person learns to skip."""
    log, _ = await _setup(conn, projects, root)
    await log.append(
        Event(type="system.boot", trace_id="t", actor="system", payload={"unclean": True})
    )

    assert (await build(conn, projects, through_seq=log.last_seq)).system.unclean
    await acknowledge(conn, projects, through_seq=log.last_seq)
    assert (await build(conn, projects, through_seq=log.last_seq)).system.empty

    assert await get_meta(conn, SYSTEM_SEQ_KEY) == str(log.last_seq)


async def test_acknowledging_one_project_does_not_clear_the_system_section(
    conn: aiosqlite.Connection, projects: ProjectStore, root: Path
) -> None:
    """A per-project dismissal is about that project. Sweeping up the daemon's own news
    with it is how a crash gets lost."""
    log, project = await _setup(conn, projects, root)
    await log.append(
        Event(type="system.boot", trace_id="t", actor="system", payload={"unclean": True})
    )

    await acknowledge(conn, projects, through_seq=log.last_seq, project_id=project.id)
    assert (await build(conn, projects, through_seq=log.last_seq)).system.unclean


async def test_meta_round_trips_and_overwrites(conn: aiosqlite.Connection) -> None:
    assert await get_meta(conn, "absent", "fallback") == "fallback"
    await set_meta(conn, "k", "1")
    await set_meta(conn, "k", "2")
    assert await get_meta(conn, "k") == "2"


# -- archived projects ------------------------------------------------------------


async def test_an_archived_project_is_not_briefed(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """Archiving is a human saying "not now". The briefing is the surface that must
    respect it most, because it is the one that demands attention."""
    log, project = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.WAITING, finished=None)])
    await _touch(log, "tk_1")
    await projects.archive(project.id)

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert briefing.projects == ()


async def test_a_deleted_root_still_renders_its_line(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, tmp_path: Path
) -> None:
    """One absent path degrades one row, not the surface."""
    log = EventLog(conn)
    await log.load_head()
    gone = tmp_path / "Gone"
    gone.mkdir()
    await projects.register("Gone", gone)
    await tasks.save_all([_task("tk_1", "Gone", TaskStatus.FAILED, error="x")])
    await _touch(log, "tk_1")
    gone.rmdir()

    briefing = await build(conn, projects, through_seq=log.last_seq)
    assert [b.project for b in briefing.projects] == ["Gone"]
    assert briefing.projects[0].failed == 1


# -- rendering and the wire shape --------------------------------------------------


def test_the_renderer_is_pure_arithmetic() -> None:
    """No model, no I/O, no clock. `render` takes a value and returns a string, which is
    what makes the glance surface both fast and incapable of inventing anything."""
    briefing = Briefing(
        through_seq=9,
        since_ts="2026-08-26T18:04:00.000Z",
        projects=(
            ProjectBrief(
                project="Asterim",
                status="active",
                completed=3,
                failed=1,
                waiting=1,
                elapsed_s=2280.0,
                usd=0.42,
                highlights=(
                    TaskLine(id="tk_1", objective="fix the timeout", status="waiting"),
                    TaskLine(
                        id="tk_2", objective="regression tests", status="failed", error="3 failing"
                    ),
                ),
            ),
        ),
        system=SystemBrief(degraded=("Ollama is not reachable",)),
    )
    text = render(briefing)

    assert "Asterim" in text
    assert "1 waiting on you" in text
    assert "1 failed" in text
    assert "3 completed" in text
    assert "38m" in text
    assert "$0.42" in text
    assert "fix the timeout" in text
    assert "regression tests — 3 failing" in text
    assert "Ollama is not reachable" in text


def test_the_wire_shape_never_omits_a_key() -> None:
    """A client must never have to read a missing key as a value."""
    body = wire(Briefing(through_seq=0))

    assert set(body) == {"through_seq", "since_ts", "empty", "text", "projects", "system"}
    assert set(body["system"]) == {"restarted_at", "unclean", "degraded", "errors"}
    assert body["projects"] == []
    assert body["empty"] is True


async def test_the_wire_shape_carries_every_project_field(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all([_task("tk_1", "Asterim", TaskStatus.SUCCEEDED, cost={"tokens": 5})])
    await _touch(log, "tk_1")

    body = wire(await build(conn, projects, through_seq=log.last_seq))
    row = body["projects"][0]

    assert {
        "project",
        "status",
        "completed",
        "failed",
        "waiting",
        "in_flight",
        "cancelled",
        "elapsed_s",
        "tokens",
        "usd",
        "needs_you",
        "more",
        "highlights",
    } == set(row)
    assert json.dumps(body), "the whole payload must be JSON-serialisable"


async def test_a_run_in_progress_is_reported(
    conn: aiosqlite.Connection, projects: ProjectStore, tasks: TaskStore, root: Path
) -> None:
    """ "What is running now" is one of the six things VISION.md §2 gives the screen three
    to five seconds to answer. A briefing that counted only outcomes would go blank in the
    middle of a long run — precisely when a person most wants to see something."""
    log, _ = await _setup(conn, projects, root)
    await tasks.save_all(
        [
            _task("tk_1", "Asterim", TaskStatus.RUNNING, finished=None),
            _task("tk_2", "Asterim", TaskStatus.PENDING, started=None, finished=None),
        ]
    )
    await _touch(log, "tk_1")
    await _touch(log, "tk_2")

    brief = (await build(conn, projects, through_seq=log.last_seq)).projects[0]
    assert brief.in_flight == 2
    assert not brief.empty
    assert "2 running" in render(await build(conn, projects, through_seq=log.last_seq))
