"""The reference multi-task scenario, executed — ORCHESTRATION.md §7, Phase 8's criterion.

`test_reference_scenario.py` does this for one turn: classify, run a tool, escalate, ask,
delegate, collect. This does it for a *graph*, and the thing asserted is the same thing —
**the order**, because the ordering is the design:

    objective → planning egress approval → validation → graph approval
              → per-task gating (each delegation's own egress) → verification → report

Five separate tests already prove each link. None of them proves the chain, and a chain of
proven links is exactly the shape of system that turns out not to work end to end. This is
that test, and it is also the regression test for every seam Phase 8 built.

Everything below the vendor is real: a real policy and gate, a real `ApprovalStore`, a
real `DelegationService`, a real git worktree with a real scrub, a real harvest, the real
scheduler, the real task store, the real ladder. The two ends that cost money are
replayed — the planner is an `ExternalAgentAdapter` returning recorded plan JSON, the
worker is the stub CLI replaying output recorded from the real one in P6-T1, and the
reporter is `FakeProvider`. No network, no quota, no clock-watching.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from oracle.core.eventlog import EventLog
from oracle.llm.fake import FakeProvider
from oracle.orchestration.models import Task, TaskKind, TaskStatus
from oracle.orchestration.plan import compile_plan
from oracle.orchestration.registry import load_registry
from oracle.orchestration.service import GraphService
from oracle.orchestration.store import TaskStore
from oracle.orchestration.templates import load_templates
from oracle.runners.delegation import make_delegation_runner
from oracle.runners.planning import Planner, PlanSource, approve_graph, plan_with_ladder
from oracle.runners.report import make_report_runner
from oracle.runners.verify import Counts, make_verify_runner
from tests.helpers_delegation import SMOKE, events_of, make_repo, make_service, stub_adapter
from tests.test_orchestration_runners import FakeBaseline, PlantingAdapter, _returns, report
from tests.test_plan_ladder import PROJECTS, REGISTRY, TEMPLATES
from tests.test_plan_to_graph import ScriptedPlanner
from tests.test_replanning import answer_approvals

ROOT_DOCS = Path(__file__).resolve().parents[1]

#: The §7 shape, one link shorter: one coder, ORACLE's own verification, a local report.
#: Three tasks rather than four because the fourth (a second cloud reviewer) would add a
#: vendor call and no new seam — every seam in the chain is already crossed once here.
REFERENCE_PLAN: dict[str, Any] = {
    "objective": "make the auth tests pass in oracle",
    "summary": "one fix, ORACLE verifies it, then a digest",
    "tasks": [
        {
            "id": "A",
            "role": "coder",
            "objective": "fix the 401 handling",
            "project": "oracle",
            "acceptance": ["the auth tests pass", "nothing outside src/auth changes"],
            "expected_outcome": "diff",
        },
        {
            "id": "B",
            "role": "reviewer",
            "objective": "verify A against the baseline",
            "depends_on": ["A"],
            "expected_outcome": "verdict",
        },
        {
            "id": "C",
            "role": "summarizer",
            "objective": "report what was done",
            "depends_on": ["B"],
            "expected_outcome": "report",
        },
    ],
    "risks": ["the failure may be in the fixture rather than the code"],
}


async def test_the_reference_multi_task_scenario_runs_end_to_end(
    tmp_path: Path,
    eventlog: EventLog,
    conn: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    service, approvals, engine = make_service(
        tmp_path, eventlog, PlantingAdapter(stub_adapter()), ttl_s=90.0
    )
    store = TaskStore(conn)

    #: Every stage that has to happen, in the order it has to happen in. Appended to by
    #: the things that actually do them, never by the test narrating itself.
    stages: list[str] = []

    planner_adapter = ScriptedPlanner(REFERENCE_PLAN)
    planner = Planner(planner_adapter, approvals, engine, REGISTRY, projects=PROJECTS)

    # -- 1. the objective, and the ladder that turns it into a plan ---------------
    approver = asyncio.create_task(answer_approvals(approvals, eventlog, [True, True, True]))

    ladder_running = asyncio.create_task(
        plan_with_ladder(
            planner,
            TEMPLATES,
            REGISTRY,
            PROJECTS,
            "make the auth tests pass in oracle",
            trace_id="tr_ref",
            intent="continue_project",
            project="oracle",
            eventlog=eventlog,
        )
    )
    ladder = await asyncio.wait_for(ladder_running, timeout=60)
    stages.append("planning_egress_approved")
    assert ladder.ok and ladder.source is PlanSource.PLANNER, ladder.problems
    assert ladder.descents == [], "the ladder descended on a plan that was fine"
    assert len(planner_adapter.packets) == 1

    # -- 2. validation, then the shape a person approves --------------------------
    stages.append("validated")
    graph = compile_plan(ladder.plan, REGISTRY, root_id="tk_ref", plan_id="pl_ref")  # type: ignore[arg-type]
    assert [t.kind for t in graph.tasks] == [
        TaskKind.DELEGATION,
        TaskKind.VERIFY,
        # The summarizer is local now, not a delegation (P8-T3).
        TaskKind.REPORT,
    ]

    card = asyncio.create_task(
        approve_graph(
            approvals,
            engine,
            graph,
            ladder.plan,  # type: ignore[arg-type]
            trace_id="tr_ref",
            source=ladder.source,
        )
    )
    assert await asyncio.wait_for(card, timeout=60) is True
    stages.append("graph_approved")

    # -- 3. the runners, all real below the vendor --------------------------------
    baseline = Counts(passed=100, failed=1, total=101, failures=frozenset({"old_flake"}))
    reporter = FakeProvider(["One fix landed and ORACLE re-ran the suite: no new failures."])

    delegation = make_delegation_runner(service, repo, allowed_tools=("Read", "Edit", "Write"))

    async def traced_delegation(task: Task) -> Any:
        stages.append(f"delegation:{task.id}")
        return await delegation(task)

    verify = make_verify_runner(
        store,
        lambda _p: _returns(report(101, 1, ["old_flake"])),
        FakeBaseline(baseline),
    )

    async def traced_verify(task: Task) -> Any:
        result = await verify(task)
        stages.append("verified")
        return result

    reporting = make_report_runner(reporter, store)

    async def traced_report(task: Task) -> Any:
        result = await reporting(task)
        stages.append("reported")
        return result

    graphs = GraphService(eventlog, store)
    status = await asyncio.wait_for(
        graphs.run(
            graph,
            {
                TaskKind.DELEGATION: traced_delegation,
                TaskKind.VERIFY: traced_verify,
                TaskKind.REPORT: traced_report,
            },
            trace_id="tr_ref",
        ),
        timeout=300,
    )
    await asyncio.wait_for(approver, timeout=30)

    # -- the order, which is the assertion ----------------------------------------
    assert status is TaskStatus.SUCCEEDED
    assert stages == [
        "planning_egress_approved",
        "validated",
        "graph_approved",
        "delegation:tk_ref-a",
        "verified",
        "reported",
    ], stages

    # -- and the properties the order exists to protect ---------------------------
    tree = await graphs.tree("tk_ref")
    rows = {row["id"]: row for row in tree["tasks"]}
    assert tree["status"] == "succeeded"

    # Per-task gating: the delegation asked for its own egress, after the card.
    approvals_asked = [e.payload["tool"] for e in await events_of(eventlog, "approval.requested")]
    assert approvals_asked == ["ai.delegate", "ai.graph", "ai.delegate"], approvals_asked

    # Verification is ORACLE's, against a baseline, and it is what gated the report.
    verified = rows["tk_ref-b"]
    assert verified["evidence"]["verified"] == "tk_ref-a"
    assert verified["evidence"]["new_failures"] == []
    assert verified["claim"] is None, "a VERIFY task has no claim to make"

    # The worker's output outlived its worktree, and the row says where.
    worker = rows["tk_ref-a"]
    assert worker["evidence"]["harvest_commit"], "the result did not outlive its workspace"
    assert worker["claim"], "the delegate's own account was dropped rather than kept apart"

    # The report is local: no fourth approval, no fourth egress, and it summarised
    # ORACLE's measurements rather than the worker's account of them.
    reported = rows["tk_ref-c"]
    assert reported["evidence"]["generated_by"] == "local"
    assert "no new failures" in reported["summary"]
    sent = "\n".join(m.content for req in reporter.calls for m in req.messages)
    assert worker["claim"] not in sent
    assert len(planner_adapter.packets) == 1, "the planner was asked again"


async def test_the_same_chain_runs_on_a_template_when_there_is_no_planner(
    tmp_path: Path,
    eventlog: EventLog,
    conn: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ladder's point, end to end: with no planner reachable at all, the same chain
    still runs — one fewer approval, because nothing egressed to plan it."""
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    service, approvals, engine = make_service(
        tmp_path, eventlog, PlantingAdapter(stub_adapter()), ttl_s=90.0
    )
    store = TaskStore(conn)
    templates = load_templates(ROOT_DOCS / "config" / "plan_templates.yaml")

    ladder = await plan_with_ladder(
        None,
        templates,
        load_registry(ROOT_DOCS / "config" / "agents.yaml"),
        PROJECTS,
        "answer whether the auth tests can pass",
        trace_id="tr_tmpl",
        # The read-only shape: research, then report. Nothing in it can produce a diff.
        intent="answer",
        project="oracle",
        eventlog=eventlog,
    )
    assert ladder.ok and ladder.source is PlanSource.TEMPLATE

    graph = compile_plan(ladder.plan, REGISTRY, root_id="tk_tmpl", plan_id="pl_tmpl")  # type: ignore[arg-type]
    assert [t.kind for t in graph.tasks] == [TaskKind.DELEGATION, TaskKind.REPORT]

    # One card and one delegation egress: two questions, both the owner's.
    approver = asyncio.create_task(answer_approvals(approvals, eventlog, [True, True]))
    card = asyncio.create_task(
        approve_graph(
            approvals,
            engine,
            graph,
            ladder.plan,  # type: ignore[arg-type]
            trace_id="tr_tmpl",
            source=ladder.source,
            descents=ladder.descents,
        )
    )
    preview_ok = await asyncio.wait_for(card, timeout=60)
    assert preview_ok is True

    graphs = GraphService(eventlog, store)
    status = await asyncio.wait_for(
        graphs.run(
            graph,
            {
                TaskKind.DELEGATION: make_delegation_runner(service, repo),
                TaskKind.REPORT: make_report_runner(
                    FakeProvider(["nothing conclusive yet"]), store
                ),
            },
            trace_id="tr_tmpl",
        ),
        timeout=300,
    )
    await asyncio.wait_for(approver, timeout=30)

    assert status is TaskStatus.SUCCEEDED
    asked = [e.payload["tool"] for e in await events_of(eventlog, "approval.requested")]
    assert asked == ["ai.graph", "ai.delegate"], asked
    # The descent is on the record, so a graph that ran on rung 2 reads as one afterwards.
    descended = await events_of(eventlog, "plan.descended")
    assert [e.payload["to"] for e in descended] == ["template"]


