"""The graph algebra (ORCHESTRATION.md §3), ported from Asterim's `pipeline.ts`.

Pure functions over a set of tasks: validation, the ready set, the skip cascade, the
aggregate status. No I/O, no scheduling, no clock — which is what makes the scheduler's
correctness testable without running anything.

Two details are ports rather than reinventions, and both were paid for once already
(ASTERIM_REUSE.md Tier 1):

* **The cycle is reported as a path.** "There is a cycle" is not an error message anyone
  can act on; `a → b → c → a` is. The walk is iterative because the input may come from a
  planner, and a planner is an untrusted source that can hand us a 12-deep chain or an
  adversarial one (ADR-0021).
* **Ready = PENDING ∧ every dependency SUCCEEDED.** Not "not failed" — *succeeded*. The
  graph is then fail-closed with no special-casing anywhere: one failure and everything
  downstream stops being eligible, which is also exactly what makes the skip cascade a
  consequence rather than a feature.
"""

from __future__ import annotations

from collections.abc import Iterable

from oracle.orchestration.models import Task, TaskStatus, aggregate

#: ORCHESTRATION.md §3: a plan larger than this is a planner losing the thread, and the
#: cap is the same instinct as the old 8-step pipeline limit.
MAX_GRAPH_SIZE = 12


class GraphError(ValueError):
    """Invalid graph. Carries the offending path when the problem is a cycle, because
    the message is the whole point of detecting it."""

    def __init__(self, message: str, *, cycle: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.cycle = cycle


def find_cycle(tasks: Iterable[Task]) -> tuple[str, ...]:
    """The cycle as a path (`a → b → c → a`), or `()`. Iterative DFS with an explicit
    stack: recursion here would put an untrusted graph in charge of our stack depth."""
    edges = {task.id: list(task.depends_on) for task in tasks}
    #: 0 = unseen, 1 = on the current path, 2 = finished.
    colour: dict[str, int] = {}
    for start in edges:
        if colour.get(start, 0) == 2:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, index = stack.pop()
            if index == 0:
                if colour.get(node, 0) == 2:
                    continue
                colour[node] = 1
                path.append(node)
            if index < len(edges.get(node, ())):
                stack.append((node, index + 1))
                nxt = edges[node][index]
                if colour.get(nxt, 0) == 1:
                    # `nxt` is on the current path: everything from it to here is the cycle.
                    return (*path[path.index(nxt) :], nxt)
                if colour.get(nxt, 0) == 0 and nxt in edges:
                    stack.append((nxt, 0))
            else:
                colour[node] = 2
                if path and path[-1] == node:
                    path.pop()
    return ()


def validate(tasks: list[Task]) -> None:
    """Raises `GraphError` on the first structural problem. Order matters: a duplicate id
    makes every later check meaningless, and a dangling dependency makes the cycle walk
    lie about which edges exist."""
    if not tasks:
        raise GraphError("the graph has no tasks")
    ids = [task.id for task in tasks]
    if len(ids) != len(set(ids)):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise GraphError(f"duplicate task ids: {', '.join(duplicates)}")
    if len(tasks) > MAX_GRAPH_SIZE:
        raise GraphError(f"{len(tasks)} tasks exceeds the graph limit of {MAX_GRAPH_SIZE}")
    known = set(ids)
    for task in tasks:
        for dep in task.depends_on:
            if dep not in known:
                raise GraphError(f"{task.id} depends on unknown task {dep!r}")
        if task.id in task.depends_on:
            raise GraphError(f"{task.id} depends on itself", cycle=(task.id, task.id))
    roots = {task.root_id for task in tasks}
    if len(roots) != 1:
        raise GraphError(f"tasks span {len(roots)} roots: {', '.join(sorted(roots))}")
    cycle = find_cycle(tasks)
    if cycle:
        raise GraphError("cycle: " + " → ".join(cycle), cycle=cycle)


class TaskGraph:
    """A validated set of tasks, addressable by id. Immutable in shape — tasks are
    replaced as they transition, never added — because adding is *replanning*, which is
    P8's append-only operation and needs re-validation of its own."""

    def __init__(self, tasks: list[Task]) -> None:
        validate(tasks)
        self._tasks: dict[str, Task] = {task.id: task for task in tasks}
        self.root_id = tasks[0].root_id

    def __len__(self) -> int:
        return len(self._tasks)

    def __contains__(self, task_id: object) -> bool:
        return task_id in self._tasks

    def __getitem__(self, task_id: str) -> Task:
        return self._tasks[task_id]

    @property
    def tasks(self) -> list[Task]:
        return list(self._tasks.values())

    def replace(self, task: Task) -> None:
        if task.id not in self._tasks:
            raise GraphError(f"{task.id} is not in this graph")
        self._tasks[task.id] = task

    # -- the algebra ---------------------------------------------------------

    def ready(self) -> list[Task]:
        """PENDING tasks whose every dependency SUCCEEDED, in insertion order so a
        scheduler's dispatch is reproducible across runs."""
        return [
            task
            for task in self._tasks.values()
            if task.status is TaskStatus.PENDING
            and all(self._tasks[dep].status is TaskStatus.SUCCEEDED for dep in task.depends_on)
        ]

    def blocked(self) -> list[Task]:
        """PENDING tasks that can never become ready: some dependency reached a terminal
        state that was not success. These are the skip cascade's input."""
        return [
            task
            for task in self._tasks.values()
            if task.status is TaskStatus.PENDING
            and any(
                self._tasks[dep].terminal and self._tasks[dep].status is not TaskStatus.SUCCEEDED
                for dep in task.depends_on
            )
        ]

    def dependents(self, task_id: str) -> list[Task]:
        return [task for task in self._tasks.values() if task_id in task.depends_on]

    def active(self) -> list[Task]:
        return [task for task in self._tasks.values() if not task.terminal]

    def done(self) -> bool:
        return not self.active()

    def status(self) -> TaskStatus:
        return aggregate([task.status for task in self._tasks.values()])
