"""The real runners: a gated tool call, a real delegation, and a verifier with a baseline.

Everything below the vendor is real — a real `ToolExecutor` with a real policy, a real
`DelegationService`, real worktrees, real git — and only the CLI is a stub replaying
recorded output. That is the same line the delegation suite already draws, and it is
where the interesting bugs live: not in whether a fake returns what the test told it to,
but in whether the lifecycle's dict maps onto a task's evidence without losing the
distinction between what ORACLE measured and what the agent claimed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from oracle.core.eventlog import EventLog
from oracle.orchestration.graph import TaskGraph
from oracle.orchestration.models import Task, TaskKind, TaskSpec, TaskStatus
from oracle.orchestration.scheduler import Limits, Scheduler
from oracle.orchestration.store import TaskStore
from oracle.runners.delegation import make_delegation_runner
from oracle.runners.tool import make_tool_runner
from oracle.runners.verify import BaselineCache, Counts, make_verify_runner
from tests.helpers_delegation import make_repo, make_service, stub_adapter

ROOT = "tk_root"

#: This module's own policy, not the delegation harness's. That one declares `ai.delegate`
#: and nothing else, and widening a fixture the security suite shares - to make a new test
#: pass - is the move AGENTS.md names outright ("never widen a filesystem scope to make a
#: test pass"). The same instinct applies to tool rules.
POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
tools:
  ai.delegate: {{ tier: T2 }}
  fs.read:
    tier: T0
    scopes: [projects]
"""


def executor_for(tmp_path: Path) -> Any:
    """A real executor over a real policy: registry, gate, audit log, no toolhost (these
    tools run in-process, which `ToolExecutor(host=None)` documents as the test path)."""
    from oracle.policy.audit import AuditLog
    from oracle.policy.engine import PolicyEngine, load_policy
    from oracle.tools import ToolExecutor, build_registry

    policy_path = tmp_path / "runner-policy.yaml"
    policy_path.write_text(POLICY.format(root=(tmp_path / "project").as_posix()), encoding="utf-8")
    engine = PolicyEngine(load_policy(policy_path))
    return ToolExecutor(build_registry(), engine, AuditLog(tmp_path / "runner-audit.jsonl"))


def task(
    task_id: str,
    *deps: str,
    kind: TaskKind = TaskKind.TOOL,
    tool: str | None = None,
    args: dict[str, Any] | None = None,
    objective: str = "do the thing",
) -> Task:
    return Task(
        id=task_id,
        root_id=ROOT,
        kind=kind,
        spec=TaskSpec(objective=objective, role="coder", tool=tool, args=args or {}),
        depends_on=tuple(deps),
    )


# -- the TOOL runner -----------------------------------------------------------


async def test_a_tool_task_runs_through_the_existing_gate(
    tmp_path: Path, eventlog: EventLog
) -> None:
    """One task, one ordinary `ToolInvocation`: same registry, same policy engine, same
    audit entry. The evidence records the rule that allowed it, not just the answer."""
    repo = make_repo(tmp_path)
    runner = make_tool_runner(executor_for(tmp_path))

    result = await runner(task("t1", tool="fs.read", args={"path": str(repo / "app.py")}))

    assert result.ok
    assert result.evidence["tool"] == "fs.read"
    assert "VALUE = 1" in json.dumps(result.evidence["result"])
    assert result.evidence["rule"], "the verdict's rule is missing from the evidence"
    assert result.claim is None, "a tool has no claim to make; it is code, not a narrator"


async def test_a_denied_tool_task_fails_and_is_never_retryable(
    tmp_path: Path, eventlog: EventLog
) -> None:
    """Retrying a denial is how an agent nags a person into approving something. The
    scheduler decides retries from this flag, so the flag is the control."""
    make_repo(tmp_path)
    runner = make_tool_runner(executor_for(tmp_path))

    outside = tmp_path.parent / "not-in-any-scope.txt"
    result = await runner(task("t1", tool="fs.read", args={"path": str(outside)}))

    assert not result.ok
    assert result.error is not None
    assert result.error.kind == "denied"
    assert result.error.retryable is False


async def test_a_tool_task_without_a_tool_is_a_construction_bug_not_a_negotiation(
    tmp_path: Path, eventlog: EventLog
) -> None:
    result = await make_tool_runner(executor_for(tmp_path))(task("t1"))

    assert not result.ok and result.error is not None
    assert result.error.kind == "invalid_args"


# -- the DELEGATION runner -----------------------------------------------------