async def test_the_bottom_rung_runs_too(
    tmp_path: Path,
    eventlog: EventLog,
    conn: aiosqlite.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No planner, no templates: ORACLE still does the work, as one delegation carrying
    the objective unchanged. This is Phase 6's behaviour reached as a **defined state**
    rather than as a crash — and the point of asserting it end to end is that "degrades
    gracefully" is a claim about a path nobody usually walks."""
    from oracle.orchestration.templates import Templates

    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    service, approvals, engine = make_service(
        tmp_path, eventlog, PlantingAdapter(stub_adapter()), ttl_s=90.0
    )
    store = TaskStore(conn)

    ladder = await plan_with_ladder(
        None,
        Templates(problem="config/plan_templates.yaml could not be read"),
        REGISTRY,
        PROJECTS,
        "make the auth tests pass in oracle",
        trace_id="tr_bottom",
        project="oracle",
        eventlog=eventlog,
    )
    assert ladder.source is PlanSource.SINGLE_TASK and ladder.rung == 3

    graph = compile_plan(ladder.plan, REGISTRY, root_id="tk_bottom", plan_id="pl_bottom")  # type: ignore[arg-type]
    approver = asyncio.create_task(answer_approvals(approvals, eventlog, [True, True]))
    card = asyncio.create_task(
        approve_graph(
            approvals,
            engine,
            graph,
            ladder.plan,  # type: ignore[arg-type]
            trace_id="tr_bottom",
            source=ladder.source,
            descents=ladder.descents,
        )
    )
    assert await asyncio.wait_for(card, timeout=60) is True

    graphs = GraphService(eventlog, store)
    status = await asyncio.wait_for(
        graphs.run(
            graph,
            {TaskKind.DELEGATION: make_delegation_runner(service, repo)},
            trace_id="tr_bottom",
        ),
        timeout=300,
    )
    await asyncio.wait_for(approver, timeout=30)

    assert status is TaskStatus.SUCCEEDED
    tree = await graphs.tree("tk_bottom")
    assert len(tree["tasks"]) == 1
    row = tree["tasks"][0]
    assert row["objective"] == "make the auth tests pass in oracle", "the objective was rewritten"
    assert row["evidence"]["harvest_commit"], "the bottom rung lost its own output"
    # Still two questions, still in that order. A degraded rung skips no approval.
    asked = [e.payload["tool"] for e in await events_of(eventlog, "approval.requested")]
    assert asked == ["ai.graph", "ai.delegate"], asked
