"""The graph's human surfaces: stopping it, parking it, and reading it.

Everything Phase 7 built so far happens where nobody can see or reach it. These tests are
about the two things that make a supervisor usable rather than merely correct — a person
can stop it, and a person can look at it — plus the state that exists so a graph can wait
for a person without holding a worker slot hostage.

The HALT test asserts on a **real child process**, because a HALT that "works" against
fake runners is a HALT that has never been tested.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from oracle.core.approvals import ApprovalStore
from oracle.core.eventlog import EventLog
from oracle.orchestration.graph import TaskGraph
from oracle.orchestration.models import Task, TaskKind, TaskResult, TaskSpec, TaskStatus
from oracle.orchestration.scheduler import Limits
from oracle.orchestration.service import GraphService
from oracle.orchestration.store import TaskStore
from oracle.runners.delegation import make_delegation_runner
from oracle.runners.tool import make_tool_runner
from tests.helpers_delegation import (
    SMOKE,
    make_repo,
    make_service,
    stub_adapter,
    wait_event,
)
from tests.test_orchestration_runners import POLICY, executor_for

ROOT = "tk_root"


def task(
    task_id: str,
    *deps: str,
    kind: TaskKind = TaskKind.TOOL,
    tool: str | None = None,
    args: dict[str, Any] | None = None,
) -> Task:
    return Task(
        id=task_id,
        root_id=ROOT,
        kind=kind,
        spec=TaskSpec(objective=f"do {task_id}", role="coder", tool=tool, args=args or {}),
        depends_on=tuple(deps),
    )


def service(conn: aiosqlite.Connection, eventlog: EventLog, **kwargs: Any) -> GraphService:
    return GraphService(eventlog, TaskStore(conn), **kwargs)


# -- stopping it ---------------------------------------------------------------


async def test_cancelling_one_task_from_outside_spares_the_other_branch(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """The reason a graph beats a script: stopping one thing stops exactly what depended
    on it. The scheduler's own record wins over whatever the cancelled runner says."""
    started = asyncio.Event()

    async def slow(t: Task) -> TaskResult:
        if t.id == "a":
            started.set()
            await asyncio.sleep(5)
        return TaskResult(ok=True, summary=f"{t.id} done")

    graphs = service(conn, eventlog)
    graph = TaskGraph([task("a"), task("b", "a"), task("elsewhere")])
    running = asyncio.create_task(graphs.run(graph, {TaskKind.TOOL: slow}))

    await asyncio.wait_for(started.wait(), timeout=10)
    assert graphs.running == [ROOT], "a live graph was not addressable"
    assert await graphs.cancel_task(ROOT, "a") is True

    status = await asyncio.wait_for(running, timeout=15)

    assert graph["a"].status is TaskStatus.CANCELLED
    assert graph["b"].status is TaskStatus.SKIPPED
    assert graph["elsewhere"].status is TaskStatus.SUCCEEDED
    assert status is TaskStatus.CANCELLED
    assert graphs.running == [], "the handle outlived the graph"


