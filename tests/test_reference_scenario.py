"""The reference scenario, executed — INTEGRATIONS.md §8.

"Check why Asterim authentication is broken", as it actually runs. Everything here is
the real thing — the pipeline, the gate, the approval store, the packet renderer, the
worktree and its scrub, the collection — except the two ends that cost money: the local
model (`FakeProvider`, replaying the structured answers a turn needs) and the vendor CLI
(the stub, replaying output recorded from the real one in P6-T1).

**What is asserted is the ORDER**, because the ordering is the design:

    classify → tool → failing tests → escalate → packet (with the prior attempt)
             → egress approval → run → collect → report

Two properties matter more than the rest and each has its own assertion: **nothing
egresses before the approval**, and the evidence at the end is ORACLE's own — the diff
and the independently-run tests — not the delegate's claim about its work.
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
from oracle.core.events import Event
from oracle.core.sessions import SessionStore
from oracle.delegation.service import DelegationService, Outcome
from oracle.llm.fake import FakeProvider
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.router.intent import IntentClassifier
from oracle.router.pipeline import TurnPipeline
from oracle.router.selection import ToolSelector
from oracle.tools import ToolExecutor, build_registry
from oracle.tools.executor import ToolOutcome
from tests.helpers_delegation import SMOKE, make_repo, stub_adapter, wait_for

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
tools:
  fs.read:       {{ tier: T0, scopes: [projects] }}
  git.status:    {{ tier: T0, scopes: [projects] }}
  dev.run_tests: {{ tier: T1, scopes: [projects] }}
  ai.delegate:   {{ tier: T2 }}
"""

#: The two structured answers a routed turn needs from the local model: an intent, then
#: a tool choice. Both are shapes the P1 fixtures already measured the real model
#: producing — the fake is replaying, not inventing.
ROUTED_TURN = [
    json.dumps({"intent": "investigate", "project": "Asterim", "confidence": "high"}),
    json.dumps({"tool": "dev.run_tests", "argument": "Asterim", "reason": "check the failure"}),
]

#: Step 6 of §8: two failures, captured. Substituted for the real runner because a
#: pytest run inside a pytest run is a different test than this one.
FAILING = {
    "project": "Asterim",
    "runner": "pnpm",
    "command": "pnpm test auth",
    "passed": 11,
    "failed": 2,
    "skipped": 0,
    "total": 13,
    "duration_s": 3.2,
    "failures": [
        {"name": "refreshes the token after 15 minutes", "message": "expected 200, got 401"},
        {"name": "rejects an expired refresh token", "message": "timeout"},
    ],
    "exit_code": 1,
    "source": "json",
    "log_path": "x.log",
    "ok": False,
}


class ScriptedTests(ToolExecutor):
    """`dev.run_tests` returns the recorded failure; every other tool runs for real.

    The point of the scenario is the *escalation*, and escalation needs a failure that
    is a fact rather than a mock of one — so the shape is the real `TestRunResult`, it
    goes through the real gate, and it is reported on the real event log.
    """

    async def execute(self, tool_id: str, raw_args: dict[str, Any], **kwargs: Any) -> ToolOutcome:
        if tool_id != "dev.run_tests" or kwargs.get("dry_run"):
            return await super().execute(tool_id, raw_args, **kwargs)
        from oracle.tools.dev import TestRunResult

        verdict, _ = self.preview(tool_id, raw_args)
        return ToolOutcome(
            tool=tool_id,
            # Read from the fixture, not hardcoded: the sibling test flips these to
            # green, and an outcome that is always a failure would make it pass for
            # the wrong reason.
            ok=bool(FAILING["ok"]),
            result=TestRunResult(**FAILING),
            verdict=verdict,
            duration_ms=3200,
        )


@pytest.fixture
def asterim(tmp_path: Path) -> Path:
    """A project that looks like the scenario's: a git repo with agent docs."""
    projects = tmp_path / "Projects"
    repo = make_repo(tmp_path)  # tmp_path/project, a real git repo
    target = projects / "Asterim"
    projects.mkdir(exist_ok=True)
    repo.rename(target)
    (target / "AGENTS.md").write_text("Asterim conventions: strict TS.\n", encoding="utf-8")
    return target


