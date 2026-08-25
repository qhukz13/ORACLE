"""The scheduler (ORCHESTRATION.md §3 "Scheduling").

Batched topological dispatch, deliberately boring:

    loop:
      ready = ready_tasks(graph)
      dispatch up to available slots, per the concurrency limits
      await any completion
      record → re-derive ready set → repeat
    until nothing is left non-terminal

The interesting decisions are the ones about *who asserts what*, and they come from
measurement rather than taste (`logs/development/2026-08-24-p6t5-antigravity-planning.md`):

* **Cancellation is the scheduler's own record, never an inference.** A cancelled
  Antigravity run reports `status: ERROR` / "timeout waiting for response" — the same
  shape as a genuine vendor timeout. So a task this scheduler cancelled is `CANCELLED`
  whatever its runner then says, and `TIMEOUT` is only ever asserted by the clock here.
  Without that rule, `SKIPPED ≠ CANCELLED` and `TIMEOUT ≠ FAILED` survive in the enum and
  die in practice.
* **A graph of independent tasks is the common case, not the degenerate one.** Five of
  twelve valid plans in the P6-T5 spike declared no dependencies at all, so "the ready set
  is everything" is the path that must be correct and legible, not an edge case.
* **The result gates, not the claim.** A runner returns a `TaskResult`; `result.ok` is
  what makes dependents eligible, and a runner is expected to have put ORACLE's own
  evidence there (INTEGRATIONS.md §7).

Runners are injected, one per `TaskKind`. This module knows nothing about tools, agents
or worktrees — which is why the whole scheduler is testable with fakes and no vendor, and
why P7-T2 can wrap `DelegationService` without touching a line of it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from oracle.core.eventlog import EventLog
from oracle.core.events import Event, new_id
from oracle.logsink import get_logger
from oracle.orchestration.graph import TaskGraph
from oracle.orchestration.models import (
    Task,
    TaskError,
    TaskKind,
    TaskResult,
    TaskStatus,
)
from oracle.orchestration.store import TaskStore

log = get_logger(__name__)

Runner = Callable[[Task], Awaitable[TaskResult]]


@dataclass(frozen=True)
class Limits:
    """Config, not code (ORCHESTRATION.md §3). Delegations are bounded at 2 because each
    is minutes long, costs quota and holds a worktree; local-model work at 1 because
    there is one GPU and one resident model (ADR-0004); tool tasks at 4 so a graph
    cannot fork-bomb the machine."""

    delegation: int = 2
    tool: int = 4
    local: int = 1
    #: Per-kind wall-clock ceiling. A DELEGATION that trips this is `TIMEOUT`, not
    #: `FAILED`, and its runner is still expected to have collected whatever evidence
    #: exists — a timed-out worker may well have done the work.
    timeout_s: dict[TaskKind, float] = field(
        default_factory=lambda: {
            TaskKind.TOOL: 120.0,
            TaskKind.VERIFY: 900.0,
            TaskKind.REPORT: 120.0,
            TaskKind.PLANNING: 600.0,
            TaskKind.DELEGATION: 3600.0,
        }
    )

    def slot_class(self, kind: TaskKind) -> str:
        if kind is TaskKind.DELEGATION:
            return "delegation"
        if kind in (TaskKind.PLANNING, TaskKind.REPORT):
            return "local"
        return "tool"

    def capacity(self, slot: str) -> int:
        return {"delegation": self.delegation, "tool": self.tool, "local": self.local}[slot]


class Scheduler:
    """Runs one graph to completion. One instance per graph run, like a turn."""

    def __init__(
        self,
        graph: TaskGraph,
        runners: dict[TaskKind, Runner],
        *,
        store: TaskStore | None = None,
        eventlog: EventLog | None = None,
        limits: Limits | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.graph = graph
        self.runners = runners
        #: Both optional so the algebra can be tested without a database or a log; in the
        #: daemon both are always present, and `save()` runs before every announcement.
        self._store = store
        self._log = eventlog
        self.limits = limits or Limits()
        self._session_id = session_id
        self._trace = trace_id or new_id("tr")
        self._running: dict[str, asyncio.Task[TaskResult]] = {}
        #: Ids this scheduler cancelled. Consulted *before* a runner's own answer,
        #: because a cancelled run and a failed one look identical from the outside.
        self._cancelled: set[str] = set()
        self._done = asyncio.Event()

    # -- the loop ------------------------------------------------------------

    async def run(self) -> TaskStatus:
        await self._persist_all()
        for task in self.graph.tasks:
            await self._emit("task.created", task, {"kind": str(task.kind), "root": task.root_id})

        while not self.graph.done():
            await self._cascade_skips()
            if self.graph.done():
                break
            dispatched = await self._dispatch()
            if not self._running:
                if dispatched:
                    continue
                # Nothing running, nothing ready, nothing skippable, and tasks remain.
                # In a validated DAG this is unreachable; if it ever happens, stopping
                # loudly beats spinning silently.
                log.error(
                    "graph.stalled",
                    root=self.graph.root_id,
                    active=[t.id for t in self.graph.active()],
                )
                await self._stall()
                break
            await self._collect_one()

        status = self.graph.status()
        log.info(
            "graph.finished", root=self.graph.root_id, status=str(status), size=len(self.graph)
        )
        self._done.set()
        return status

    async def _dispatch(self) -> bool:
        """Start every ready task that fits in its slot class. Returns whether anything
        started, so the loop can tell 'waiting on work' from 'stuck'."""
        started = False
        in_flight = self._slot_usage()
        for task in self.graph.ready():
            slot = self.limits.slot_class(task.kind)
            if in_flight[slot] >= self.limits.capacity(slot):
                continue
            if task.kind not in self.runners:
                await self._finish(
                    task,
                    TaskStatus.FAILED,
                    TaskResult(
                        ok=False,
                        summary=f"no runner registered for {task.kind}",
                        error=TaskError(kind="not_found", message=f"no runner for {task.kind}"),
                    ),
                )
                continue
            in_flight[slot] += 1
            started = True
            await self._start(task)
        return started

    def _slot_usage(self) -> dict[str, int]:
        usage = {"delegation": 0, "tool": 0, "local": 0}
        for task_id in self._running:
            usage[self.limits.slot_class(self.graph[task_id].kind)] += 1
        return usage

    async def _start(self, task: Task) -> None:
        running = task.with_status(TaskStatus.RUNNING)
        self.graph.replace(running)
        await self._persist(running)
        await self._emit("task.updated", running, {"status": str(running.status)})
        timeout = self.limits.timeout_s.get(task.kind)
        runner = self.runners[task.kind]
        self._running[task.id] = asyncio.create_task(
            asyncio.wait_for(runner(running), timeout=timeout), name=f"task:{task.id}"
        )

    async def _collect_one(self) -> None:
        done, _ = await asyncio.wait(self._running.values(), return_when=asyncio.FIRST_COMPLETED)
        for finished in done:
            task_id = next(tid for tid, t in self._running.items() if t is finished)
            del self._running[task_id]
            await self._record(self.graph[task_id], finished)

    async def _record(self, task: Task, finished: asyncio.Task[TaskResult]) -> None:
        """Turn one completed coroutine into one task transition.

        The order of these branches is the contract: **cancellation first**, before the
        runner's own answer is even read. A runner cannot talk this scheduler out of a
        cancellation it performed itself."""
        if task.id in self._cancelled:
            with contextlib.suppress(BaseException):
                finished.result()
            await self._finish(
                task,
                TaskStatus.CANCELLED,
                TaskResult(ok=False, summary="cancelled by the supervisor"),
            )
            return
        try:
            result = finished.result()
        except TimeoutError:
            # The clock, asserted here and nowhere else. TIMEOUT is not FAILED: a
            # timed-out worker may have done the work, and its evidence is still
            # collectable by whoever owns the workspace.
            await self._finish(
                task,
                TaskStatus.TIMEOUT,
                TaskResult(
                    ok=False,
                    summary=f"exceeded the {task.kind} timeout",
                    error=TaskError(kind="timeout", message="task timed out", retryable=False),
                ),
            )
            return
        except asyncio.CancelledError:
            # Cancelled without going through `cancel()` — a shutdown, a HALT. Not a
            # failure of the task, and not something to retry.
            await self._finish(
                task, TaskStatus.CANCELLED, TaskResult(ok=False, summary="cancelled")
            )
            return
        except Exception as exc:  # a runner that raised is a runner that failed
            await self._retry_or_fail(
                task,
                TaskResult(
                    ok=False,
                    summary=str(exc),
                    error=TaskError(kind="execution_failed", message=str(exc)),
                ),
            )
            return
        if result.ok:
            await self._finish(task, TaskStatus.SUCCEEDED, result)
        else:
            await self._retry_or_fail(task, result)

    async def _retry_or_fail(self, task: Task, result: TaskResult) -> None:
        retryable = result.error is not None and result.error.retryable
        if retryable and task.attempt < task.max_attempts:
            retried = task.model_copy(
                update={
                    "status": TaskStatus.PENDING,
                    "attempt": task.attempt + 1,
                    "started_at": None,
                    "result": None,
                }
            )
            self.graph.replace(retried)
            await self._persist(retried)
            await self._emit(
                "task.updated", retried, {"status": str(retried.status), "attempt": retried.attempt}
            )
            return
        await self._finish(task, TaskStatus.FAILED, result)

    async def _cascade_skips(self) -> None:
        """A dependency that ended in anything but success makes its dependents
        unreachable. `SKIPPED` — not `CANCELLED`: nobody stopped these, they simply never
        became eligible, and the graph's shape is the explanation."""
        while blocked := self.graph.blocked():
            for task in blocked:
                reason = ", ".join(
                    f"{dep} {self.graph[dep].status}"
                    for dep in task.depends_on
                    if self.graph[dep].terminal
                    and self.graph[dep].status is not TaskStatus.SUCCEEDED
                )
                await self._finish(
                    task,
                    TaskStatus.SKIPPED,
                    TaskResult(ok=False, summary=f"skipped: {reason}"),
                )

    async def _stall(self) -> None:
        for task in self.graph.active():
            await self._finish(
                task,
                TaskStatus.FAILED,
                TaskResult(
                    ok=False,
                    summary="the graph stalled with no runnable task",
                    error=TaskError(kind="execution_failed", message="graph stalled"),
                ),
            )

    # -- cancellation --------------------------------------------------------

    async def cancel(self, task_id: str) -> None:
        """Cancel one task. Its dependents become `SKIPPED` on the next pass;
        independent branches carry on, which is the whole reason a graph beats a script."""
        if task_id not in self.graph:
            return
        self._cancelled.add(task_id)
        running = self._running.get(task_id)
        if running is not None:
            running.cancel()
            return
        task = self.graph[task_id]
        if not task.terminal:
            await self._finish(
                task, TaskStatus.CANCELLED, TaskResult(ok=False, summary="cancelled before it ran")
            )

    async def cancel_root(self) -> None:
        """Cancel every non-terminal task in the graph — the operator's 'stop this'.
        HALT is above this and unchanged (AGENT_RUNTIME.md §7)."""
        for task in self.graph.active():
            await self.cancel(task.id)

    # -- bookkeeping ---------------------------------------------------------

    async def _finish(self, task: Task, status: TaskStatus, result: TaskResult) -> None:
        finished = task.with_status(status, result=result)
        self.graph.replace(finished)
        await self._persist(finished)
        await self._emit(
            "task.finished",
            finished,
            {
                "status": str(status),
                "ok": result.ok,
                "summary": result.summary,
                # Evidence is what ORACLE measured and is safe to act on; the worker's
                # claim rides beside it, labelled, and gates nothing.
                "evidence": result.evidence,
                "claim": result.claim,
            },
        )

    async def _persist(self, task: Task) -> None:
        if self._store is not None:
            await self._store.save(task)

    async def _persist_all(self) -> None:
        if self._store is not None:
            await self._store.save_all(self.graph.tasks)

    async def _emit(self, event_type: str, task: Task, payload: dict[str, object]) -> None:
        if self._log is None:
            return
        await self._log.append(
            Event(
                type=event_type,
                session_id=self._session_id,
                task_id=task.id,
                trace_id=self._trace,
                actor="system",
                payload={"root_id": task.root_id, **payload},
            )
        )
