"""`GraphService`: the handle a person reaches a running graph through.

The scheduler already knows how to cancel itself; what it lacked was an address. This
holds the live schedulers by `root_id` so the API can say "stop that one" — the same
shape `TerminalBridge` uses for shells and `DelegationService` for delegations, because
a third pattern for "long-lived thing the UI can poke" would be a third thing to reason
about at three in the morning.

Two properties it does not have, on purpose:

* **It does not build graphs.** A graph arrives already validated; where it came from —
  a fixture, a template, a plan in P8 — is not this object's business.
* **It does not own the runners.** They are passed in per run, so the composition stays
  in one place (the daemon) and this file keeps importing nothing that executes.

Every run is spawned through `AppState.spawn`, which is what makes HALT reach it: HALT
cancels every tracked task, the scheduler's `_abandon()` cancels its children, each
delegation's runner sees the cancellation and kills its vendor process. No new HALT path
exists for graphs, and that is the design working rather than a gap.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from oracle.core.eventlog import EventLog
from oracle.logsink import get_logger
from oracle.orchestration.graph import TaskGraph
from oracle.orchestration.models import TaskKind, TaskStatus
from oracle.orchestration.scheduler import Limits, Replanner, Runner, Scheduler
from oracle.orchestration.store import TaskStore

log = get_logger(__name__)


class GraphService:
    """One instance per daemon. Holds the graphs that are currently running."""

    def __init__(
        self,
        eventlog: EventLog,
        store: TaskStore,
        *,
        limits: Limits | None = None,
        spawn: Callable[[Any], None] | None = None,
    ) -> None:
        self._log = eventlog
        self._store = store
        self._limits = limits or Limits()
        #: The daemon's tracked-task spawner. Without it (tests, scripts) runs are awaited
        #: by the caller instead — but in the daemon this is not optional: an untracked
        #: task is a task HALT cannot reach.
        self._spawn = spawn
        self._running: dict[str, Scheduler] = {}

    # -- running -------------------------------------------------------------

    async def run(
        self,
        graph: TaskGraph,
        runners: dict[TaskKind, Runner],
        *,
        replan: Replanner | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> TaskStatus:
        """Run a graph to completion, holding it addressable while it lasts.

        `replan` is passed through untouched, for the same reason the runners are: this
        object is an address, not an authority, and a service that decided when to replan
        would be a third place the budget lives."""
        root_id = graph.root_id
        if root_id in self._running:
            raise ValueError(f"graph {root_id} is already running")
        scheduler = Scheduler(
            graph,
            runners,
            store=self._store,
            eventlog=self._log,
            limits=self._limits,
            replan=replan,
            session_id=session_id,
            trace_id=trace_id,
        )
        self._running[root_id] = scheduler
        log.info("graph.started", root_id=root_id, tasks=len(graph))
        try:
            return await scheduler.run()
        finally:
            # Whatever happened — finished, cancelled, HALT — the handle goes. A graph
            # left in this dict is one the API would offer to cancel forever.
            self._running.pop(root_id, None)

    def start(
        self,
        graph: TaskGraph,
        runners: dict[TaskKind, Runner],
        *,
        replan: Replanner | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Fire-and-forget, through the daemon's tracked-task spawner."""
        if self._spawn is None:
            raise RuntimeError("this GraphService has no spawner; await run() instead")
        self._spawn(
            self.run(graph, runners, replan=replan, session_id=session_id, trace_id=trace_id)
        )

    # -- reaching it ---------------------------------------------------------

    @property
    def running(self) -> list[str]:
        return list(self._running)

    def get(self, root_id: str) -> Scheduler | None:
        return self._running.get(root_id)

    async def cancel_task(self, root_id: str, task_id: str) -> bool:
        """Stop one task. Its dependents are `SKIPPED`; independent branches carry on,
        which is the whole reason a graph beats a script."""
        scheduler = self._running.get(root_id)
        if scheduler is None or task_id not in scheduler.graph:
            return False
        await scheduler.cancel(task_id)
        log.info("graph.cancel_task", root_id=root_id, task_id=task_id)
        return True

    async def cancel_root(self, root_id: str) -> bool:
        """Stop the whole graph. Not HALT — HALT is above this and stops everything,
        including the graphs this object has never heard of."""
        scheduler = self._running.get(root_id)
        if scheduler is None:
            return False
        await scheduler.cancel_root()
        log.info("graph.cancel_root", root_id=root_id)
        return True

    # -- reading it ----------------------------------------------------------

    async def tree(self, root_id: str) -> dict[str, Any]:
        """The graph as data, read from the table rather than from memory.

        The store is the answer even while a graph is running, because a client asking
        about a *finished* graph must get the same shape as one asking about a live one —
        and because the row is the record (ORCHESTRATION.md §2). The live scheduler adds
        exactly one thing the table cannot know: whether this process is still running it.
        """
        tasks = await self._store.load_graph(root_id)
        scheduler = self._running.get(root_id)
        return {
            "root_id": root_id,
            "live": scheduler is not None,
            "status": str(scheduler.graph.status()) if scheduler else _status_of(tasks),
            "tasks": [
                {
                    "id": task.id,
                    "kind": str(task.kind),
                    "status": str(task.status),
                    "depends_on": list(task.depends_on),
                    "objective": task.spec.objective,
                    "role": task.spec.role,
                    "agent": task.agent,
                    "attempt": task.attempt,
                    # Replanning lineage. `supersedes` names the failed attempt this row
                    # replaces; `parent_id` records where it came from. Neither is ever
                    # rewritten and neither hides the other - the tree shows both rows
                    # (ORCHESTRATION.md §4).
                    "supersedes": task.supersedes,
                    "parent_id": task.parent_id,
                    "plan_id": task.plan_id,
                    "started_at": task.started_at,
                    "finished_at": task.finished_at,
                    # Evidence and claim stay apart all the way to the client: a UI that
                    # renders them in one blob has undone the distinction the whole
                    # verification design rests on.
                    "summary": task.result.summary if task.result else None,
                    "evidence": task.result.evidence if task.result else {},
                    "claim": task.result.claim if task.result else None,
                    "error": task.result.error.model_dump()
                    if task.result and task.result.error
                    else None,
                }
                for task in tasks
            ],
        }


def _status_of(tasks: list[Any]) -> str:
    from oracle.orchestration.models import aggregate

    return str(aggregate([t.status for t in tasks])) if tasks else str(TaskStatus.SUCCEEDED)
