"""Objective → plan → validated graph → approval, with no vendor in the room.

The planner is replaced by a fake adapter returning recorded plan JSON, because the thing
under test is ORACLE's half: does it ask before spending, does it refuse what it should
refuse, does exactly one repair happen, and does a plan become rows that run.

What a real vendor does with the prompt was measured separately and at length
(`logs/development/2026-08-24-p6t5-antigravity-planning.md`); repeating that here would
buy a slower test and no new information.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from oracle.core.approvals import ApprovalStore
from oracle.core.eventlog import EventLog
from oracle.integrations.types import AgentCaps, AgentResult, Preflight
from oracle.orchestration.models import TaskKind, TaskResult, TaskStatus
from oracle.orchestration.plan import compile_plan
from oracle.orchestration.registry import load_registry
from oracle.orchestration.service import GraphService
from oracle.orchestration.store import TaskStore
from oracle.runners.planning import Planner, approve_graph
from tests.helpers_delegation import make_repo
from tests.test_orchestration_runners import executor_for
from tests.test_plan_validation import raw_plan

REGISTRY = load_registry(Path(__file__).resolve().parents[1] / "config" / "agents.yaml")
PROJECTS = {"oracle", "asterim"}


class ScriptedPlanner:
    """An adapter that returns whatever plan the test wrote, one call at a time.

    Deliberately an `ExternalAgentAdapter` rather than a mock of `Planner`: everything
    between the packet and the parsed plan — the schema, the collection, the structured
    field — is then real."""

    id = "scripted"

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.packets: list[Any] = []

    def capabilities(self) -> AgentCaps:
        return AgentCaps(
            streaming=False,
            resume=False,
            structured_output=True,
            workspace_scoped=True,
            cost_reporting=False,
        )

    async def preflight(self) -> Preflight:
        return Preflight(ok=True, version="scripted")

    async def submit(self, packet: Any, ws: Any) -> Any:
        self.packets.append(packet)
        return object()

    async def events(self, handle: Any) -> Any:
        if False:  # pragma: no cover - an adapter must be async-iterable, not chatty
            yield None

    async def cancel(self, handle: Any) -> None:  # pragma: no cover - never cancelled here
        return None

    async def collect(self, handle: Any) -> AgentResult:
        response = self.responses.pop(0) if self.responses else None
        structured = response if isinstance(response, dict) else None
        text = response if isinstance(response, str) else ""
        return AgentResult(
            success=True,
            exit_code=0,
            result_text=text,
            structured=structured,
        )


async def first_preview(
    approvals: ApprovalStore, eventlog: EventLog, decision: bool
) -> dict[str, Any]:
    """The next approval's preview, plus the tool it was priced under, answered once.

    Always awaited under `wait_for`: a *denied* action requests no approval at all, so a
    bare `async for` over the stream waits for something that will never arrive. The first
    version of these tests did exactly that and hung for the whole pytest timeout instead
    of failing in ten seconds."""
    async for event in eventlog.stream(0):
        if event.type != "approval.requested":
            continue
        approval_id = str(event.payload["approval_id"])
        # Skip anything already answered. `stream(0)` replays the backlog, so a second
        # call would otherwise re-find the *first* request, "answer" it again, and leave
        # the one it was waiting for unanswered - which is precisely how the end-to-end
        # test hung the first time.
        still_open = approvals.get(approval_id)
        if still_open is None or not still_open.open:
            continue
        preview = dict(event.payload["preview"])
        preview["__tool__"] = str(event.payload["tool"])
        await approvals.resolve(approval_id, decision)
        return preview
    raise AssertionError("no approval was requested")  # pragma: no cover


async def approve_next(approvals: ApprovalStore, eventlog: EventLog, decision: bool) -> str:
    """Answer the next approval that appears, once. One subscription, not one per call —
    `wait_for` restarts the stream from seq 0 and would re-read the same request forever."""
    preview = await first_preview(approvals, eventlog, decision)
    return str(preview["__tool__"])


def planner_for(
    tmp_path: Path, eventlog: EventLog, adapter: ScriptedPlanner
) -> tuple[Planner, ApprovalStore]:
    executor = executor_for(tmp_path)
    approvals = ApprovalStore(eventlog, executor, ttl_s=30.0)
    engine = executor._engine  # the same engine the executor was built with
    return (
        Planner(adapter, approvals, engine, REGISTRY, projects=PROJECTS),
        approvals,
    )


# -- asking first --------------------------------------------------------------


async def test_planning_asks_before_it_spends_anything(tmp_path: Path, eventlog: EventLog) -> None:
    """The planning call is an egress and is priced like one. Nothing reaches the adapter
    until somebody says yes."""
    make_repo(tmp_path)
    adapter = ScriptedPlanner(raw_plan())
    planner, approvals = planner_for(tmp_path, eventlog, adapter)

    running = asyncio.create_task(planner.plan("make the auth tests pass", trace_id="tr_1"))
    tool = await asyncio.wait_for(approve_next(approvals, eventlog, True), timeout=10)
    outcome = await asyncio.wait_for(running, timeout=30)

    assert tool == "ai.delegate", "the planning egress was not priced as an egress"
    assert outcome.ok and outcome.attempts == 1
    assert len(adapter.packets) == 1


async def test_a_refused_planning_egress_sends_nothing(tmp_path: Path, eventlog: EventLog) -> None:
    make_repo(tmp_path)
    adapter = ScriptedPlanner(raw_plan())
    planner, approvals = planner_for(tmp_path, eventlog, adapter)

    running = asyncio.create_task(planner.plan("do something", trace_id="tr_1"))
    await asyncio.wait_for(approve_next(approvals, eventlog, False), timeout=10)
    outcome = await asyncio.wait_for(running, timeout=30)

    assert not outcome.ok and outcome.refused
    assert adapter.packets == [], "something egressed after a refusal"


async def test_the_preview_states_the_bound_and_what_is_not_sent(
    tmp_path: Path, eventlog: EventLog
) -> None:
    """A person approving this is approving up to two calls, and is told so. They are
    also told no repo contents ride along — the planner gets an objective and rules."""
    make_repo(tmp_path)
    adapter = ScriptedPlanner(raw_plan())
    planner, approvals = planner_for(tmp_path, eventlog, adapter)

    running = asyncio.create_task(planner.plan("objective text", trace_id="tr_1"))
    preview = await asyncio.wait_for(first_preview(approvals, eventlog, True), timeout=10)
    await asyncio.wait_for(running, timeout=30)

    assert "up to 2" in preview["calls"]
    assert preview["sends_repo_contents"] is False
    assert "objective text" in preview["prompt"]
    # The rules the plan is judged by are in the prompt the person reads.
    assert "at most 12 tasks" in preview["prompt"]


# -- one repair, then the ladder ------------------------------------------------


async def test_an_invalid_plan_gets_exactly_one_repair_attempt(
    tmp_path: Path, eventlog: EventLog
) -> None:
    """The ADR-0017 pattern: told the specific errors, once. A second attempt would be an
    agentic loop wearing a budget's clothes."""
    make_repo(tmp_path)
    broken = raw_plan()
    broken["tasks"] = []  # check 0: the silently-emptied plan the spike actually saw
    adapter = ScriptedPlanner(broken, raw_plan())
    planner, approvals = planner_for(tmp_path, eventlog, adapter)

    running = asyncio.create_task(planner.plan("fix it", trace_id="tr_1"))
    await asyncio.wait_for(approve_next(approvals, eventlog, True), timeout=10)
    outcome = await asyncio.wait_for(running, timeout=30)

    assert outcome.ok and outcome.attempts == 2
    assert len(adapter.packets) == 2
    # The repair told it what was wrong rather than asking again more loudly.
    assert "no tasks" in adapter.packets[1].render_prompt()


