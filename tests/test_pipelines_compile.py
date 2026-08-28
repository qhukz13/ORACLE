"""A pipeline becomes rows — and the rows are indistinguishable from hand-written ones.

The roadmap's extra acceptance criterion for Phase 10 is *"a pipeline run and a
hand-written graph of the same steps produce identical event shapes"*, and it is really a
claim about architecture: if the two differ anywhere, then a pipeline is a second way to
run work, which is exactly what the 2026-08-24 replan removed. It is proved here, against
the compiler alone, before any privileged code exists — so a failure cannot be ambiguous
between the compiler and the approval path.
"""

from __future__ import annotations

from typing import Any

import pytest

from oracle.orchestration.graph import TaskGraph
from oracle.orchestration.models import Task, TaskKind, TaskSpec, TaskStatus
from oracle.orchestration.scheduler import Limits, Scheduler
from oracle.pipelines.compile import PIPELINE_ROLE, compile_pipeline, render
from oracle.pipelines.models import Pipeline
from oracle.pipelines.template import PipelineError

ROOT = "tk_run"


def pipeline(**overrides: Any) -> Pipeline:
    return Pipeline.model_validate(
        {
            "version": 1,
            "name": "check",
            "project": "Asterim",
            "params": {"skip": {"type": "bool", "default": False}},
            "steps": [
                {"id": "status", "tool": "git.status", "with": {"path": "{{ project.root }}"}},
                {"id": "tests", "tool": "dev.run_tests", "with": {"path": "{{ project.root }}"}},
            ],
            **overrides,
        }
    )


def compile_it(pl: Pipeline, params: dict[str, Any] | None = None) -> TaskGraph:
    rendered = render(pl, params or {"skip": False}, project_root="C:/Projects/Asterim")
    return compile_pipeline(rendered, pl, root_id=ROOT)


class TestEveryStepIsAnOrdinaryToolTask:
    def test_kind_is_always_tool(self) -> None:
        """No new `TaskKind` and no new runner. That is what "no second way to run an
        agent" means when you look at the rows instead of the prose."""
        graph = compile_it(pipeline())
        assert {t.kind for t in graph.tasks} == {TaskKind.TOOL}

    def test_the_tool_and_its_resolved_arguments_are_on_the_spec(self) -> None:
        graph = compile_it(pipeline())
        first = graph.tasks[0]
        assert first.spec.tool == "git.status"
        assert first.spec.args == {"path": "C:/Projects/Asterim"}

    def test_the_role_is_one_no_agent_holds(self) -> None:
        """A pipeline step is work ORACLE performs, not work it delegates — so the role
        resolves to no agent and nothing is handed to a vendor."""
        graph = compile_it(pipeline())
        assert {t.spec.role for t in graph.tasks} == {PIPELINE_ROLE}
        assert all(t.agent is None for t in graph.tasks)

    def test_ids_are_namespaced_by_the_root(self) -> None:
        """Two runs of the same pipeline must not be the same rows."""
        graph = compile_it(pipeline())
        assert [t.id for t in graph.tasks] == ["tk_run-status", "tk_run-tests"]


class TestOnFailureIsEdgeConstruction:
    def test_abort_chains(self) -> None:
        graph = compile_it(pipeline())
        assert graph.tasks[0].depends_on == ()
        assert graph.tasks[1].depends_on == ("tk_run-status",)

    def test_continue_is_a_leaf_so_its_failure_reaches_nothing(self) -> None:
        """ "Record and proceed" expressed as a shape rather than as a runtime rule: if
        nothing depends on a step, `_cascade_skips()` has nothing to skip when it fails."""
        pl = pipeline(
            steps=[
                {"id": "a", "tool": "git.status"},
                {"id": "b", "tool": "dev.lint", "on_failure": "continue"},
                {"id": "c", "tool": "dev.build"},
            ]
        )
        graph = compile_it(pl)
        by_id = {t.id: t for t in graph.tasks}
        assert by_id["tk_run-b"].depends_on == ("tk_run-a",)
        # `c` waits on the last *barrier*, which `b` did not become.
        assert by_id["tk_run-c"].depends_on == ("tk_run-a",)
        assert not any("tk_run-b" in t.depends_on for t in graph.tasks)

    async def test_a_continue_step_really_does_not_stop_the_run(self) -> None:
        """The property the shape is for, checked by running it."""
        pl = pipeline(
            steps=[
                {"id": "a", "tool": "git.status"},
                {"id": "b", "tool": "dev.lint", "on_failure": "continue"},
                {"id": "c", "tool": "dev.build"},
            ]
        )
        graph = compile_it(pl)
        from oracle.orchestration.models import TaskError, TaskResult

        async def run(t: Task) -> TaskResult:
            if t.id.endswith("-b"):
                return TaskResult(
                    ok=False,
                    summary="lint failed",
                    error=TaskError(kind="execution_failed", message="boom", retryable=False),
                )
            return TaskResult(ok=True, summary="ok")

        await Scheduler(graph, {TaskKind.TOOL: run}, limits=Limits(tool=4)).run()
        by_id = {t.id: t for t in graph.tasks}
        assert by_id["tk_run-c"].status is TaskStatus.SUCCEEDED
        assert by_id["tk_run-b"].status is TaskStatus.FAILED