async def test_a_delegation_task_separates_what_oracle_measured_from_what_the_agent_said(
    tmp_path: Path, eventlog: EventLog, conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the boundary. `DelegationService` returns both in one dict; a
    graph must not, because the moment they merge, a confident agent's prose starts
    gating dependent tasks."""
    from tests.helpers_delegation import SMOKE

    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    adapter = stub_adapter()
    service, approvals, _engine = make_service(tmp_path, eventlog, adapter)
    runner = make_delegation_runner(service, repo, allowed_tools=("Read", "Write"))

    import asyncio

    running = asyncio.create_task(runner(task("d1", kind=TaskKind.DELEGATION)))
    from tests.helpers_delegation import wait_for

    requested = await wait_for(eventlog, "approval.requested")
    await approvals.resolve(str(requested.payload["approval_id"]), True)
    result = await asyncio.wait_for(running, timeout=60)

    assert result.ok
    assert adapter.submits == 1
    # ORACLE's measurements.
    assert result.evidence["exit_code"] == 0
    assert "diff_lines" in result.evidence and "branch" in result.evidence
    # The agent's words, kept apart and gating nothing.
    assert result.claim is not None
    assert "result_text" not in result.evidence


class PlantingAdapter:
    """The stub CLI replays a recorded stream and writes nothing, so a test about
    *harvesting a worker's output* has to put output there. Planting it inside `submit()`
    - the exact moment the real vendor would start writing - keeps the test deterministic;
    the first version raced a polling loop against the delegation and lost."""

    def __init__(self, inner: Any, filename: str = "answer.txt", body: str = "42\n") -> None:
        self.inner = inner
        self.id = inner.id
        self.filename = filename
        self.body = body

    def capabilities(self) -> Any:
        return self.inner.capabilities()

    async def preflight(self) -> Any:
        return await self.inner.preflight()

    async def submit(self, packet: Any, ws: Any) -> Any:
        (ws.path / self.filename).write_text(self.body, encoding="utf-8")
        return await self.inner.submit(packet, ws)

    def events(self, h: Any) -> Any:
        return self.inner.events(h)

    async def cancel(self, h: Any) -> None:
        await self.inner.cancel(h)

    async def collect(self, h: Any) -> Any:
        return await self.inner.collect(h)


async def test_a_delegations_result_outlives_its_worktree(
    tmp_path: Path, eventlog: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The P6-T5 hole, closed: the delegate cannot commit, so ORACLE harvests the diff to
    the task's branch and records the sha. A dependent task finds the work by that sha
    after the checkout is gone."""
    import asyncio

    from tests.helpers_delegation import SMOKE, wait_for

    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    service, approvals, _engine = make_service(tmp_path, eventlog, PlantingAdapter(stub_adapter()))

    running = asyncio.create_task(
        make_delegation_runner(service, repo)(task("d1", kind=TaskKind.DELEGATION))
    )
    requested = await wait_for(eventlog, "approval.requested")
    await approvals.resolve(str(requested.payload["approval_id"]), True)
    result = await asyncio.wait_for(running, timeout=60)

    sha = result.evidence.get("harvest_commit")
    assert sha, f"nothing was harvested: {result.evidence}"

    active = service.get("d1")
    assert active is not None and active.worktree is not None
    await asyncio.to_thread(active.worktree.discard, keep_branch=True)

    kept = await asyncio.to_thread(_show, repo, f"{sha}:answer.txt")
    assert kept.strip() == "42", "the result did not survive its workspace"


def _show(repo: Path, ref: str) -> str:
    import subprocess

    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "show", ref],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout


