"""The scheduler, with fake runners: order, gating, failure, cancellation, durability.

No vendor, no toolhost, no clock the test does not control. Every runner here is a
coroutine the test wrote, which is the point — P7-T1's whole claim is that the
supervisor's correctness is decidable without running anything real.

Three tests exist because of what P6-T5 measured rather than what the design assumed:
a cancelled run reports an error indistinguishable from a timeout, so cancellation must
be the *scheduler's* assertion; a graph with no edges is the common case; and a result
that lives only in a worktree does not survive the worktree.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from oracle.core.eventlog import EventLog
from oracle.integrations.workspace import create_worktree
from oracle.orchestration.graph import TaskGraph
from oracle.orchestration.models import (
    Task,
    TaskError,
    TaskKind,
    TaskResult,
    TaskSpec,
    TaskStatus,
)
from oracle.orchestration.scheduler import Limits, Scheduler
from oracle.orchestration.store import TaskStore
from tests.helpers_delegation import make_repo

ROOT = "tk_root"


def task(
    task_id: str,
    *deps: str,
    kind: TaskKind = TaskKind.TOOL,
    max_attempts: int = 1,
) -> Task:
    return Task(
        id=task_id,
        root_id=ROOT,
        kind=kind,
        spec=TaskSpec(objective=f"do {task_id}", role="coder"),
        depends_on=tuple(deps),
        max_attempts=max_attempts,
    )


def ok_runner(order: list[str], *, delay: float = 0.0):
    async def run(t: Task) -> TaskResult:
        if delay:
            await asyncio.sleep(delay)
        order.append(t.id)
        return TaskResult(ok=True, summary=f"{t.id} done", evidence={"ran": t.id})

    return run


def failing_runner(order: list[str], failures: set[str], *, retryable: bool = False):
    async def run(t: Task) -> TaskResult:
        order.append(t.id)
        if t.id in failures:
            return TaskResult(
                ok=False,
                summary=f"{t.id} failed",
                error=TaskError(kind="execution_failed", message="boom", retryable=retryable),
            )
        return TaskResult(ok=True, summary=f"{t.id} done")

    return run


# -- the happy path ------------------------------------------------------------


async def test_a_four_task_graph_runs_in_dependency_order() -> None:
    """The shape P7 exists to run: tool → delegation → verify → report. The assertion is
    the *order*, because that is the only thing a scheduler is for."""
    graph = TaskGraph(
        [
            task("investigate", kind=TaskKind.TOOL),
            task("fix", "investigate", kind=TaskKind.DELEGATION),
            task("verify", "fix", kind=TaskKind.VERIFY),
            task("report", "verify", kind=TaskKind.REPORT),
        ]
    )
    order: list[str] = []
    runner = ok_runner(order)
    status = await Scheduler(graph, dict.fromkeys(TaskKind, runner), limits=Limits(local=4)).run()

    assert order == ["investigate", "fix", "verify", "report"]
    assert status is TaskStatus.SUCCEEDED
    assert all(t.status is TaskStatus.SUCCEEDED for t in graph.tasks)
    assert graph["report"].result is not None and graph["report"].result.ok


async def test_independent_tasks_run_concurrently_not_in_sequence() -> None:
    """A graph with no edges is the common case (OQ-20), so 'all ready at once' must
    actually dispatch at once. Measured by concurrency, not by wall clock: the runner
    records its own high-water mark."""
    live = 0
    peak = 0

    async def run(t: Task) -> TaskResult:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return TaskResult(ok=True)

    graph = TaskGraph([task(f"t{i}") for i in range(4)])
    await Scheduler(graph, {TaskKind.TOOL: run}, limits=Limits(tool=4)).run()
    assert peak == 4


async def test_the_concurrency_limit_is_a_limit() -> None:
    """Delegations are capped at 2: each is minutes long, costs quota and holds a
    worktree (ORCHESTRATION.md §3)."""
    live = 0
    peak = 0

    async def run(t: Task) -> TaskResult:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return TaskResult(ok=True)

    graph = TaskGraph([task(f"d{i}", kind=TaskKind.DELEGATION) for i in range(5)])
    await Scheduler(graph, {TaskKind.DELEGATION: run}, limits=Limits(delegation=2)).run()
    assert peak == 2


# -- failure -------------------------------------------------------------------


async def test_a_failure_skips_dependents_and_leaves_independent_branches_alone() -> None:
    """`SKIPPED`, not `CANCELLED`: nobody stopped these, they never became eligible. And
    the branch that shares no ancestor with the failure still finishes — the reason a
    graph beats a script."""
    graph = TaskGraph(
        [
            task("a"),
            task("b", "a"),
            task("c", "b"),
            task("independent"),
        ]
    )
    order: list[str] = []
    await Scheduler(graph, {TaskKind.TOOL: failing_runner(order, {"a"})}).run()

    assert graph["a"].status is TaskStatus.FAILED
    assert graph["b"].status is TaskStatus.SKIPPED
    assert graph["c"].status is TaskStatus.SKIPPED, "the cascade stopped one level down"
    assert graph["independent"].status is TaskStatus.SUCCEEDED
    assert "b" not in order and "c" not in order, "a skipped task was executed anyway"
    assert graph.status() is TaskStatus.FAILED

    reason = graph["b"].result
    assert reason is not None and "a failed" in reason.summary


async def test_a_retryable_failure_is_retried_within_max_attempts() -> None:
    attempts: list[int] = []

    async def flaky(t: Task) -> TaskResult:
        attempts.append(t.attempt)
        if t.attempt < 2:
            return TaskResult(
                ok=False,
                summary="transient",
                error=TaskError(kind="execution_failed", message="try again", retryable=True),
            )
        return TaskResult(ok=True, summary="second time lucky")

    graph = TaskGraph([task("a", max_attempts=2)])
    status = await Scheduler(graph, {TaskKind.TOOL: flaky}).run()
    assert attempts == [1, 2]
    assert status is TaskStatus.SUCCEEDED


async def test_a_denial_is_never_retried_however_many_attempts_remain() -> None:
    """Retrying a denial is how an agent nags a person into approving something."""
    attempts: list[int] = []

    async def denied(t: Task) -> TaskResult:
        attempts.append(t.attempt)
        return TaskResult(
            ok=False,
            summary="denied",
            error=TaskError(kind="denied", message="policy said no", retryable=False),
        )

    graph = TaskGraph([task("a", max_attempts=3)])
    status = await Scheduler(graph, {TaskKind.TOOL: denied}).run()
    assert attempts == [1]
    assert status is TaskStatus.FAILED


async def test_a_runner_that_raises_fails_its_task_and_not_the_graph_loop() -> None:
    async def explode(t: Task) -> TaskResult:
        raise RuntimeError("runner exploded")

    graph = TaskGraph([task("a"), task("b")])

    async def mixed(t: Task) -> TaskResult:
        if t.id == "a":
            return await explode(t)
        return TaskResult(ok=True)

    await Scheduler(graph, {TaskKind.TOOL: mixed}).run()
    assert graph["a"].status is TaskStatus.FAILED
    assert graph["b"].status is TaskStatus.SUCCEEDED
    error = graph["a"].result
    assert error is not None and error.error is not None
    assert "exploded" in error.error.message


async def test_a_missing_runner_fails_the_task_rather_than_hanging() -> None:
    graph = TaskGraph([task("a", kind=TaskKind.PLANNING)])
    status = await Scheduler(graph, {}).run()
    assert status is TaskStatus.FAILED
    assert graph["a"].result is not None
    assert "no runner" in graph["a"].result.summary


# -- the clock and the operator ------------------------------------------------


async def test_a_task_that_outruns_its_timeout_is_TIMEOUT_and_not_FAILED() -> None:
    """TIMEOUT ≠ FAILED, asserted where it is decided: a timed-out worker may well have
    done the work, and folding the two loses the only fact that says so."""

    async def slow(t: Task) -> TaskResult:
        await asyncio.sleep(5)
        return TaskResult(ok=True)

    graph = TaskGraph([task("a")])
    limits = Limits(timeout_s={TaskKind.TOOL: 0.05})
    status = await Scheduler(graph, {TaskKind.TOOL: slow}, limits=limits).run()

    assert graph["a"].status is TaskStatus.TIMEOUT
    assert status is TaskStatus.TIMEOUT


async def test_cancellation_is_the_schedulers_record_not_the_runners_answer() -> None:
    """The finding this test exists for: a cancelled `agy` run reports
    `status: ERROR` / "timeout waiting for response" — indistinguishable from a genuine
    vendor timeout. So a runner that answers *anything* after being cancelled must not be
    able to talk the scheduler out of `CANCELLED`."""
    started = asyncio.Event()

    async def liar(t: Task) -> TaskResult:
        started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            # The vendor's shape: an error that looks exactly like a timeout.
            return TaskResult(
                ok=False,
                summary="timeout waiting for response",
                error=TaskError(kind="timeout", message="timeout waiting for response"),
            )
        return TaskResult(ok=True)

    graph = TaskGraph([task("a"), task("b", "a"), task("elsewhere")])
    scheduler = Scheduler(graph, {TaskKind.TOOL: liar})

    async def cancel_once_running() -> None:
        await started.wait()
        await scheduler.cancel("a")

    canceller = asyncio.create_task(cancel_once_running())
    status = await asyncio.wait_for(scheduler.run(), timeout=10)
    await canceller

    assert graph["a"].status is TaskStatus.CANCELLED, "the runner's story won"
    assert graph["b"].status is TaskStatus.SKIPPED, (
        "a dependent of a cancelled task was not skipped"
    )
    assert graph["elsewhere"].status is TaskStatus.SUCCEEDED
    assert status is TaskStatus.CANCELLED


async def test_cancel_root_stops_every_non_terminal_task() -> None:
    running = asyncio.Event()

    async def slow(t: Task) -> TaskResult:
        running.set()
        await asyncio.sleep(5)
        return TaskResult(ok=True)

    graph = TaskGraph([task("a"), task("b"), task("c", "a")])
    scheduler = Scheduler(graph, {TaskKind.TOOL: slow}, limits=Limits(tool=2))

    async def stop() -> None:
        await running.wait()
        await scheduler.cancel_root()

    stopper = asyncio.create_task(stop())
    status = await asyncio.wait_for(scheduler.run(), timeout=10)
    await stopper

    assert status is TaskStatus.CANCELLED
    assert graph["a"].status is TaskStatus.CANCELLED
    assert graph["b"].status is TaskStatus.CANCELLED
    assert graph["c"].status is TaskStatus.CANCELLED


# -- durability ----------------------------------------------------------------


async def test_the_graph_round_trips_through_the_tasks_table(
    conn: aiosqlite.Connection,
) -> None:
    """Migration 0002 applied by the existing runner; the row is the record."""
    store = TaskStore(conn)
    graph = TaskGraph([task("a"), task("b", "a", kind=TaskKind.DELEGATION)])
    await Scheduler(graph, dict.fromkeys(TaskKind, ok_runner([])), store=store).run()

    reloaded = await store.load_graph(ROOT)
    assert {t.id for t in reloaded} == {"a", "b"}
    rebuilt = TaskGraph(reloaded)
    assert rebuilt.status() is TaskStatus.SUCCEEDED
    assert rebuilt["b"].depends_on == ("a",)
    assert rebuilt["b"].kind is TaskKind.DELEGATION
    assert rebuilt["b"].result is not None and rebuilt["b"].result.evidence == {"ran": "b"}
    assert rebuilt["b"].finished_at is not None


async def test_what_was_running_when_the_daemon_died_is_findable(
    conn: aiosqlite.Connection,
) -> None:
    """Recovery's input. The rule it feeds (ORCHESTRATION.md §3) is *gate, never
    auto-restart* — but the rule needs the row to have been written before the crash,
    which is what this asserts."""
    store = TaskStore(conn)
    await store.save_all(
        [
            task("done").with_status(TaskStatus.SUCCEEDED),
            task("interrupted").with_status(TaskStatus.RUNNING),
            task("never-ran"),
        ]
    )
    unfinished = {t.id: t.status for t in await store.unfinished()}
    assert unfinished == {
        "interrupted": TaskStatus.RUNNING,
        "never-ran": TaskStatus.PENDING,
    }


async def test_task_events_reach_the_log_with_their_task_id(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """The execution tree the UI renders is a query over these (ORCHESTRATION.md §6):
    tasks by `root_id`, joined to their events. No parallel bookkeeping."""
    graph = TaskGraph([task("a"), task("b", "a")])
    await Scheduler(
        graph, {TaskKind.TOOL: ok_runner([])}, store=TaskStore(conn), eventlog=eventlog
    ).run()

    events = await eventlog.read_range(0, eventlog.last_seq, 500)
    kinds = [e.type for e in events if e.type.startswith("task.")]
    assert kinds.count("task.created") == 2
    assert kinds.count("task.finished") == 2
    finished = [e for e in events if e.type == "task.finished"]
    assert {e.task_id for e in finished} == {"a", "b"}
    assert all(e.payload["root_id"] == ROOT for e in finished)
    # Evidence and claim stay separate all the way to the wire.
    assert "evidence" in finished[0].payload and "claim" in finished[0].payload


# -- the harvest ---------------------------------------------------------------


def test_a_harvested_result_survives_the_worktree(tmp_path: Path) -> None:
    """P6-T5 finding 8, turned into a test: delegates are forbidden git commands, so a
    result used to live only as long as its checkout. ORACLE commits it — the ban on the
    delegate committing stands — and `discard(keep_branch=True)` then throws away a
    checkout instead of the work."""
    repo = make_repo(tmp_path)
    worktree = create_worktree(repo, "tk-harvest")
    (worktree.ws.path / "answer.txt").write_text("42\n", encoding="utf-8")

    sha = worktree.harvest("worker output for tk-harvest")
    assert sha is not None
    worktree.discard(keep_branch=True)

    assert not worktree.ws.path.exists(), "the checkout outlived discard()"
    import subprocess

    kept = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "show", f"{sha}:answer.txt"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert kept.strip() == "42", "the result did not survive its workspace"


def test_harvesting_nothing_commits_nothing(tmp_path: Path) -> None:
    """A worker that did nothing must not leave an empty commit implying it did."""
    repo = make_repo(tmp_path)
    worktree = create_worktree(repo, "tk-empty")
    assert worktree.harvest("nothing happened") is None


@pytest.mark.parametrize("keep", [True, False])
def test_discard_keeps_the_branch_only_when_asked(tmp_path: Path, keep: bool) -> None:
    import subprocess

    repo = make_repo(tmp_path)
    worktree = create_worktree(repo, f"tk-keep-{keep}")
    (worktree.ws.path / "x.txt").write_text("x\n", encoding="utf-8")
    worktree.harvest("work")
    worktree.discard(keep_branch=keep)

    branches = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "branch", "--list", worktree.branch],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert bool(branches.strip()) is keep


async def test_the_graph_survives_the_process_that_wrote_it(tmp_path: Path) -> None:
    """Durable means durable: a second connection, opened after the first is closed, sees
    the graph. The round-trip test above shares a connection and would still pass if the
    rows only ever lived in a transaction nobody committed."""
    from oracle.storage.db import connect, migrate

    path = tmp_path / "restart.sqlite3"
    first = await connect(path)
    await migrate(first)
    graph = TaskGraph([task("a"), task("b", "a")])
    await Scheduler(graph, {TaskKind.TOOL: ok_runner([])}, store=TaskStore(first)).run()
    await first.close()

    second = await connect(path)
    try:
        reloaded = TaskGraph(await TaskStore(second).load_graph(ROOT))
        assert reloaded.status() is TaskStatus.SUCCEEDED
        assert reloaded["b"].depends_on == ("a",)
        assert reloaded["a"].started_at is not None
    finally:
        await second.close()