class TestWhenOmitsRatherThanSkips:
    def test_a_false_condition_removes_the_step_from_the_graph(self) -> None:
        """`SKIPPED` means "an ancestor failed and this never ran". Reusing it for "the
        author's condition was false" would put a lie in the field the UI reads."""
        pl = pipeline(
            steps=[
                {"id": "a", "tool": "git.status"},
                {"id": "b", "tool": "dev.lint", "when": "params.skip"},
                {"id": "c", "tool": "dev.build"},
            ]
        )
        rendered = render(pl, {"skip": False}, project_root="C:/Projects/Asterim")
        graph = compile_pipeline(rendered, pl, root_id=ROOT)

        assert [t.id for t in graph.tasks] == ["tk_run-a", "tk_run-c"]
        assert rendered.omitted == (("b", "when: params.skip"),)
        assert not any(t.status is TaskStatus.SKIPPED for t in graph.tasks)

    def test_the_neighbours_join_up(self) -> None:
        """An omitted step must not leave a hole its successor waits on forever."""
        pl = pipeline(
            steps=[
                {"id": "a", "tool": "git.status"},
                {"id": "b", "tool": "dev.lint", "when": "params.skip"},
                {"id": "c", "tool": "dev.build"},
            ]
        )
        graph = compile_it(pl, {"skip": False})
        by_id = {t.id: t for t in graph.tasks}
        assert by_id["tk_run-c"].depends_on == ("tk_run-a",)

    def test_a_true_condition_keeps_it(self) -> None:
        pl = pipeline(
            steps=[
                {"id": "a", "tool": "git.status"},
                {"id": "b", "tool": "dev.lint", "when": "params.skip"},
            ]
        )
        graph = compile_it(pl, {"skip": True})
        assert [t.id for t in graph.tasks] == ["tk_run-a", "tk_run-b"]

    def test_a_pipeline_conditioned_out_of_existence_is_refused(self) -> None:
        """Better than compiling an empty graph and reporting a successful run of
        nothing."""
        pl = pipeline(steps=[{"id": "a", "tool": "git.status", "when": "false"}])
        with pytest.raises(PipelineError, match="nothing to run"):
            compile_it(pl)


class TestTimeoutsAndRetries:
    def test_a_step_timeout_becomes_the_tasks_own_ceiling(self) -> None:
        pl = pipeline(steps=[{"id": "a", "tool": "dev.run_tests", "timeout": 600}])
        assert compile_it(pl).tasks[0].timeout_s == 600.0

    def test_no_timeout_leaves_the_kind_default_in_charge(self) -> None:
        assert compile_it(pipeline()).tasks[0].timeout_s is None

    def test_retry_max_is_attempts_minus_one(self) -> None:
        pl = pipeline(steps=[{"id": "a", "tool": "dev.build", "retry": {"max": 2}}])
        assert compile_it(pl).tasks[0].max_attempts == 3