async def test_a_refused_egress_fails_the_task_and_is_not_retried(
    tmp_path: Path, eventlog: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A human said no. Asking again is not a retry strategy, it is nagging."""
    import asyncio

    from tests.helpers_delegation import SMOKE, wait_for

    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    adapter = stub_adapter()
    service, approvals, _engine = make_service(tmp_path, eventlog, adapter)

    running = asyncio.create_task(
        make_delegation_runner(service, repo)(task("d1", kind=TaskKind.DELEGATION))
    )
    requested = await wait_for(eventlog, "approval.requested")
    await approvals.resolve(str(requested.payload["approval_id"]), False)
    result = await asyncio.wait_for(running, timeout=60)

    assert not result.ok
    assert adapter.submits == 0, "something egressed after a refusal"
    assert result.error is not None and result.error.retryable is False


# -- the VERIFY runner ---------------------------------------------------------


def report(passed: int, failed: int, names: list[str]) -> dict[str, Any]:
    return {
        "ran": True,
        "ok": failed == 0,
        "detail": {
            "passed": passed,
            "failed": failed,
            "total": passed + failed,
            "failures": [{"name": n, "message": "boom"} for n in names],
        },
    }


class FakeBaseline(BaselineCache):
    """A baseline without a three-minute suite run. Subclassed rather than mocked so the
    caching contract (one measurement per root) is the real one."""

    def __init__(self, counts: Counts | None) -> None:
        self._counts = counts
        self.calls = 0
        super().__init__(Path("."), self._never)

    async def _never(self, path: Path) -> dict[str, Any] | None:  # pragma: no cover
        raise AssertionError("the fake baseline must not run a suite")

    async def counts_for(self, root_id: str) -> Counts | None:
        self.calls += 1
        return self._counts


async def verify_setup(
    conn: aiosqlite.Connection, workspace: Path, observed: dict[str, Any], baseline: Counts | None
) -> Any:
    store = TaskStore(conn)
    worker = task("w1", kind=TaskKind.DELEGATION)
    from oracle.orchestration.models import TaskResult

    await store.save(
        worker.with_status(
            TaskStatus.SUCCEEDED,
            result=TaskResult(ok=True, evidence={"workspace": str(workspace), "diff_lines": 3}),
        )
    )
    runner = make_verify_runner(store, lambda _p: _returns(observed), FakeBaseline(baseline))
    return runner, task("v1", "w1", kind=TaskKind.VERIFY)


async def _returns(value: dict[str, Any]) -> dict[str, Any]:
    return value


async def test_pre_existing_failures_do_not_fail_the_verification(
    tmp_path: Path, conn: aiosqlite.Connection
) -> None:
    """The measurement this runner exists for: 28 failures in the delegate's worktree and
    28 in a pristine one is a delegation that broke nothing."""
    baseline = Counts(
        passed=578, failed=28, total=625, failures=frozenset(f"t{i}" for i in range(28))
    )
    observed = report(583, 28, [f"t{i}" for i in range(28)])
    runner, verify = await verify_setup(conn, tmp_path, observed, baseline)

    result = await runner(verify)

    assert result.ok, result.summary
    assert result.evidence["new_failures"] == []
    assert result.evidence["delta_passed"] == 5
    assert result.evidence["observed"]["failed"] == 28


async def test_a_new_failure_fails_the_verification(
    tmp_path: Path, conn: aiosqlite.Connection
) -> None:
    baseline = Counts(passed=100, failed=1, total=101, failures=frozenset({"old"}))
    observed = report(99, 2, ["old", "freshly_broken"])
    runner, verify = await verify_setup(conn, tmp_path, observed, baseline)

    result = await runner(verify)

    assert not result.ok
    assert result.evidence["new_failures"] == ["freshly_broken"]
    assert result.error is not None and "freshly_broken" in result.error.message


async def test_a_fixed_failure_is_recorded_as_evidence(
    tmp_path: Path, conn: aiosqlite.Connection
) -> None:
    baseline = Counts(passed=100, failed=2, total=102, failures=frozenset({"old", "also_old"}))
    observed = report(101, 1, ["old"])
    runner, verify = await verify_setup(conn, tmp_path, observed, baseline)

    result = await runner(verify)

    assert result.ok
    assert result.evidence["fixed"] == ["also_old"]


async def test_without_a_baseline_the_verifier_refuses_to_guess(
    tmp_path: Path, conn: aiosqlite.Connection
) -> None:
    """A verifier that quietly falls back to a threshold is worse than one that admits it
    cannot tell — the threshold would reject every correct delegation in this repo."""
    observed = report(583, 28, [f"t{i}" for i in range(28)])
    runner, verify = await verify_setup(conn, tmp_path, observed, None)

    result = await runner(verify)

    assert not result.ok
    assert "no baseline" in result.summary
    assert result.evidence["observed"]["failed"] == 28


async def test_a_suite_that_did_not_run_is_not_a_pass(
    tmp_path: Path, conn: aiosqlite.Connection
) -> None:
    """P6-T5's false green, as a test: the tool returned, the tests never ran, and the
    first version of that harness printed 'ok'."""
    baseline = Counts(passed=1, failed=0, total=1, failures=frozenset())
    runner, verify = await verify_setup(
        conn, tmp_path, {"ran": True, "ok": False, "detail": None}, baseline
    )

    result = await runner(verify)

    assert not result.ok
    assert "did not run" in result.summary
    assert result.error is not None and result.error.retryable is True


async def test_verify_with_no_workspace_to_check_says_so(conn: aiosqlite.Connection) -> None:
    store = TaskStore(conn)
    runner = make_verify_runner(store, lambda _p: _returns({}), FakeBaseline(None))
    result = await runner(task("v1", kind=TaskKind.VERIFY))
    assert not result.ok and "nothing to verify" in result.summary


# -- the four-task graph, for real ---------------------------------------------


async def test_the_four_task_graph_runs_through_the_real_lifecycle(
    tmp_path: Path, eventlog: EventLog, conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tool → delegation → verify → report, with the stub CLI standing in for Claude and
    nothing else faked below it. The assertion is the order and the gating, exactly as
    `test_reference_scenario.py` asserts them for a single turn."""
    import asyncio

    from oracle.orchestration.models import TaskResult
    from tests.helpers_delegation import SMOKE, wait_for

    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    service, approvals, _engine = make_service(tmp_path, eventlog, stub_adapter())
    executor = executor_for(tmp_path)
    store = TaskStore(conn)

    order: list[str] = []

    async def traced(inner: Any, name: str) -> Any:
        async def run(t: Task) -> Any:
            order.append(t.id)
            return await inner(t)

        return run

    baseline = Counts(passed=1, failed=0, total=1, failures=frozenset())

    async def report_runner(t: Task) -> TaskResult:
        order.append(t.id)
        return TaskResult(ok=True, summary="reported")

    graph = TaskGraph(
        [
            task("look", kind=TaskKind.TOOL, tool="fs.read", args={"path": str(repo / "app.py")}),
            task("fix", "look", kind=TaskKind.DELEGATION),
            task("check", "fix", kind=TaskKind.VERIFY),
            task("tell", "check", kind=TaskKind.REPORT),
        ]
    )
    runners = {
        TaskKind.TOOL: await traced(make_tool_runner(executor), "tool"),
        TaskKind.DELEGATION: await traced(make_delegation_runner(service, repo), "delegation"),
        TaskKind.VERIFY: await traced(
            make_verify_runner(
                store, lambda _p: _returns(report(1, 0, [])), FakeBaseline(baseline)
            ),
            "verify",
        ),
        TaskKind.REPORT: report_runner,
    }
    scheduler = Scheduler(graph, runners, store=store, eventlog=eventlog, limits=Limits(local=2))

    async def approve() -> None:
        requested = await wait_for(eventlog, "approval.requested")
        await approvals.resolve(str(requested.payload["approval_id"]), True)

    approver = asyncio.create_task(approve())
    status = await asyncio.wait_for(scheduler.run(), timeout=120)
    await approver

    assert order == ["look", "fix", "check", "tell"], order
    assert status is TaskStatus.SUCCEEDED
    assert graph["check"].result is not None
    assert graph["check"].result.evidence["verified"] == "fix"

    # Both event streams are present for the delegation task, and distinguishable.
    from tests.helpers_delegation import events_of

    created = [e for e in await events_of(eventlog, "task.created")]
    graph_created = [e for e in created if e.payload.get("source") == "graph"]
    assert len(graph_created) == 4, "the graph's own task.created events are ambiguous"


async def test_two_delegations_run_side_by_side_and_the_third_queues(
    tmp_path: Path, eventlog: EventLog, conn: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The concurrency limit with real worktrees behind it. Each delegation cuts its own
    checkout from the same repo, so "without interference" is checkable: three distinct
    workspaces, three distinct branches, and never more than two live at once."""
    import asyncio

    from tests.helpers_delegation import SMOKE

    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    service, approvals, _engine = make_service(tmp_path, eventlog, stub_adapter(), ttl_s=60.0)
    inner = make_delegation_runner(service, repo)

    live = 0
    peak = 0

    async def counted(t: Task) -> Any:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            return await inner(t)
        finally:
            live -= 1

    graph = TaskGraph([task(f"d{i}", kind=TaskKind.DELEGATION) for i in range(3)])
    scheduler = Scheduler(
        graph,
        {TaskKind.DELEGATION: counted},
        store=TaskStore(conn),
        eventlog=eventlog,
        limits=Limits(delegation=2),
    )

    async def approve_each() -> None:
        # One subscription, not one per approval: `wait_for` restarts the stream from
        # seq 0 and hands back the *first* match every time, so calling it in a loop
        # re-reads the same request forever while the others quietly expire. Three
        # concurrent egresses need three answers, in the order they are asked.
        seen: set[str] = set()
        async for event in eventlog.stream(0):
            if event.type != "approval.requested":
                continue
            approval_id = str(event.payload["approval_id"])
            if approval_id in seen:
                continue
            seen.add(approval_id)
            await approvals.resolve(approval_id, True)
            if len(seen) == 3:
                return

    approver = asyncio.create_task(approve_each())
    status = await asyncio.wait_for(scheduler.run(), timeout=180)
    await asyncio.wait_for(approver, timeout=10)

    assert status is TaskStatus.SUCCEEDED
    assert peak == 2, f"the delegation limit did not hold (peak {peak})"
    workspaces = {graph[f"d{i}"].result.evidence["workspace"] for i in range(3)}  # type: ignore[union-attr]
    branches = {graph[f"d{i}"].result.evidence["branch"] for i in range(3)}  # type: ignore[union-attr]
    assert len(workspaces) == 3, "delegations shared a workspace"
    assert len(branches) == 3, "delegations shared a branch"