async def test_two_invalid_plans_stop_rather_than_looping(
    tmp_path: Path, eventlog: EventLog
) -> None:
    make_repo(tmp_path)
    broken = raw_plan()
    broken["tasks"] = []
    adapter = ScriptedPlanner(broken, broken)
    planner, approvals = planner_for(tmp_path, eventlog, adapter)

    running = asyncio.create_task(planner.plan("fix it", trace_id="tr_1"))
    await asyncio.wait_for(approve_next(approvals, eventlog, True), timeout=10)
    outcome = await asyncio.wait_for(running, timeout=30)

    assert not outcome.ok
    assert outcome.attempts == 2 and len(adapter.packets) == 2
    assert outcome.problems == ["the plan has no tasks"]


async def test_prose_wrapped_json_is_a_failure_not_a_salvage_operation(
    tmp_path: Path, eventlog: EventLog
) -> None:
    make_repo(tmp_path)
    adapter = ScriptedPlanner("Sure! Here is the plan:\n" + json.dumps(raw_plan()), raw_plan())
    planner, approvals = planner_for(tmp_path, eventlog, adapter)

    running = asyncio.create_task(planner.plan("fix it", trace_id="tr_1"))
    await asyncio.wait_for(approve_next(approvals, eventlog, True), timeout=10)
    outcome = await asyncio.wait_for(running, timeout=30)

    # It recovered on the repair attempt — but the first answer was refused, not parsed.
    assert outcome.ok and outcome.attempts == 2


# -- the graph approval card ----------------------------------------------------