class TestIdenticalToAHandWrittenGraph:
    """The roadmap's extra acceptance criterion.

    If a compiled pipeline and a hand-written graph of the same steps differ in the events
    they produce, then a pipeline *is* a second execution path and the replan's central
    claim is false. Compared on normalised events, because ids and timestamps differ by
    construction and are not what the claim is about.
    """

    def hand_written(self) -> TaskGraph:
        return TaskGraph(
            [
                Task(
                    id="tk_hand-status",
                    root_id="tk_hand",
                    kind=TaskKind.TOOL,
                    spec=TaskSpec(
                        objective="anything",
                        role=PIPELINE_ROLE,
                        project="Asterim",
                        tool="git.status",
                        args={"path": "C:/Projects/Asterim"},
                    ),
                ),
                Task(
                    id="tk_hand-tests",
                    root_id="tk_hand",
                    kind=TaskKind.TOOL,
                    spec=TaskSpec(
                        objective="anything",
                        role=PIPELINE_ROLE,
                        project="Asterim",
                        tool="dev.run_tests",
                        args={"path": "C:/Projects/Asterim"},
                    ),
                    depends_on=("tk_hand-status",),
                ),
            ]
        )

    async def events_of(self, graph: TaskGraph, eventlog: Any) -> list[Any]:
        """Every event the scheduler wrote, normalised.

        Ids, timestamps and sequence numbers differ by construction and are not what the
        claim is about; the event *type*, the position of the task it names, and the
        payload's key set are."""
        from oracle.orchestration.models import TaskResult

        async def run(_t: Task) -> TaskResult:
            return TaskResult(ok=True, summary="ok")

        before = await eventlog.load_head()
        await Scheduler(graph, {TaskKind.TOOL: run}, limits=Limits(tool=4), eventlog=eventlog).run()
        events = await eventlog.read_range(before, await eventlog.load_head())

        position = {t.id: f"#{i}" for i, t in enumerate(graph.tasks)}
        return [
            (
                event.type,
                position.get(event.task_id or "", event.task_id),
                tuple(sorted(k for k in event.payload if k != "root_id")),
                event.payload.get("source"),
                event.payload.get("status"),
            )
            for event in events
        ]

    async def test_the_two_produce_the_same_events_in_the_same_order(self, eventlog: Any) -> None:
        compiled = await self.events_of(compile_it(pipeline()), eventlog)
        hand = await self.events_of(self.hand_written(), eventlog)
        assert compiled == hand
        assert compiled, "a run that emits nothing proves nothing"

    async def test_and_no_event_type_is_unique_to_the_compiled_one(self, eventlog: Any) -> None:
        """`pipeline.started` / `pipeline.finished` are emitted by the service *around*
        the scheduler, so they are outside this comparison by construction. Nothing
        inside it may differ."""
        compiled = {row[0] for row in await self.events_of(compile_it(pipeline()), eventlog)}
        hand = {row[0] for row in await self.events_of(self.hand_written(), eventlog)}
        assert compiled == hand
        assert not any(kind.startswith("pipeline.") for kind in compiled)


class TestCancellingARun:
    """PIPELINES.md §8: *"Cancelling mid-run kills the current step's process tree and
    marks the rest `skipped`."*

    A pipeline is a task graph, so this is `graph.cancel` on the run's `root_id` and
    there is no `pipe.cancel` — a second cancel command would be a second thing to keep
    correct. That the kill reaches a real child process is already proved on a real pid
    by `test_halt_reaches_a_graphs_child_process`; what is asserted here is the half that
    belongs to the compiler's shape: a step that already finished **stays finished**. A
    pipeline is not a transaction and does not pretend to roll back.

    **`CANCELLED`, not `SKIPPED`** — and the spec's wording is the thing that is wrong
    here, not the scheduler. PIPELINES.md §4 says "marks remaining steps `skipped`", which
    predates the status vocabulary P7 settled: `SKIPPED` means *an ancestor failed and
    this never ran*, `CANCELLED` means *a person stopped this*. Collapsing them would lose
    the one distinction a person reading a stopped run actually needs — whether something
    broke or they pressed the button. §4 is corrected.
    """

    async def test_cancelling_skips_the_rest_and_keeps_what_finished(self) -> None:
        import asyncio

        from oracle.orchestration.models import TaskResult
        from oracle.orchestration.service import GraphService

        pl = pipeline(
            steps=[
                {"id": "first", "tool": "git.status"},
                {"id": "slow", "tool": "dev.build"},
                {"id": "after", "tool": "dev.lint"},
            ]
        )
        graph = compile_it(pl)
        started = asyncio.Event()

        async def run(t: Task) -> TaskResult:
            if t.id.endswith("-slow"):
                started.set()
                await asyncio.sleep(30)
            return TaskResult(ok=True, summary="ok")

        service = GraphService(eventlog=None, store=None)  # type: ignore[arg-type]
        running = asyncio.create_task(service.run(graph, {TaskKind.TOOL: run}))
        await asyncio.wait_for(started.wait(), timeout=5)
        await service.cancel_root(graph.root_id)
        await running

        by_id = {t.id: t for t in graph.tasks}
        assert by_id["tk_run-first"].status is TaskStatus.SUCCEEDED, "finished work stays finished"
        assert by_id["tk_run-slow"].status is TaskStatus.CANCELLED
        assert by_id["tk_run-after"].status is TaskStatus.CANCELLED
        assert not any(t.status is TaskStatus.SKIPPED for t in graph.tasks), (
            "nothing failed, so nothing was skipped — SKIPPED would say an ancestor broke"
        )
