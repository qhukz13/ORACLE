"""Replanning: a failure buys one more idea, not an afternoon (ORCHESTRATION.md §4).

Three layers, deliberately separated, because they fail for different reasons:

1. **The decision** (`orchestration/replan.py`) — pure, no I/O. Is there a replan here?
   Every branch of that question has a test, and the one that matters most is the
   shortest: a refusal is not replanned.
2. **The append** (`scheduler.py` + `graph.extend`) — a hook returns rows, the graph
   grows, the failed task keeps its row. The scheduler is driven with a fake replanner
   here, because "does the loop survive a graph that grows underneath it" is a question
   about the loop.
3. **The whole thing** — a scripted planner, a real policy, real approvals, and (for the
   concurrency criterion) real worktrees and the stub CLI.

No vendor appears anywhere. What a real planner does with a failure-carrying prompt was
measured in the P6-T5 spike; repeating it here would buy a slower suite and no new fact.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from oracle.core.approvals import ApprovalStore
from oracle.core.eventlog import EventLog
from oracle.orchestration.graph import MAX_GRAPH_TOTAL, TaskGraph
from oracle.orchestration.models import (
    Task,
    TaskError,
    TaskKind,
    TaskResult,
    TaskSpec,
    TaskStatus,
)
from oracle.orchestration.plan import compile_plan, parse
from oracle.orchestration.registry import load_registry
from oracle.orchestration.replan import (
    REPLAN_BUDGET,
    attach,
    attempts_report,
    budget_used,
    consider,
)
from oracle.orchestration.scheduler import Limits, Scheduler
from oracle.orchestration.service import GraphService
from oracle.orchestration.store import TaskStore
from oracle.runners.planning import Planner, approve_graph, failure_context, make_replanner
from tests.helpers_delegation import make_repo
from tests.test_orchestration_runners import executor_for
from tests.test_plan_to_graph import ScriptedPlanner, approve_next, first_preview
from tests.test_plan_validation import raw_plan

REGISTRY = load_registry(Path(__file__).resolve().parents[1] / "config" / "agents.yaml")
PROJECTS = {"oracle", "asterim"}
ROOT = "tk_root"


# -- fixtures in the small ------------------------------------------------------


def failed_task(
    task_id: str = "tk_root-a",
    *,
    kind: TaskKind = TaskKind.DELEGATION,
    status: TaskStatus = TaskStatus.FAILED,
    error: TaskError | None = None,
    evidence: dict[str, Any] | None = None,
    claim: str | None = None,
    supersedes: str | None = None,
) -> Task:
    return Task(
        id=task_id,
        root_id=ROOT,
        kind=kind,
        status=status,
        supersedes=supersedes,
        spec=TaskSpec(objective="fix the 401 handling", role="coder", project="oracle"),
        result=TaskResult(
            ok=False,
            summary="the delegation failed",
            evidence=evidence if evidence is not None else {"exit_code": 1, "diff_lines": 0},
            claim=claim,
            error=error or TaskError(kind="failed", message="the worker gave up"),
        ),
    )


def two_coder_plan() -> dict:
    """Two independent coding tasks and a third. The concurrency criterion needs a graph
    a *plan* authored, not one a test hand-wrote (P8-T2, requirement 5)."""
    return raw_plan(
        objective="three independent fixes",
        summary="three coders, no dependencies",
        tasks=[
            {
                "id": chr(ord("A") + i),
                "role": "coder",
                "objective": f"independent fix {i}",
                "project": "oracle",
                "acceptance": ["the suite still passes"],
                "expected_outcome": "diff",
            }
            for i in range(3)
        ],
    )


# -- the decision ---------------------------------------------------------------


def test_a_failure_with_budget_produces_a_request_carrying_oracles_evidence() -> None:
    failed = failed_task(evidence={"exit_code": 2, "new_failures": ["tests.test_auth"]})
    request, reason = consider(failed, [failed], objective="make the auth tests pass")

    assert request is not None and reason == ""
    assert request.failed_id == failed.id
    assert request.objective == "make the auth tests pass"
    assert request.failed.evidence["new_failures"] == ["tests.test_auth"]
    assert request.replans_used == 0 and request.attempt_number == 1


@pytest.mark.parametrize(
    "kind", ["refused", "expired", "halted", "denied", "cancelled", "approval_required"]
)
def test_a_human_decision_is_never_replanned_around(kind: str) -> None:
    """The load-bearing test of this whole task. A person said no — or a policy a person
    wrote said no — and a supervisor that replanned would be asking the same question
    with a new face on it."""
    failed = failed_task(error=TaskError(kind=kind, message="the owner declined the egress"))
    request, reason = consider(failed, [failed], objective="do it anyway")

    assert request is None
    assert kind in reason and "decision" in reason


def test_a_planning_task_is_not_answered_with_another_planning_call() -> None:
    failed = failed_task(kind=TaskKind.PLANNING)
    request, reason = consider(failed, [failed], objective="plan it")
    assert request is None and "loop" in reason


def test_a_skipped_task_is_not_itself_a_failure_to_replan() -> None:
    """One event, one replan. Replanning both a failure and each of its skipped
    dependents would spend the budget several times over on the same thing."""
    skipped = failed_task(status=TaskStatus.SKIPPED)
    request, reason = consider(skipped, [skipped], objective="x")
    assert request is None and "skipped" in reason


def test_the_same_failure_is_not_replanned_twice() -> None:
    failed = failed_task()
    replacement = Task(
        id="tk_root-r1-a",
        root_id=ROOT,
        kind=TaskKind.DELEGATION,
        spec=TaskSpec(objective="another approach", role="coder"),
        supersedes=failed.id,
    )
    request, reason = consider(failed, [failed, replacement], objective="x")
    assert request is None and "already been replanned" in reason


def test_the_budget_is_two_per_root_and_counted_from_the_rows() -> None:
    first = failed_task("tk_root-a")
    second = failed_task("tk_root-r1-a", supersedes="tk_root-a")
    third = failed_task("tk_root-r2-a", supersedes="tk_root-r1-a")
    rows = [first, second, third]

    assert budget_used(rows) == REPLAN_BUDGET
    request, reason = consider(third, rows, objective="x")
    assert request is None and "budget" in reason and "2/2" in reason


def test_one_replan_authoring_three_tasks_still_costs_one() -> None:
    """Counted as distinct superseded tasks, not as rows. A planner that answers one bad
    task with a research step and two narrower ones has had one idea, not three."""
    failed = failed_task()
    batch = [
        Task(
            id=f"tk_root-r1-{i}",
            root_id=ROOT,
            kind=TaskKind.DELEGATION,
            spec=TaskSpec(objective=f"step {i}", role="coder"),
            supersedes=failed.id,
        )
        for i in range(3)
    ]
    assert budget_used([failed, *batch]) == 1


def test_a_skipped_dependent_is_named_to_the_planner_but_never_resurrected() -> None:
    failed = failed_task()
    skipped = Task(
        id="tk_root-b",
        root_id=ROOT,
        kind=TaskKind.VERIFY,
        status=TaskStatus.SKIPPED,
        depends_on=(failed.id,),
        spec=TaskSpec(objective="review the change", role="reviewer"),
    )
    request, _ = consider(failed, [failed, skipped], objective="x")

    assert request is not None
    assert [a.task_id for a in request.skipped] == ["tk_root-b"]
    # Naming it is not scheduling it: the row is untouched and stays SKIPPED.
    assert skipped.status is TaskStatus.SKIPPED
    prompt = failure_context(request)
    assert "NEVER RAN" in prompt and "must ask for it again" in prompt


def test_attach_records_lineage_without_rewriting_anything() -> None:
    failed = failed_task()
    fresh = Task(
        id="tk_root-r1-a",
        root_id=ROOT,
        kind=TaskKind.DELEGATION,
        spec=TaskSpec(objective="a narrower fix", role="coder"),
    )
    (stamped,) = attach([fresh], failed=failed, plan_id="pl_2")

    assert stamped.supersedes == failed.id and stamped.parent_id == failed.id
    assert stamped.plan_id == "pl_2"
    assert failed.status is TaskStatus.FAILED and failed.result is not None


def test_the_exhaustion_report_names_where_the_partial_work_went() -> None:
    """An exhausted budget fails the root *with a report of everything tried*. A report
    that does not say where the work went is a report that throws the work away."""
    rows = [
        failed_task("tk_root-a", evidence={"branch": "oracle/tk_root-a", "harvest_commit": "abc"}),
        failed_task("tk_root-r1-a", supersedes="tk_root-a", evidence={"exit_code": 1}),
    ]
    report = attempts_report(rows)

    assert report["replans_used"] == 1 and report["budget"] == REPLAN_BUDGET
    assert len(report["attempts"]) == 2
    assert report["partial_results"] == [
        {
            "task_id": "tk_root-a",
            "branch": "oracle/tk_root-a",
            "workspace": None,
            "harvest_commit": "abc",
        }
    ]


# -- the append -----------------------------------------------------------------


def _row(task_id: str, *deps: str, kind: TaskKind = TaskKind.DELEGATION) -> Task:
    return Task(
        id=task_id,
        root_id=ROOT,
        kind=kind,
        spec=TaskSpec(objective=f"do {task_id}", role="coder"),
        depends_on=tuple(deps),
    )


async def test_a_replan_appends_rows_and_the_failed_task_keeps_its_place(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """Append-only, at the loop's level: the replacement runs, the failure stays failed,
    and both rows are in the table afterwards (ADR-0020)."""
    replacement = _row("tk_root-r1-a")
    asked: list[str] = []

    async def replanner(failed: Task) -> list[Task]:
        asked.append(failed.id)
        return attach([replacement], failed=failed, plan_id="pl_2")

    ran: list[str] = []

    async def runner(task: Task) -> TaskResult:
        ran.append(task.id)
        if task.id == "tk_root-a":
            return TaskResult(
                ok=False,
                summary="misunderstood the objective",
                error=TaskError(kind="failed", message="wrong file"),
            )
        return TaskResult(ok=True, summary="the second approach worked")

    store = TaskStore(conn)
    graph = TaskGraph([_row("tk_root-a")])
    scheduler = Scheduler(
        graph, {TaskKind.DELEGATION: runner}, store=store, eventlog=eventlog, replan=replanner
    )
    status = await asyncio.wait_for(scheduler.run(), timeout=30)

    assert asked == ["tk_root-a"]
    assert ran == ["tk_root-a", "tk_root-r1-a"]
    # The graph's aggregate is still FAILED: a replan does not erase the failure, and
    # `CANCELLED > FAILED > …` precedence is not quietly special-cased for replacements.
    assert status is TaskStatus.FAILED
    rows = {t.id: t for t in await store.load_graph(ROOT)}
    assert rows["tk_root-a"].status is TaskStatus.FAILED
    assert rows["tk_root-r1-a"].status is TaskStatus.SUCCEEDED
    assert rows["tk_root-r1-a"].supersedes == "tk_root-a"


async def test_the_arrival_announces_its_lineage_on_the_wire(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """A client must be able to place a replacement from the first event about it, not
    by re-querying the tree and diffing."""

    async def replanner(failed: Task) -> list[Task]:
        return attach([_row("tk_root-r1-a")], failed=failed, plan_id="pl_2")

    async def runner(task: Task) -> TaskResult:
        ok = task.id != "tk_root-a"
        return TaskResult(
            ok=ok,
            summary="x",
            error=None if ok else TaskError(kind="failed", message="nope"),
        )

    graph = TaskGraph([_row("tk_root-a")])
    await asyncio.wait_for(
        Scheduler(
            graph,
            {TaskKind.DELEGATION: runner},
            store=TaskStore(conn),
            eventlog=eventlog,
            replan=replanner,
        ).run(),
        timeout=30,
    )

    from tests.helpers_delegation import events_of

    created = [e for e in await events_of(eventlog, "task.created") if e.task_id == "tk_root-r1-a"]
    assert len(created) == 1
    assert created[0].payload["supersedes"] == "tk_root-a"
    assert created[0].payload["parent_id"] == "tk_root-a"
    assert created[0].payload["source"] == "graph"


async def test_a_replan_that_would_break_the_graph_is_refused_whole(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """Re-validated like a first plan. A batch that would collide with an existing id is
    rejected entirely — half a replan is a graph nobody designed."""

    async def replanner(failed: Task) -> list[Task]:
        # `tk_root-a` already exists. So does the second one, which is the point: the
        # good row must not slip in beside the bad one.
        return attach([_row("tk_root-r1-a"), _row("tk_root-a")], failed=failed, plan_id="pl_2")

    async def runner(task: Task) -> TaskResult:
        return TaskResult(ok=False, summary="x", error=TaskError(kind="failed", message="nope"))

    store = TaskStore(conn)
    graph = TaskGraph([_row("tk_root-a")])
    status = await asyncio.wait_for(
        Scheduler(
            graph,
            {TaskKind.DELEGATION: runner},
            store=store,
            eventlog=eventlog,
            replan=replanner,
        ).run(),
        timeout=30,
    )

    assert status is TaskStatus.FAILED
    assert [t.id for t in await store.load_graph(ROOT)] == ["tk_root-a"]


async def test_a_replan_that_raises_leaves_the_graph_exactly_as_it_was(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    async def replanner(failed: Task) -> list[Task]:
        raise RuntimeError("the planner exploded")

    async def runner(task: Task) -> TaskResult:
        return TaskResult(ok=False, summary="x", error=TaskError(kind="failed", message="no"))

    graph = TaskGraph([_row("tk_root-a")])
    status = await asyncio.wait_for(
        Scheduler(
            graph,
            {TaskKind.DELEGATION: runner},
            store=TaskStore(conn),
            eventlog=eventlog,
            replan=replanner,
        ).run(),
        timeout=30,
    )
    assert status is TaskStatus.FAILED and len(graph) == 1


async def _settled(eventlog: EventLog, task_id: str) -> None:
    """Wait until the scheduler has *recorded* a task, not merely until its runner
    returned. `task.finished` is emitted after the transition is written, so it is the
    honest signal that the loop came back round rather than that a coroutine ended."""
    async for event in eventlog.stream(0):
        if event.type == "task.finished" and event.task_id == task_id:
            return
    raise AssertionError("the event stream ended")  # pragma: no cover


async def test_a_replan_does_not_stall_the_other_delegations(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """A replan is a vendor call and a human decision. If the loop did it inline, a graph
    would go silent for the length of an approval and its concurrency limit would be a
    lie — so the slow replan below must not delay the sibling that is still running."""
    release = asyncio.Event()
    finished_sibling = asyncio.Event()

    async def replanner(failed: Task) -> list[Task]:
        await release.wait()
        return []

    async def runner(task: Task) -> TaskResult:
        if task.id == "tk_root-a":
            return TaskResult(ok=False, summary="x", error=TaskError(kind="failed", message="no"))
        await asyncio.sleep(0.05)
        finished_sibling.set()
        return TaskResult(ok=True, summary="the sibling got on with it")

    graph = TaskGraph([_row("tk_root-a"), _row("tk_root-b")])
    scheduler = Scheduler(
        graph,
        {TaskKind.DELEGATION: runner},
        store=TaskStore(conn),
        eventlog=eventlog,
        replan=replanner,
    )
    running = asyncio.create_task(scheduler.run())
    # The sibling runs to completion and is *recorded* while the replan is still parked
    # on `release`. Recorded, not merely finished: the loop has to have come back round.
    await asyncio.wait_for(finished_sibling.wait(), timeout=10)
    await asyncio.wait_for(_settled(eventlog, "tk_root-b"), timeout=10)
    assert graph["tk_root-b"].status is TaskStatus.SUCCEEDED
    assert not release.is_set(), "the replan finished before the sibling did"
    release.set()
    await asyncio.wait_for(running, timeout=30)


async def test_cancelling_a_task_cancels_the_replan_it_triggered(
    conn: aiosqlite.Connection, eventlog: EventLog
) -> None:
    """Somebody stopped this. Authoring its replacement anyway would be the supervisor
    arguing with them."""
    started = asyncio.Event()
    produced = False

    async def replanner(failed: Task) -> list[Task]:
        nonlocal produced
        started.set()
        await asyncio.sleep(5)
        produced = True
        return attach([_row("tk_root-r1-a")], failed=failed, plan_id="pl_2")

    async def runner(task: Task) -> TaskResult:
        return TaskResult(ok=False, summary="x", error=TaskError(kind="failed", message="no"))

    graph = TaskGraph([_row("tk_root-a")])
    scheduler = Scheduler(
        graph,
        {TaskKind.DELEGATION: runner},
        store=TaskStore(conn),
        eventlog=eventlog,
        replan=replanner,
    )
    running = asyncio.create_task(scheduler.run())
    await asyncio.wait_for(started.wait(), timeout=10)
    await scheduler.cancel("tk_root-a")
    await asyncio.wait_for(running, timeout=30)

    assert not produced
    assert len(graph) == 1


def test_the_graph_ceiling_is_stated_rather_than_multiplied_in_anyones_head() -> None:
    """A replan may grow a graph; it may not grow it forever. The ceiling is what the
    per-plan cap and the replan budget already imply."""
    graph = TaskGraph([_row("tk_root-a")])
    from oracle.orchestration.graph import GraphError

    with pytest.raises(GraphError, match="exceeds the graph limit"):
        graph.extend([_row(f"tk_root-x{i}") for i in range(MAX_GRAPH_TOTAL)])
    assert len(graph) == 1


# -- the whole thing ------------------------------------------------------------


def planner_for(
    tmp_path: Path, eventlog: EventLog, adapter: ScriptedPlanner
) -> tuple[Planner, ApprovalStore, Any]:
    executor = executor_for(tmp_path)
    approvals = ApprovalStore(eventlog, executor, ttl_s=60.0)
    return (
        Planner(adapter, approvals, executor._engine, REGISTRY, projects=PROJECTS),
        approvals,
        executor._engine,
    )


async def answer_approvals(
    approvals: ApprovalStore, eventlog: EventLog, decisions: list[bool]
) -> list[dict[str, Any]]:
    """Answer the next N approvals in the order they are asked, from **one**
    subscription. `stream(0)` replays the backlog, so a helper called in a loop re-reads
    the first request forever while the others expire — the trap this project has now
    sprung four times."""
    previews: list[dict[str, Any]] = []
    seen: set[str] = set()
    async for event in eventlog.stream(0):
        if event.type != "approval.requested":
            continue
        approval_id = str(event.payload["approval_id"])
        if approval_id in seen:
            continue
        seen.add(approval_id)
        preview = dict(event.payload["preview"])
        preview["__tool__"] = str(event.payload["tool"])
        previews.append(preview)
        await approvals.resolve(approval_id, decisions[len(previews) - 1])
        if len(previews) == len(decisions):
            return previews
    raise AssertionError("the event stream ended")  # pragma: no cover


async def test_one_failure_produces_exactly_one_planning_call_carrying_the_evidence(
    tmp_path: Path, eventlog: EventLog, conn: aiosqlite.Connection
) -> None:
    """The acceptance criterion, end to end: one call, ORACLE's evidence in the prompt,
    and rows that arrive with `supersedes` set."""
    make_repo(tmp_path)
    adapter = ScriptedPlanner(raw_plan())
    planner, approvals, engine = planner_for(tmp_path, eventlog, adapter)
    store = TaskStore(conn)

    failed = failed_task(evidence={"exit_code": 2, "new_failures": ["tests.test_auth"]})
    await store.save(failed)

    replan = make_replanner(
        planner,
        approvals,
        engine,
        REGISTRY,
        lambda: store.load_graph(ROOT),
        objective="make the auth tests pass",
        trace_id="tr_1",
    )
    running = asyncio.create_task(replan(failed))
    previews = await asyncio.wait_for(
        answer_approvals(approvals, eventlog, [True, True]), timeout=20
    )
    added = await asyncio.wait_for(running, timeout=30)

    assert len(adapter.packets) == 1, "a replan cost more than one planning call"
    prompt = adapter.packets[0].render_prompt()
    assert "This is a REPLAN" in prompt
    assert "new_failures" in prompt and "tests.test_auth" in prompt
    assert "make the auth tests pass" in prompt

    assert previews[0]["__tool__"] == "ai.delegate" and previews[0]["purpose"] == "replanning"
    assert previews[0]["replaces"] == failed.id
    assert previews[0]["replan"].startswith("1 of 2")

    assert [t.supersedes for t in added] == [failed.id, failed.id]
    assert [t.parent_id for t in added] == [failed.id, failed.id]
    assert all(t.root_id == ROOT for t in added)
    # A new namespace, so nothing collides with the graph it is joining.
    assert [t.id for t in added] == ["tk_root-r1-a", "tk_root-r1-b"]


async def test_the_card_shows_the_additions_as_additions(
    tmp_path: Path, eventlog: EventLog, conn: aiosqlite.Connection
) -> None:
    """A replan is new work and gets a new decision — on the same card, showing what is
    *added* rather than re-rendering the whole graph. Re-showing everything is how a
    person is trained to click through without reading."""
    make_repo(tmp_path)
    adapter = ScriptedPlanner(raw_plan())
    planner, approvals, engine = planner_for(tmp_path, eventlog, adapter)
    store = TaskStore(conn)
    failed = failed_task()
    await store.save(failed)
    # A sibling that succeeded: it must not appear on the replan's card.
    await store.save(
        Task(
            id="tk_root-z",
            root_id=ROOT,
            kind=TaskKind.DELEGATION,
            status=TaskStatus.SUCCEEDED,
            spec=TaskSpec(objective="something that worked", role="coder"),
        )
    )

    replan = make_replanner(
        planner,
        approvals,
        engine,
        REGISTRY,
        lambda: store.load_graph(ROOT),
        objective="x",
        trace_id="tr_1",
    )
    running = asyncio.create_task(replan(failed))
    previews = await asyncio.wait_for(
        answer_approvals(approvals, eventlog, [True, True]), timeout=20
    )
    await asyncio.wait_for(running, timeout=30)

    card = previews[1]
    assert card["__tool__"] == "ai.graph", "a replan invented a new approval type"
    assert card["addition"] is True and card["replaces"] == failed.id
    assert [t["task_id"] for t in card["tasks"]] == ["tk_root-r1-a", "tk_root-r1-b"]
    assert "stays failed and is not re-run" in card["note"]


async def test_a_refused_additions_card_adds_nothing(
    tmp_path: Path, eventlog: EventLog, conn: aiosqlite.Connection
) -> None:
    make_repo(tmp_path)
    adapter = ScriptedPlanner(raw_plan())
    planner, approvals, engine = planner_for(tmp_path, eventlog, adapter)
    store = TaskStore(conn)
    failed = failed_task()
    await store.save(failed)

    replan = make_replanner(
        planner,
        approvals,
        engine,
        REGISTRY,
        lambda: store.load_graph(ROOT),
        objective="x",
        trace_id="tr_1",
    )
    running = asyncio.create_task(replan(failed))
    await asyncio.wait_for(answer_approvals(approvals, eventlog, [True, False]), timeout=20)
    assert await asyncio.wait_for(running, timeout=30) == []


async def test_a_graph_that_keeps_failing_stops_after_two_replans(
    tmp_path: Path, eventlog: EventLog, conn: aiosqlite.Connection
) -> None:
    """The budget, driven rather than asserted: a graph whose every task fails, with a
    planner that always answers. It must stop at two, report what was tried, and leave
    every attempt readable in the tree."""
    make_repo(tmp_path)
    # Four plans offered; only two may ever be asked for.
    adapter = ScriptedPlanner(*[single_task_plan(i) for i in range(4)])
    planner, approvals, engine = planner_for(tmp_path, eventlog, adapter)
    store = TaskStore(conn)

    exhausted: list[dict[str, Any]] = []

    async def on_exhausted(report: dict[str, Any]) -> None:
        exhausted.append(report)

    replan = make_replanner(
        planner,
        approvals,
        engine,
        REGISTRY,
        lambda: store.load_graph(ROOT),
        objective="something that cannot be done",
        trace_id="tr_1",
        on_exhausted=on_exhausted,
    )

    async def runner(task: Task) -> TaskResult:
        return TaskResult(
            ok=False,
            summary=f"{task.id} failed too",
            evidence={"exit_code": 1, "branch": f"oracle/{task.id}"},
            error=TaskError(kind="failed", message="still wrong"),
        )

    graph = TaskGraph([_row("tk_root-a")])
    scheduler = Scheduler(
        graph,
        {TaskKind.DELEGATION: runner},
        store=store,
        eventlog=eventlog,
        replan=replan,
    )
    # Two replans, two approvals each. A third replan is never asked for, so a third
    # pair of answers would hang — which is exactly the assertion.
    approver = asyncio.create_task(answer_approvals(approvals, eventlog, [True] * 4))
    status = await asyncio.wait_for(scheduler.run(), timeout=90)
    await asyncio.wait_for(approver, timeout=10)

    assert status is TaskStatus.FAILED
    assert len(adapter.packets) == REPLAN_BUDGET, "the budget was a suggestion"
    rows = await store.load_graph(ROOT)
    assert len(rows) == 1 + REPLAN_BUDGET
    # Every attempt is still readable, and the lineage is a chain rather than a tangle.
    assert sorted(t.id for t in rows) == ["tk_root-a", "tk_root-r1-a", "tk_root-r2-a"]
    assert {t.supersedes for t in rows} == {None, "tk_root-a", "tk_root-r1-a"}
    assert all(t.status is TaskStatus.FAILED for t in rows)

    assert len(exhausted) == 1, "the budget ran out without saying so"
    report = exhausted[0]
    assert report["replans_used"] == REPLAN_BUDGET
    assert len(report["attempts"]) == 3
    assert {p["branch"] for p in report["partial_results"]} == {
        "oracle/tk_root-a",
        "oracle/tk_root-r1-a",
        "oracle/tk_root-r2-a",
    }


def single_task_plan(index: int) -> dict:
    return raw_plan(
        objective="something that cannot be done",
        summary=f"attempt {index}",
        tasks=[
            {
                "id": "A",
                "role": "coder",
                "objective": f"try approach {index}",
                "project": "oracle",
                "acceptance": ["the suite still passes"],
                "expected_outcome": "diff",
            }
        ],
    )


# -- two workers, for real ------------------------------------------------------


async def test_two_delegations_from_one_plan_run_concurrently_and_the_third_queues(
    tmp_path: Path,
    eventlog: EventLog,
    conn: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P7-T2 proved the limit with a hand-written graph. This proves it with a graph a
    *plan* authored: three independent coder tasks compiled from plan JSON, two running
    at once in separate worktrees, each harvested to its own branch, the third queued."""
    from tests.helpers_delegation import SMOKE, make_service, stub_adapter
    from tests.test_orchestration_runners import PlantingAdapter

    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    # The stub replays a recorded stream and writes nothing, so a test about *harvesting*
    # has to put a worker's output where a worker would have put it.
    service, approvals, engine = make_service(
        tmp_path, eventlog, PlantingAdapter(stub_adapter()), ttl_s=90.0
    )

    plan, problems = parse(two_coder_plan())
    assert plan is not None, problems
    graph = compile_plan(plan, REGISTRY, root_id="tk_conc", plan_id="pl_conc")
    assert len(graph) == 3

    from oracle.runners.delegation import make_delegation_runner

    inner = make_delegation_runner(service, repo, allowed_tools=("Read", "Edit", "Write"))
    live = 0
    peak = 0

    async def counted(task: Task) -> Any:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            return await inner(task)
        finally:
            live -= 1

    graphs = GraphService(eventlog, TaskStore(conn), limits=Limits(delegation=2))
    # One card for the graph, then one egress approval per delegation.
    approver = asyncio.create_task(answer_approvals(approvals, eventlog, [True] * 4))
    card = asyncio.create_task(approve_graph(approvals, engine, graph, plan, trace_id="tr_conc"))
    running = asyncio.create_task(
        graphs.run(graph, {TaskKind.DELEGATION: counted}, trace_id="tr_conc")
    )
    assert await asyncio.wait_for(card, timeout=60) is True
    status = await asyncio.wait_for(running, timeout=300)
    await asyncio.wait_for(approver, timeout=30)

    assert status is TaskStatus.SUCCEEDED
    assert peak == 2, f"the delegation limit did not hold (peak {peak})"
    evidence = [graph[t.id].result.evidence for t in graph.tasks]  # type: ignore[union-attr]
    assert len({e["workspace"] for e in evidence}) == 3, "delegations shared a workspace"
    assert len({e["branch"] for e in evidence}) == 3, "delegations shared a branch"
    commits = {e.get("harvest_commit") for e in evidence}
    assert all(commits), "a result did not outlive its worktree"
    assert len(commits) == 3, "two delegations harvested onto the same commit"


