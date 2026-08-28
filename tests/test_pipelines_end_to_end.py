"""A pipeline, from a YAML file to a run record — through the real gate.

PIPELINES.md §8's acceptance criteria, with nothing faked below the pipeline itself: a
real `ToolExecutor`, a real `PolicyEngine`, a real `Scheduler`, a real `TaskStore`. The
only stand-in is the person, and only where the criterion is about what they were asked.

The one that matters most is AC3 — *"a run containing a T2 step asks exactly once, before
starting"* — because it is where Phase 10 is most exposed. A card that authorises several
actions at once is one keystroke away from a card nobody reads, so the assertion is not
"an approval happened" but **one, before any task existed, listing the step by name.**
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from oracle.core.approvals import ApprovalStore
from oracle.core.eventlog import EventLog
from oracle.orchestration.models import TaskKind, TaskStatus
from oracle.orchestration.scheduler import Limits
from oracle.orchestration.service import GraphService
from oracle.orchestration.store import TaskStore
from oracle.pipelines.loader import Source, load_file
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.runners.pipeline import PipelineService
from oracle.runners.tool import make_tool_runner
from oracle.storage.db import connect, migrate
from oracle.tools import ToolExecutor, build_registry

POLICY = """
version: 1
default_decision: deny
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
programs:
  uv:
    subcommands:
      allow:   [run, sync]
tools:
  fs.read:     {{ tier: T0, scopes: [projects] }}
  fs.list:     {{ tier: T0, scopes: [projects] }}
  git.status:  {{ tier: T0, scopes: [projects] }}
  dev.execute: {{ tier: T2, scopes: [projects] }}
  pipe.run:    {{ tier: T0 }}   # a FLOOR; declared_tier raises it to max(step)
"""

READ_ONLY = """\
version: 1
name: look
project: Asterim
steps:
  - id: one
    tool: fs.list
    with: { path: "{{ project.root }}" }
  - id: two
    tool: fs.read
    with: { path: "{{ project.root }}/a.txt" }
"""

WITH_A_T2 = """\
version: 1
name: build
project: Asterim
steps:
  - id: look
    tool: fs.list
    with: { path: "{{ project.root }}" }
  - id: run
    tool: dev.execute
    with:
      path: "{{ project.root }}"
      program: uv
      args: ["run", "true"]