@pytest.fixture
async def scenario(
    conn: aiosqlite.Connection, asterim: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Any:
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(POLICY.format(root=(tmp_path / "Projects").as_posix()), "utf-8")

    eventlog = EventLog(conn)
    await eventlog.load_head()
    engine = PolicyEngine(load_policy(policy_path))
    executor = ScriptedTests(build_registry(), engine, AuditLog(tmp_path / "audit.jsonl"))
    approvals = ApprovalStore(eventlog, executor, ttl_s=30.0)
    adapter = stub_adapter()
    delegations = DelegationService(
        eventlog,
        approvals,
        engine,
        adapter,
        handoff_root=tmp_path / "handoff",
    )
    provider = FakeProvider(ROUTED_TURN)
    pipeline = TurnPipeline(
        eventlog,
        provider,
        IntentClassifier(provider, projects=["Asterim"]),
        projects=["Asterim"],
        executor=executor,
        selector=ToolSelector(build_registry(), provider),
        approvals=approvals,
        projects_root=tmp_path / "Projects",
        delegations=delegations,
    )
    session = await SessionStore(conn).create(origin="test")
    return pipeline, eventlog, approvals, adapter, delegations, session


async def events(eventlog: EventLog, kind: str | None = None) -> list[Event]:
    rows = await eventlog.read_range(0, eventlog.last_seq, 2000)
    return [e for e in rows if kind is None or e.type == kind]


async def test_the_reference_scenario_runs_end_to_end(scenario: Any) -> None:
    pipeline, eventlog, approvals, adapter, _delegations, session = scenario

    # Steps 1-6: the routed turn. It classifies, picks one tool, runs it, and the tests
    # fail — everything ORACLE can do on its own, and it was not enough.
    turn = asyncio.ensure_future(pipeline.run(session, "why is Asterim authentication broken"))

    # Steps 7-9: the failure signature escalates, a packet is built, and the egress
    # preview is asked. Nothing has left the machine at this point — asserted below.
    requested = await wait_for(eventlog, "approval.requested")
    assert requested.payload["tool"] == "ai.delegate"
    assert adapter.submits == 0, "something egressed before the owner was asked"

    preview = requested.payload["preview"]
    packet_dir = Path(str(preview["packet_dir"]))
    attempts = (packet_dir / "ATTEMPTS.md").read_text(encoding="utf-8")
    # The packet carries what ORACLE already learned. A delegate that repeats the run
    # ORACLE just did is the waste this file format exists to prevent.
    assert "dev.run_tests" in attempts and "2 tests still failing" in attempts
    assert "refreshes the token after 15 minutes" in attempts
    assert "AGENTS.md" in preview["files"] or "CONTEXT.md" in preview["files"]

    await approvals.resolve(str(requested.payload["approval_id"]), True)
    await asyncio.wait_for(turn, 30)

    # Steps 10-12: the delegate runs, ORACLE collects its OWN evidence, and reports.
    finished = await wait_for(eventlog, "task.finished")
    assert finished.payload["outcome"] == Outcome.SUCCESS
    assert adapter.submits == 1
    assert "diff_lines" in finished.payload
    assert finished.payload["tests"]["ran"] is False  # no verifier wired in this harness
    assert finished.payload["cost_usd"] == pytest.approx(0.3213741)

    # The order is the design. This is the assertion the whole file is for.
    ordered = [e.type for e in await events(eventlog)]
    assert ordered.index("tool.started") < ordered.index("approval.requested")
    assert ordered.index("approval.requested") < ordered.index("approval.resolved")
    assert ordered.index("approval.resolved") < ordered.index("task.finished")

    states = [e.payload.get("state") for e in await events(eventlog, "agent.state")]
    assert "delegating" in states, "the handoff never announced itself"

    # And the turn did not stay open for the delegation.
    turn_end = (await events(eventlog, "turn.finished"))[-1]
    assert turn_end.payload["outcome"] in ("completed", "delegated")


async def test_a_passing_test_run_does_not_escalate(scenario: Any, monkeypatch: Any) -> None:
    """Escalation is a fact about the turn, not a habit. Green tests spend nothing."""
    pipeline, eventlog, _, adapter, _, session = scenario
    monkeypatch.setitem(FAILING, "failed", 0)
    monkeypatch.setitem(FAILING, "ok", True)
    monkeypatch.setitem(FAILING, "failures", [])
    monkeypatch.setitem(FAILING, "exit_code", 0)

    await asyncio.wait_for(pipeline.run(session, "run the auth tests for Asterim"), 30)

    assert await events(eventlog, "approval.requested") == []
    assert adapter.submits == 0
    assert await events(eventlog, "task.created") == []


DELEGATE_TURN = [json.dumps({"intent": "delegate", "project": "Asterim", "confidence": "high"})]
DELEGATE_NO_PROJECT = [json.dumps({"intent": "delegate", "project": None, "confidence": "high"})]


async def test_ask_claude_to_starts_a_delegation(scenario: Any) -> None:
    """The explicit route: "ask Claude to ..." reaches the delegation service, and the
    turn ends `delegated` rather than holding the session open for it."""
    pipeline, eventlog, approvals, adapter, _delegations, session = scenario
    pipeline._provider.responses = list(DELEGATE_TURN)
    pipeline._classifier._provider.responses = list(DELEGATE_TURN)

    turn = asyncio.ensure_future(
        pipeline.run(session, "ask Claude to rewrite the auth module in Asterim")
    )
    requested = await wait_for(eventlog, "approval.requested")
    assert requested.payload["tool"] == "ai.delegate"
    await approvals.resolve(str(requested.payload["approval_id"]), False)
    await asyncio.wait_for(turn, 30)

    assert adapter.submits == 0  # refused, so nothing was sent
    turn_end = (await events(eventlog, "turn.finished"))[-1]
    assert turn_end.payload["outcome"] == "delegated"
    states = [e.payload.get("state") for e in await events(eventlog, "agent.state")]
    assert "delegating" in states


async def test_an_unresolvable_project_asks_instead_of_guessing(scenario: Any) -> None:
    """A wrong answer costs a retry. A delegation against the wrong repository sends
    that repository's context to a cloud API — so this one asks."""
    pipeline, eventlog, _approvals, adapter, _delegations, session = scenario
    pipeline._provider.responses = list(DELEGATE_NO_PROJECT)
    pipeline._classifier._provider.responses = list(DELEGATE_NO_PROJECT)

    await asyncio.wait_for(pipeline.run(session, "ask Claude to fix the thing"), 30)

    assert await events(eventlog, "task.created") == []
    assert adapter.submits == 0
    replies = await events(eventlog, "message.completed")
    said = " ".join(str(e.payload.get("text", "")) for e in replies)
    assert "Which project" in said and "Asterim" in said