async def test_approving_the_graph_first_does_not_pre_approve_a_replans_additions(
    tmp_path: Path, eventlog: EventLog, conn: aiosqlite.Connection
) -> None:
    """The graph card authorises the graph that was shown. Rows that did not exist when
    it was approved are a second decision, or they are not authorised at all."""
    make_repo(tmp_path)
    adapter = ScriptedPlanner(raw_plan())
    planner, approvals, engine = planner_for(tmp_path, eventlog, adapter)
    store = TaskStore(conn)

    plan, _ = parse(raw_plan())
    assert plan is not None
    graph = compile_plan(plan, REGISTRY, root_id=ROOT, plan_id="pl_1")
    card = asyncio.create_task(approve_graph(approvals, engine, graph, plan, trace_id="tr_1"))
    await asyncio.wait_for(first_preview(approvals, eventlog, True), timeout=20)
    assert await asyncio.wait_for(card, timeout=20) is True

    failed = failed_task(task_id=graph.tasks[0].id)
    await store.save(failed)
    replan = make_replanner(
        planner,
        approvals,
        engine,
        REGISTRY,
        lambda: store.load_graph(ROOT),
        objective="x",
        trace_id="tr_1",
    )
    running = asyncio.create_task(replan(failed))
    # Two fresh questions, both asked after the graph card was already answered.
    previews = await asyncio.wait_for(
        answer_approvals(approvals, eventlog, [True, True, True]), timeout=30
    )
    assert await asyncio.wait_for(running, timeout=30)
    assert [p["__tool__"] for p in previews] == ["ai.graph", "ai.delegate", "ai.graph"]
    assert previews[0].get("addition") is False and previews[2]["addition"] is True


async def test_approve_next_is_still_the_shape_the_other_suites_use(
    tmp_path: Path, eventlog: EventLog
) -> None:
    """A guard on the shared helper rather than on this feature: `approve_next` skips
    approvals that are no longer open, and these suites now lean on that harder than
    ever."""
    make_repo(tmp_path)
    adapter = ScriptedPlanner(raw_plan())
    planner, approvals, _engine = planner_for(tmp_path, eventlog, adapter)
    running = asyncio.create_task(planner.plan("objective", trace_id="tr_1"))
    assert await asyncio.wait_for(approve_next(approvals, eventlog, True), timeout=20) == (
        "ai.delegate"
    )
    assert (await asyncio.wait_for(running, timeout=30)).ok