"""


class Harness:
    """Everything the daemon builds, built once for a test."""

    def __init__(self, conn: aiosqlite.Connection, root: Path, tmp: Path) -> None:
        policy = tmp / "policy.yaml"
        policy.write_text(POLICY.format(root=root.as_posix()), encoding="utf-8")
        self.root = root
        self.eventlog = EventLog(conn)
        self.executor = ToolExecutor(
            build_registry(), PolicyEngine(load_policy(policy)), AuditLog(tmp / "audit.jsonl")
        )
        self.approvals = ApprovalStore(self.eventlog, self.executor)
        self.store = TaskStore(conn)
        self.graphs = GraphService(self.eventlog, self.store, limits=Limits(tool=2))
        self.service = PipelineService(
            self.graphs,
            self.executor,
            self.approvals,
            self.executor.policy,
            projects_root=root.parent,
        )

    def runners_for(self, granted: dict[str, str]) -> dict[TaskKind, Any]:
        return {TaskKind.TOOL: make_tool_runner(self.executor, pre_granted=granted)}


@pytest.fixture
async def harness(tmp_path: Path) -> Any:
    root = tmp_path / "projects" / "Asterim"
    root.mkdir(parents=True)
    (root / "a.txt").write_text("hello", encoding="utf-8")
    conn = await connect(tmp_path / "oracle.db")
    await migrate(conn)
    log = EventLog(conn)
    await log.load_head()
    try:
        yield Harness(conn, root, tmp_path)
    finally:
        await conn.close()


def write(tmp: Path, text: str) -> Any:
    path = tmp / "p.yaml"
    path.write_text(text, encoding="utf-8")
    loaded, problems = load_file(path, source=Source.GLOBAL)
    assert loaded is not None, problems
    return loaded


class TestARunReportsEveryStep:
    async def test_a_read_only_pipeline_runs_and_reports(
        self, harness: Harness, tmp_path: Path
    ) -> None:
        """AC1. Also AC3's other half: a run of nothing but T0 steps **asks nobody**,
        which is what pricing the card as `max(tier(step))` is for."""
        await harness.eventlog.load_head()
        before = await harness.eventlog.load_head()

        record = await harness.service.run(
            write(tmp_path, READ_ONLY), {}, runners_for=harness.runners_for
        )

        assert record["status"] == str(TaskStatus.SUCCEEDED), record
        assert [s["step"] for s in record["steps"]] == ["one", "two"]
        assert all(s["status"] == str(TaskStatus.SUCCEEDED) for s in record["steps"])
        # Every step carries the rule that allowed it, not just the fact that it ran.
        assert all(s["rule"] for s in record["steps"])

        events = await harness.eventlog.read_range(before, await harness.eventlog.load_head())
        assert not [e for e in events if e.type == "approval.requested"]


class TestItAsksOnceBeforeStarting:
    async def test_a_t2_step_asks_exactly_once_and_before_any_task_exists(
        self, harness: Harness, tmp_path: Path
    ) -> None:
        """AC3, in the strong form. One request, and its `seq` is lower than the first
        `task.created` — so the person decided before anything was scheduled, not after
        the first step had already run."""
        before = await harness.eventlog.load_head()
        loaded = write(tmp_path, WITH_A_T2)

        async def answer() -> None:
            for _ in range(200):
                pending = list(harness.approvals._pending.values())
                if pending:
                    await harness.approvals.resolve(pending[0].id, True)
                    return
                await asyncio.sleep(0.01)

        answering = asyncio.create_task(answer())
        record = await harness.service.run(loaded, {}, runners_for=harness.runners_for)
        await answering

        events = await harness.eventlog.read_range(before, await harness.eventlog.load_head())
        asked = [e for e in events if e.type == "approval.requested"]
        created = [e for e in events if e.type == "task.created"]

        assert len(asked) == 1, f"expected one card, got {[e.payload for e in asked]}"
        assert asked[0].payload["tool"] == "pipe.run"
        assert created, "the run must have created tasks"
        assert asked[0].seq < created[0].seq, "the card must precede the graph"

        # The card names the step, its tool and its *resolved* arguments — SECURITY.md §2
        # rule 5: confirm actions, not intentions.
        steps = asked[0].payload["preview"]["steps"]
        elevated = [s for s in steps if s["asks"]]
        assert [s["step"] for s in elevated] == ["run"]
        assert elevated[0]["tool"] == "dev.execute"
        assert elevated[0]["args"]["program"] == "uv"
        assert elevated[0]["tier"] == "T2"
        assert record["status"] in {str(TaskStatus.SUCCEEDED), str(TaskStatus.FAILED)}

    async def test_nothing_parks_mid_run(self, harness: Harness, tmp_path: Path) -> None:
        """PIPELINES.md §3: *never a prompt mid-run*. A pre-granted task must not reach
        `WAITING`, which is the state a task enters when it stops to ask."""
        before = await harness.eventlog.load_head()

        async def answer() -> None:
            for _ in range(200):
                pending = list(harness.approvals._pending.values())
                if pending:
                    await harness.approvals.resolve(pending[0].id, True)
                    return
                await asyncio.sleep(0.01)

        answering = asyncio.create_task(answer())
        await harness.service.run(write(tmp_path, WITH_A_T2), {}, runners_for=harness.runners_for)
        await answering

        events = await harness.eventlog.read_range(before, await harness.eventlog.load_head())
        waiting = [e for e in events if e.payload.get("status") == str(TaskStatus.WAITING)]
        assert not waiting, "a pre-granted step must never park"

    async def test_a_refused_card_runs_nothing(self, harness: Harness, tmp_path: Path) -> None:
        before = await harness.eventlog.load_head()

        async def refuse() -> None:
            for _ in range(200):
                pending = list(harness.approvals._pending.values())
                if pending:
                    await harness.approvals.resolve(pending[0].id, False)
                    return
                await asyncio.sleep(0.01)

        refusing = asyncio.create_task(refuse())
        record = await harness.service.run(
            write(tmp_path, WITH_A_T2), {}, runners_for=harness.runners_for
        )
        await refusing

        assert record["status"] == "refused"
        events = await harness.eventlog.read_range(before, await harness.eventlog.load_head())
        assert not [e for e in events if e.type == "task.created"]


class TestNothingRunsBeforeValidationPasses:
    async def test_a_typo_in_the_last_step_stops_the_first(
        self, harness: Harness, tmp_path: Path
    ) -> None:
        """AC2. The record says `invalid`, names the bad tool, and no task was created."""
        before = await harness.eventlog.load_head()
        bad = READ_ONLY.replace("tool: fs.read", "tool: fs.raed")
        record = await harness.service.run(
            write(tmp_path, bad), {}, runners_for=harness.runners_for
        )

        assert record["status"] == "invalid"
        assert any("fs.raed" in p for p in record["problems"])
        events = await harness.eventlog.read_range(before, await harness.eventlog.load_head())
        assert not [e for e in events if e.type == "task.created"]


class TestTheTwoShippedPipelinesAreReal:
    """PIPELINES.md §7 promises two pipelines, *"both real"*. A promise about files that
    parse is not the same as a promise about files that would run, so this prices them
    against the **shipped** `config/policy.yaml` and the **real** tool registry.

    It is the closest a hermetic test can get to the dogfooding claim: nothing executes,
    but every tool is looked up, every argument is validated against that tool's own
    model, every path is canonicalised and every program is pinned. A typo, a renamed
    tool or an argument the contract does not take fails here.
    """

    def price(self, name: str) -> Any:
        from oracle.pipelines.compile import render
        from oracle.pipelines.loader import bind_params
        from oracle.policy.audit import AuditLog
        from oracle.runners.pipeline import check

        loaded, problems = load_file(
            Path("config/pipelines") / f"{name}.yaml", source=Source.GLOBAL
        )
        assert loaded is not None, problems

        executor = ToolExecutor(
            build_registry(),
            PolicyEngine(load_policy(Path("config/policy.yaml"))),
            AuditLog(Path("logs") / "test-audit.jsonl"),
        )
        root = Path("C:/Projects") / (loaded.project or "")
        # Defaults come from `bind_params`; `render` takes params already bound, which
        # is why `prepare()` is the entry point and this mirrors what it does.
        bound = bind_params(loaded.pipeline, {})
        rendered = render(loaded.pipeline, bound, project_root=str(root))
        return check(rendered, executor)

    def test_oracle_selfcheck_prices_against_the_real_policy(self) -> None:
        priced, problems = self.price("oracle-selfcheck")
        assert not problems, problems
        assert [p.step.id for p in priced] == [
            "format",
            "lint",
            "types",
            "tests",
            "security",
            "audit",
        ]
        # Its point: several T2 steps, one card. If this ever prices to all-T0 the
        # pipeline has stopped demonstrating the thing it exists to demonstrate.
        assert sum(1 for p in priced if p.needs_approval) >= 4

    def test_asterim_check_prices_against_the_real_policy(self) -> None:
        priced, problems = self.price("asterim-check")
        assert not problems, problems
        assert [p.step.id for p in priced] == [
            "status",
            "backend_tests",
            "frontend_tests",
            "build",
        ]

    def test_the_long_steps_carry_their_own_ceiling(self) -> None:
        """The defect P10-T2 fixed, checked where it actually bites: `dev.run_tests`
        declares 630 s and the scheduler's TOOL default is 120 s, so a test step with no
        `timeout:` would be killed at two minutes and reported as a hung suite."""
        priced, _ = self.price("oracle-selfcheck")
        tests = next(p for p in priced if p.step.id == "tests")
        assert tests.step.timeout_s is not None and tests.step.timeout_s > 600


class TestTheArtifactManifest:
    """A manifest pointing at blobs the tools already wrote — never a copy of them.

    A second artifact store is a second thing to back up, and a copy destination built
    from an author-controlled label is a path-traversal surface bought for nothing. The
    label is validated as a label at parse time regardless, so this can safely become a
    filename later if a real need appears.
    """

    def record(self, capture: str, evidence: dict[str, Any]) -> dict[str, Any]:
        from oracle.orchestration.models import Task, TaskResult, TaskSpec
        from oracle.pipelines.compile import RenderedStep
        from oracle.pipelines.models import ArtifactSpec
        from oracle.policy.model import Tier
        from oracle.runners.pipeline import PipelineRun, PricedStep, summarise

        step = RenderedStep(
            id="tests",
            tool="dev.run_tests",
            args={},
            timeout_s=None,
            on_failure="abort",
            max_attempts=1,
        )
        task = Task(
            id="tk_a-tests",
            root_id="tk_a",
            kind=TaskKind.TOOL,
            spec=TaskSpec(objective="x", role="operator", tool="dev.run_tests"),
            result=TaskResult(ok=True, summary="ok", evidence=evidence),
        ).with_status(TaskStatus.SUCCEEDED)

        run = PipelineRun(
            name="p",
            source=Source.GLOBAL,
            path=Path("p.yaml"),
            root_id="tk_a",
            project="ORACLE",
            params={},
            steps=(PricedStep(step, Tier.T1, "rule", "digest", False),),
            omitted=(),
            artifact_specs=(
                ArtifactSpec.model_validate({"from": "tests", "capture": capture, "as": "t.log"}),
            ),
        )
        from oracle.orchestration.graph import TaskGraph

        return summarise(run, TaskGraph([task]), TaskStatus.SUCCEEDED)

    def test_stdout_points_at_the_blob_the_tool_wrote(self) -> None:
        record = self.record("stdout", {"result": {"log_path": "D:/ORACLE/data/blobs/abc.log"}})
        assert record["artifacts"] == [
            {
                "step": "tests",
                "capture": "stdout",
                "as": "t.log",
                "pointer": "D:/ORACLE/data/blobs/abc.log",
            }
        ]

    def test_result_carries_the_structured_result_itself(self) -> None:
        record = self.record("result", {"result": {"passed": 12, "failed": 0}})
        assert record["artifacts"][0]["pointer"] == {"passed": 12, "failed": 0}

    def test_a_step_that_produced_nothing_is_absent_rather_than_null(self) -> None:
        """An entry pointing at nothing is worse than no entry: it reads as a file that
        exists and cannot be opened."""
        assert self.record("stdout", {"result": {}})["artifacts"] == []
