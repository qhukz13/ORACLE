"""Crash recovery: what a supervisor may assume about work it did not see finish.

The answer is nothing, and these tests are mostly about *not* doing things. The rule
ported from `asterim-pipeline` — never auto-restart an interrupted agent — is worth a
test precisely because the tempting behaviour (pick up where we left off) is one line
away and looks like a feature.

The crash is simulated at the store, not by killing a process: rows that say `RUNNING`
with no live supervisor **are** the post-crash state, and testing the rules against that
state is both deterministic and the thing that actually matters. A real kill is a test of
Windows process semantics, not of these rules.
"""

from __future__ import annotations

import aiosqlite

from oracle.core.eventlog import EventLog
from oracle.orchestration.graph import TaskGraph
from oracle.orchestration.models import (
    Task,
    TaskKind,
    TaskResult,
    TaskSpec,
    TaskStatus,
)
from oracle.orchestration.recovery import INTERRUPTED, recover
from oracle.orchestration.scheduler import Scheduler
from oracle.orchestration.store import TaskStore

ROOT = "tk_root"


def task(task_id: str, *deps: str, kind: TaskKind = TaskKind.DELEGATION) -> Task:
    return Task(
        id=task_id,
        root_id=ROOT,
        kind=kind,
        spec=TaskSpec(objective=f"do {task_id}", role="coder"),
        depends_on=tuple(deps),
    )


async def crashed_graph(store: TaskStore) -> None:
    """The state a daemon leaves behind when it dies mid-graph: one task finished, one
    still marked RUNNING because nothing got to write its ending, two never started."""
    await store.save_all(
        [
            task("a", kind=TaskKind.TOOL).with_status(
                TaskStatus.SUCCEEDED, result=TaskResult(ok=True, summary="done")
            ),
            task("b", "a").with_status(TaskStatus.RUNNING),
            task("c", "b"),
            task("d", "a"),
        ]
    )


async def test_an_interrupted_task_is_failed_and_never_restarted(
    conn: aiosqlite.Connection,
) -> None:
    """A supervisor that cannot prove what a child did while it was dead does not get to
    assume it did nothing — so the task is `FAILED(interrupted)`, and the error says which
    kind of failure it was."""
    store = TaskStore(conn)
    await crashed_graph(store)

    found = await recover(store)

    assert [t.id for t in found.interrupted] == ["b"]
    assert found.gated

    reloaded = await store.load("b")
    assert reloaded is not None
    assert reloaded.status is TaskStatus.FAILED
    assert reloaded.result is not None and reloaded.result.error is not None
    assert reloaded.result.error.kind == INTERRUPTED
    assert reloaded.result.error.retryable is False, "an interrupted task must not auto-retry"
    assert reloaded.result.ok is False


async def test_tasks_that_never_started_are_left_exactly_as_they_are(
    conn: aiosqlite.Connection,
) -> None:
    """Nothing ran, so there is nothing to distrust. Reporting them is what lets a person
    see a half-finished graph rather than a quiet one."""
    store = TaskStore(conn)
    await crashed_graph(store)

    found = await recover(store)

    assert sorted(t.id for t in found.pending) == ["c", "d"]
    for task_id in ("c", "d"):
        reloaded = await store.load(task_id)
        assert reloaded is not None and reloaded.status is TaskStatus.PENDING


async def test_a_finished_task_is_not_touched(conn: aiosqlite.Connection) -> None:
    store = TaskStore(conn)
    await crashed_graph(store)
    before = await store.load("a")

    await recover(store)

    after = await store.load("a")
    assert after == before, "recovery rewrote a task that had already finished"


async def test_recovery_announces_itself_on_a_critical_event(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """A recovery that quietly tidies up is a recovery nobody audits. `system.degraded`
    is in `CRITICAL_TYPES`, so it survives backpressure."""
    store = TaskStore(conn)
    await crashed_graph(store)

    await recover(store, eventlog)

    events = await eventlog.read_range(0, eventlog.last_seq, 100)
    degraded = [e for e in events if e.type == "system.degraded"]
    assert len(degraded) == 1
    payload = degraded[0].payload
    assert payload["interrupted"] == ["b"]
    assert sorted(payload["unstarted"]) == ["c", "d"]
    assert "no task was restarted" in payload["action"]


async def test_recovery_on_a_clean_shutdown_is_silent(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """Nothing unfinished, nothing to say. A recovery event on every start would train
    everyone to ignore recovery events."""
    store = TaskStore(conn)
    await store.save(
        task("a", kind=TaskKind.TOOL).with_status(TaskStatus.SUCCEEDED, result=TaskResult(ok=True))
    )

    found = await recover(store, eventlog)

    assert not found.gated and not found.pending
    events = await eventlog.read_range(0, eventlog.last_seq, 100)
    assert not [e for e in events if e.type == "system.degraded"]


async def test_a_recovered_graph_refuses_to_run_its_dependents(
    conn: aiosqlite.Connection,
) -> None:
    """The consequence that makes the rule safe rather than merely cautious: after
    recovery, the interrupted task is a failure, so everything downstream is `SKIPPED`.
    Nothing proceeds on a result nobody verified — including the independent branch's
    sibling, which is *not* downstream and does run."""
    store = TaskStore(conn)
    await crashed_graph(store)
    await recover(store)

    graph = TaskGraph(await store.load_graph(ROOT))
    ran: list[str] = []

    async def runner(t: Task) -> TaskResult:
        ran.append(t.id)
        return TaskResult(ok=True)

    status = await Scheduler(graph, dict.fromkeys(TaskKind, runner), store=store).run()

    assert ran == ["d"], "a task downstream of an interrupted one was executed"
    assert graph["c"].status is TaskStatus.SKIPPED
    assert graph["b"].status is TaskStatus.FAILED
    assert status is TaskStatus.FAILED