async def test_the_card_lists_the_shape_and_authorises_no_egress(
    tmp_path: Path, eventlog: EventLog
) -> None:
    """One decision covering the plan, with every task named. Approving it does not
    pre-approve a single delegation's bytes."""
    make_repo(tmp_path)
    executor = executor_for(tmp_path)
    approvals = ApprovalStore(eventlog, executor, ttl_s=30.0)
    from oracle.orchestration.plan import parse

    plan, _ = parse(raw_plan())
    assert plan is not None
    graph = compile_plan(plan, REGISTRY, root_id="tk_card")

    running = asyncio.create_task(
        approve_graph(approvals, executor._engine, graph, plan, trace_id="tr_1")
    )
    preview = await asyncio.wait_for(first_preview(approvals, eventlog, True), timeout=10)
    tool = str(preview.pop("__tool__"))
    assert await asyncio.wait_for(running, timeout=10) is True

    assert tool == "ai.graph"
    assert len(preview["tasks"]) == 2
    assert [t["egresses"] for t in preview["tasks"]] == [True, False]
    assert preview["risks"], "the planner's stated risks were dropped"
    assert "still asks separately" in preview["note"]


async def test_a_refused_card_means_the_graph_never_runs(
    tmp_path: Path, eventlog: EventLog
) -> None:
    make_repo(tmp_path)
    executor = executor_for(tmp_path)
    approvals = ApprovalStore(eventlog, executor, ttl_s=30.0)
    from oracle.orchestration.plan import parse

    plan, _ = parse(raw_plan())
    assert plan is not None
    graph = compile_plan(plan, REGISTRY, root_id="tk_card")

    running = asyncio.create_task(
        approve_graph(approvals, executor._engine, graph, plan, trace_id="tr_1")
    )
    await asyncio.wait_for(approve_next(approvals, eventlog, False), timeout=10)
    assert await asyncio.wait_for(running, timeout=10) is False


# -- end to end -----------------------------------------------------------------


async def test_an_objective_becomes_a_graph_that_runs(
    tmp_path: Path, eventlog: EventLog, conn: aiosqlite.Connection
) -> None:
    """The whole point of Phase 8, with fake runners standing in for the workers: an
    objective goes out, a plan comes back, a person approves the shape, and rows execute
    in dependency order."""
    make_repo(tmp_path)
    adapter = ScriptedPlanner(raw_plan())
    planner, approvals = planner_for(tmp_path, eventlog, adapter)
    executor = executor_for(tmp_path)

    plan_running = asyncio.create_task(planner.plan("make the auth tests pass", trace_id="tr_1"))
    await asyncio.wait_for(approve_next(approvals, eventlog, True), timeout=10)
    outcome = await asyncio.wait_for(plan_running, timeout=30)
    assert outcome.plan is not None

    graph = compile_plan(outcome.plan, REGISTRY, root_id="tk_e2e", plan_id="pl_1")
    card = asyncio.create_task(
        approve_graph(approvals, executor._engine, graph, outcome.plan, trace_id="tr_1")
    )
    await asyncio.wait_for(approve_next(approvals, eventlog, True), timeout=10)
    assert await asyncio.wait_for(card, timeout=10) is True

    order: list[str] = []

    async def runner(task: Any) -> TaskResult:
        order.append(task.id)
        return TaskResult(ok=True, summary=f"{task.id} done", evidence={"ran": True})

    graphs = GraphService(eventlog, TaskStore(conn))
    status = await asyncio.wait_for(
        graphs.run(graph, dict.fromkeys(TaskKind, runner), trace_id="tr_1"), timeout=60
    )

    assert status is TaskStatus.SUCCEEDED
    assert order == ["tk_e2e-a", "tk_e2e-b"], "the plan's dependency was not respected"
    tree = await graphs.tree("tk_e2e")
    assert tree["status"] == "succeeded"
    assert {t["role"] for t in tree["tasks"]} == {"coder", "reviewer"}
    stored = await TaskStore(conn).load("tk_e2e-a")
    assert stored is not None and stored.plan_id == "pl_1", "the plan's lineage was lost"


@pytest.mark.parametrize("field_name", ["tool", "args"])
async def test_a_plan_naming_a_tool_never_reaches_a_graph(
    tmp_path: Path, eventlog: EventLog, field_name: str
) -> None:
    """The end-to-end form of ADR-0021: rejected at parse, so there is nothing to
    compile, nothing to approve, and nothing to run."""
    make_repo(tmp_path)
    hostile = raw_plan()
    hostile["tasks"][0][field_name] = "fs.write" if field_name == "tool" else {"path": "/etc"}
    adapter = ScriptedPlanner(hostile, hostile)
    planner, approvals = planner_for(tmp_path, eventlog, adapter)

    running = asyncio.create_task(planner.plan("do it", trace_id="tr_1"))
    await asyncio.wait_for(approve_next(approvals, eventlog, True), timeout=10)
    outcome = await asyncio.wait_for(running, timeout=30)

    assert not outcome.ok
    assert any(field_name in p for p in outcome.problems), outcome.problems