async def test_cancelling_an_unknown_graph_or_task_is_a_no_op_not_a_crash(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    graphs = service(conn, eventlog)
    assert await graphs.cancel_root("tk_nothing") is False
    assert await graphs.cancel_task("tk_nothing", "a") is False


async def test_cancelling_the_root_stops_every_task(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    running_now = asyncio.Event()

    async def slow(t: Task) -> TaskResult:
        running_now.set()
        await asyncio.sleep(5)
        return TaskResult(ok=True)

    graphs = service(conn, eventlog, limits=Limits(tool=3))
    graph = TaskGraph([task("a"), task("b"), task("c", "a")])
    running = asyncio.create_task(graphs.run(graph, {TaskKind.TOOL: slow}))

    await asyncio.wait_for(running_now.wait(), timeout=10)
    assert await graphs.cancel_root(ROOT) is True
    status = await asyncio.wait_for(running, timeout=15)

    assert status is TaskStatus.CANCELLED
    assert all(t.terminal for t in graph.tasks)


async def test_halt_reaches_a_graphs_child_process(
    tmp_path: Path, conn: aiosqlite.Connection, eventlog: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one that matters, and the one a fake runner cannot prove.

    HALT cancels every tracked task. Cancelling the graph's coroutine does *not*
    automatically cancel the runner tasks it spawned — they are independent asyncio tasks
    — so the scheduler cancels its own children, the delegation runner's cancellation
    reaches `DelegationService`, and the vendor process dies. **No HALT path was added
    for graphs**; this test is what says that claim is true rather than hopeful.
    """
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    monkeypatch.setenv("STUB_TRUNCATE_AT", "3")
    monkeypatch.setenv("STUB_HANG", "1")  # the shape of a wedged delegate
    repo = make_repo(tmp_path)
    delegations, approvals, _engine = make_service(tmp_path, eventlog, stub_adapter())
    graphs = service(conn, eventlog)
    graph = TaskGraph([task("d", kind=TaskKind.DELEGATION)])

    running = asyncio.create_task(
        graphs.run(graph, {TaskKind.DELEGATION: make_delegation_runner(delegations, repo)})
    )

    asked = await wait_event(
        eventlog, lambda e: e.type == "approval.requested", what="the egress approval"
    )
    await approvals.resolve(str(asked.payload["approval_id"]), True)
    pid = await _wait_for_pid(delegations, "d")

    # HALT, in the only part that matters here: cancel the tracked task.
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    for _ in range(100):
        if not _alive(pid):
            break
        await asyncio.sleep(0.1)
    assert not _alive(pid), f"the delegate's process {pid} outlived HALT"

    reloaded = await TaskStore(conn).load("d")
    assert reloaded is not None
    assert reloaded.status is TaskStatus.CANCELLED, (
        "a task left RUNNING in the table would be read as an interrupted agent on the "
        "next start-up, which is a stronger claim than the truth"
    )


async def _wait_for_pid(delegations: Any, task_id: str) -> int:
    for _ in range(200):
        active = delegations.get(task_id)
        if active is not None and active.handle is not None:
            return int(active.handle.proc.pid)
        await asyncio.sleep(0.05)
    raise AssertionError("the delegate never started")


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # type: ignore[attr-defined]
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))  # type: ignore[attr-defined]
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# -- parking on a person -------------------------------------------------------


def approving_policy(tmp_path: Path) -> Path:
    """`fs.read` raised to a tier that wants confirmation, so a TOOL task has something
    real to wait for - and `fs.list` left at T0 beside it, so a second task can prove the
    parked one is not holding the slot. Written here rather than by widening a fixture the
    security suite shares."""
    path = tmp_path / "approve-policy.yaml"
    path.write_text(
        POLICY.format(root=(tmp_path / "project").as_posix()).replace(
            "    tier: T0", "    tier: T2"
        )
        + "  fs.list:\n    tier: T0\n    scopes: [projects]\n",
        encoding="utf-8",
    )
    return path


async def test_a_task_needing_approval_parks_and_frees_its_slot(
    tmp_path: Path, conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """`WAITING`'s first real use. The slot limit is 1, so if parking held the slot the
    second task could not finish before the approval is answered — and it does."""
    repo = make_repo(tmp_path)
    executor = executor_for(tmp_path, policy=approving_policy(tmp_path))
    approvals = ApprovalStore(eventlog, executor, ttl_s=30.0)
    runner = make_tool_runner(executor, approvals)

    graphs = service(conn, eventlog, limits=Limits(tool=1))
    graph = TaskGraph(
        [
            task("needs_ok", tool="fs.read", args={"path": str(repo / "app.py")}),
            # A different tool, still T0 under this policy: if parking held the slot,
            # this could not finish before the approval is answered.
            task("free", tool="fs.list", args={"path": str(repo)}),
        ]
    )
    running = asyncio.create_task(graphs.run(graph, {TaskKind.TOOL: runner}))

    asked = await wait_event(
        eventlog, lambda e: e.type == "approval.requested", what="the parked task's approval"
    )
    approval_id = str(asked.payload["approval_id"])
    assert approval_id

    # The parked task is WAITING and the *other* task got the slot and finished.
    for _ in range(200):
        if graph["needs_ok"].status is TaskStatus.WAITING and graph["free"].terminal:
            break
        await asyncio.sleep(0.05)
    assert graph["needs_ok"].status is TaskStatus.WAITING, "the task did not park"
    assert graph["free"].status is TaskStatus.SUCCEEDED, "parking held the slot"

    await approvals.resolve(approval_id, True)
    status = await asyncio.wait_for(running, timeout=30)

    assert graph["needs_ok"].status is TaskStatus.SUCCEEDED
    assert status is TaskStatus.SUCCEEDED


async def test_a_refused_approval_fails_the_task_with_the_reason(
    tmp_path: Path, conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """The gate's own answer, not the runner's narration: the second attempt runs into
    `approval_required` and the task fails with it."""
    repo = make_repo(tmp_path)
    executor = executor_for(tmp_path, policy=approving_policy(tmp_path))
    approvals = ApprovalStore(eventlog, executor, ttl_s=30.0)
    runner = make_tool_runner(executor, approvals)

    graphs = service(conn, eventlog)
    graph = TaskGraph([task("needs_ok", tool="fs.read", args={"path": str(repo / "app.py")})])
    running = asyncio.create_task(graphs.run(graph, {TaskKind.TOOL: runner}))

    asked = await wait_event(
        eventlog, lambda e: e.type == "approval.requested", what="the parked task's approval"
    )
    await approvals.resolve(str(asked.payload["approval_id"]), False)

    status = await asyncio.wait_for(running, timeout=30)

    assert status is TaskStatus.FAILED
    result = graph["needs_ok"].result
    assert result is not None and result.error is not None
    assert result.error.kind == "approval_required"
    assert result.error.retryable is False, "a refusal is not a transient failure"


async def test_cancelling_a_parked_task_does_not_leave_it_waiting(
    tmp_path: Path, conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """A parked task holds no slot, so it never passes through the normal completion
    path. Cancelling it must still end it — and must not be undone when the approval it
    was waiting on is answered afterwards."""
    repo = make_repo(tmp_path)
    executor = executor_for(tmp_path, policy=approving_policy(tmp_path))
    approvals = ApprovalStore(eventlog, executor, ttl_s=30.0)
    runner = make_tool_runner(executor, approvals)

    graphs = service(conn, eventlog)
    graph = TaskGraph([task("needs_ok", tool="fs.read", args={"path": str(repo / "app.py")})])
    running = asyncio.create_task(graphs.run(graph, {TaskKind.TOOL: runner}))

    asked = await wait_event(
        eventlog, lambda e: e.type == "approval.requested", what="the parked task's approval"
    )
    approval_id = str(asked.payload["approval_id"])
    for _ in range(200):
        if graph["needs_ok"].status is TaskStatus.WAITING:
            break
        await asyncio.sleep(0.05)

    await graphs.cancel_task(ROOT, "needs_ok")
    status = await asyncio.wait_for(running, timeout=30)
    await approvals.resolve(approval_id, True)  # too late, and it must stay too late

    assert status is TaskStatus.CANCELLED
    assert graph["needs_ok"].status is TaskStatus.CANCELLED


# -- reading it ----------------------------------------------------------------


async def test_the_tree_is_a_projection_of_the_table(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """No parallel bookkeeping: what a client reads is what the rows say, including a
    skipped task's reason and the evidence/claim split."""

    async def runner(t: Task) -> TaskResult:
        if t.id == "a":
            return TaskResult(ok=False, summary="a failed")
        return TaskResult(ok=True, summary="fine", evidence={"diff_lines": 3}, claim="I did it all")

    graphs = service(conn, eventlog)
    graph = TaskGraph([task("a"), task("b", "a"), task("c")])
    await graphs.run(graph, {TaskKind.TOOL: runner})

    tree = await graphs.tree(ROOT)

    assert tree["root_id"] == ROOT
    assert tree["live"] is False, "a finished graph is not live"
    assert tree["status"] == str(TaskStatus.FAILED)
    by_id = {t["id"]: t for t in tree["tasks"]}
    assert by_id["b"]["status"] == str(TaskStatus.SKIPPED)
    assert "a failed" in by_id["b"]["summary"], "the skip reason did not survive"
    assert by_id["b"]["depends_on"] == ["a"]
    assert by_id["c"]["evidence"] == {"diff_lines": 3}
    assert by_id["c"]["claim"] == "I did it all", "evidence and claim were merged"


async def test_a_live_graph_reports_itself_as_live(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    started = asyncio.Event()

    async def slow(t: Task) -> TaskResult:
        started.set()
        await asyncio.sleep(5)
        return TaskResult(ok=True)

    graphs = service(conn, eventlog)
    graph = TaskGraph([task("a")])
    running = asyncio.create_task(graphs.run(graph, {TaskKind.TOOL: slow}))
    await asyncio.wait_for(started.wait(), timeout=10)

    tree = await graphs.tree(ROOT)
    assert tree["live"] is True
    assert tree["status"] == str(TaskStatus.RUNNING)

    await graphs.cancel_root(ROOT)
    await asyncio.wait_for(running, timeout=15)


async def test_the_tree_of_a_graph_nobody_ran_is_empty_not_an_error(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    tree = await service(conn, eventlog).tree("tk_never")
    assert tree["tasks"] == [] and tree["live"] is False


async def test_a_graph_cannot_be_started_twice(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """Two schedulers over one root would write the same rows from two directions."""
    hold = asyncio.Event()

    async def slow(t: Task) -> TaskResult:
        await hold.wait()
        return TaskResult(ok=True)

    graphs = service(conn, eventlog)
    graph = TaskGraph([task("a")])
    running = asyncio.create_task(graphs.run(graph, {TaskKind.TOOL: slow}))
    for _ in range(200):
        if graphs.running:
            break
        await asyncio.sleep(0.05)

    with pytest.raises(ValueError, match="already running"):
        await graphs.run(TaskGraph([task("a")]), {TaskKind.TOOL: slow})

    hold.set()
    await asyncio.wait_for(running, timeout=15)
