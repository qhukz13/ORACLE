"""What happens to a graph when the daemon dies (ORCHESTRATION.md §3 "Crash recovery").

The rules are Asterim's, ported because they are the ones that survive contact:

1. **Never auto-restart an interrupted agent.** Not "restart if it looks safe" — never.
   A supervisor that cannot prove what a child did while it was dead does not get to
   assume it did nothing.
2. **Gate, loudly.** Every interrupted task is surfaced, by name, on a critical event. A
   recovery that quietly tidies up is a recovery nobody audits.
3. **Corrupt state gates too.** A row that will not load is reported rather than skipped;
   guessing is worse than asking.

What this cannot do yet, stated rather than implied: ORACLE does not record a child PID
on the task row, so the "process still alive → gate; process gone → FAILED(interrupted)"
split in ORCHESTRATION.md §3 is collapsed to its conservative branch — **every**
interrupted task is marked `FAILED(interrupted)` and gated. Both branches gate, so no
decision changes; what is lost is the ability to say *which* of the two happened. Adding
`pid` is a migration and a scheduler hook, and it buys a diagnostic rather than a
different action, so it waits for a task that needs the diagnostic.

Recovery deliberately does **not** delete worktrees. An interrupted delegation may have
left real work on disk, and the whole point of `harvest()` is that such work is worth
something. Cleanup is a decision for whoever reads the gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from oracle.core.eventlog import EventLog
from oracle.core.events import Event, new_id
from oracle.logsink import get_logger
from oracle.orchestration.models import Task, TaskError, TaskResult, TaskStatus
from oracle.orchestration.store import TaskStore

log = get_logger(__name__)

#: The error kind a recovered task carries. Distinct from a plain failure on purpose:
#: "this never finished because the supervisor died" is a different fact from "this ran
#: and failed", and only one of them says anything about the work.
INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class Recovered:
    """What recovery found, for the caller to surface. Returned rather than acted upon:
    this module states facts; deciding what to do about them is a human's job."""

    interrupted: tuple[Task, ...] = ()
    pending: tuple[Task, ...] = ()

    @property
    def gated(self) -> bool:
        return bool(self.interrupted)


async def recover(
    store: TaskStore, eventlog: EventLog | None = None, *, session_id: str | None = None
) -> Recovered:
    """Read what was in flight, mark it, announce it, and stop.

    Called once at daemon start, before any scheduler runs. Nothing here resumes a graph:
    resuming is a decision, and a decision needs a person until the graph approval card
    exists (P8)."""
    unfinished = await store.unfinished()
    interrupted: list[Task] = []
    pending: list[Task] = []
    trace = new_id("tr")

    for task in unfinished:
        if task.status is TaskStatus.RUNNING:
            marked = task.with_status(
                TaskStatus.FAILED,
                result=TaskResult(
                    ok=False,
                    summary="interrupted: the supervisor stopped while this task was running",
                    # Whatever it did or did not do is unknown, and unknown is recorded as
                    # unknown. A dependent task will be SKIPPED, which is correct: nothing
                    # downstream should proceed on an unverified result.
                    evidence={"interrupted_at": task.started_at},
                    error=TaskError(
                        kind=INTERRUPTED,
                        message="the daemon exited while this task was running",
                        detail="never auto-restarted; a human decides what happens next",
                        retryable=False,
                    ),
                ),
            )
            await store.save(marked)
            interrupted.append(marked)
            log.warning(
                "graph.recovered_interrupted",
                task_id=task.id,
                root_id=task.root_id,
                kind=str(task.kind),
                started_at=task.started_at,
            )
        else:
            # PENDING, READY or WAITING: nothing ran, so there is nothing to distrust.
            # These are left exactly as they are — reporting them is what lets a person
            # see that a graph is half-finished rather than merely quiet.
            pending.append(task)

    if eventlog is not None and (interrupted or pending):
        await eventlog.append(
            Event(
                type="system.degraded",
                session_id=session_id,
                trace_id=trace,
                actor="system",
                payload={
                    "reason": "task graph recovery",
                    "interrupted": [t.id for t in interrupted],
                    "unstarted": [t.id for t in pending],
                    "action": "no task was restarted; a human decides what happens next",
                },
            )
        )
    log.info(
        "graph.recovery_complete",
        interrupted=len(interrupted),
        unstarted=len(pending),
    )
    return Recovered(interrupted=tuple(interrupted), pending=tuple(pending))
