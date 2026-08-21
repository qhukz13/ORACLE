"""Approval issuance, and the round trip that makes a confirmation mean something.

A confirmation dialog is security theatre unless three things hold, and each one has a
test here:

  * what the user approved is what runs — the digest is computed from the preview and
    checked again at execution;
  * one answer grants one action — no reuse, no double-click double-grant;
  * nothing grants itself — expiry refuses, HALT refuses, and there is no path that
    turns silence into consent.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from oracle.core.approvals import ApprovalStore, Resolution
from oracle.core.eventlog import EventLog
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.policy.model import Decision
from oracle.tools import ToolErrorKind, ToolExecutor, build_registry

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
tools:
  fs.read:   {{ tier: T0, scopes: [projects] }}
  fs.write:  {{ tier: T1, scopes: [projects] }}
  fs.delete: {{ tier: T3, scopes: [projects] }}
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "Projects"
    root.mkdir()
    (root / "victim.txt").write_text("important", encoding="utf-8")
    (root / "other.txt").write_text("also important", encoding="utf-8")
    return root


@pytest.fixture
def executor(tmp_path: Path, workspace: Path) -> ToolExecutor:
    p = tmp_path / "policy.yaml"
    p.write_text(POLICY.format(root=workspace.as_posix()), encoding="utf-8")
    return ToolExecutor(
        build_registry(),
        PolicyEngine(load_policy(p)),
        AuditLog(tmp_path / "audit.jsonl"),
    )


@pytest_asyncio.fixture
async def store(
    conn: aiosqlite.Connection, executor: ToolExecutor
) -> AsyncIterator[tuple[ApprovalStore, EventLog, ToolExecutor]]:
    eventlog = EventLog(conn)
    await eventlog.load_head()
    yield ApprovalStore(eventlog, executor), eventlog, executor


async def _events_of(eventlog: EventLog, kind: str) -> list[dict[str, object]]:
    rows = await eventlog.read_range(0, eventlog.last_seq, 500)
    return [e.payload for e in rows if e.type == kind]


class TestTheRoundTrip:
    async def test_request_emits_a_card_and_approval_lets_it_run(
        self, store: tuple[ApprovalStore, EventLog, ToolExecutor], workspace: Path
    ) -> None:
        approvals, eventlog, executor = store
        args = {"path": str(workspace / "victim.txt")}

        verdict, digest = executor.preview("fs.delete", args)
        assert verdict.decision is Decision.CONFIRM_STRONG

        pending = await approvals.request("fs.delete", args, verdict, digest, trace_id="t1")

        requested = await _events_of(eventlog, "approval.requested")
        assert len(requested) == 1
        card = requested[0]
        # Everything the person needs to decide has to be IN the event: the card is
        # built from it, so anything absent could not have informed the decision.
        assert card["tool"] == "fs.delete"
        assert card["tier"] == "T3"
        assert card["decision"] == "confirm_strong"
        assert card["rule"]
        assert card["args"] == args

        assert await approvals.resolve(pending.id, True) == Resolution.APPROVED
        resolved = await _events_of(eventlog, "approval.resolved")
        assert resolved[0]["resolution"] == Resolution.APPROVED

        out = await executor.execute("fs.delete", args, approval_id=pending.id)
        assert out.ok, out.error and out.error.message
        assert not (workspace / "victim.txt").exists()

    async def test_refusal_does_not_grant(
        self, store: tuple[ApprovalStore, EventLog, ToolExecutor], workspace: Path
    ) -> None:
        approvals, _, executor = store
        args = {"path": str(workspace / "victim.txt")}
        verdict, digest = executor.preview("fs.delete", args)
        pending = await approvals.request("fs.delete", args, verdict, digest, trace_id="t2")

        assert await approvals.resolve(pending.id, False) == Resolution.REFUSED

        out = await executor.execute("fs.delete", args, approval_id=pending.id)
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.APPROVAL_INVALID
        assert (workspace / "victim.txt").exists()


class TestOneAnswerOneAction:
    async def test_approving_one_file_does_not_approve_another(
        self, store: tuple[ApprovalStore, EventLog, ToolExecutor], workspace: Path
    ) -> None:
        """The acceptance criterion: an approval cannot be reused for another file."""
        approvals, _, executor = store
        shown = {"path": str(workspace / "victim.txt")}
        verdict, digest = executor.preview("fs.delete", shown)
        pending = await approvals.request("fs.delete", shown, verdict, digest, trace_id="t3")
        await approvals.resolve(pending.id, True)

        swapped = {"path": str(workspace / "other.txt")}
        out = await executor.execute("fs.delete", swapped, approval_id=pending.id)
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.APPROVAL_INVALID
        assert (workspace / "other.txt").exists()

    async def test_an_approval_is_single_use(
        self, store: tuple[ApprovalStore, EventLog, ToolExecutor], workspace: Path
    ) -> None:
        approvals, _, executor = store
        args = {"path": str(workspace / "victim.txt")}
        verdict, digest = executor.preview("fs.delete", args)
        pending = await approvals.request("fs.delete", args, verdict, digest, trace_id="t4")
        await approvals.resolve(pending.id, True)

        first = await executor.execute("fs.delete", args, approval_id=pending.id)
        assert first.ok
        second = await executor.execute("fs.delete", args, approval_id=pending.id)
        assert not second.ok
        assert second.error is not None
        assert second.error.kind == ToolErrorKind.APPROVAL_INVALID

    async def test_resolving_twice_is_idempotent(
        self, store: tuple[ApprovalStore, EventLog, ToolExecutor], workspace: Path
    ) -> None:
        """A double-click, a retried frame or a stale UI must not produce two grants."""
        approvals, eventlog, executor = store
        args = {"path": str(workspace / "victim.txt")}
        verdict, digest = executor.preview("fs.delete", args)
        pending = await approvals.request("fs.delete", args, verdict, digest, trace_id="t5")

        assert await approvals.resolve(pending.id, False) == Resolution.REFUSED
        # A second click saying "yes" must not overturn the recorded "no".
        assert await approvals.resolve(pending.id, True) == Resolution.REFUSED
        assert len(await _events_of(eventlog, "approval.resolved")) == 1

    async def test_an_unknown_id_resolves_to_nothing(
        self, store: tuple[ApprovalStore, EventLog, ToolExecutor]
    ) -> None:
        approvals, eventlog, _ = store
        assert await approvals.resolve("ap_does_not_exist", True) == "unknown"
        assert await _events_of(eventlog, "approval.resolved") == []


class TestSilenceIsNotConsent:
    async def test_an_unanswered_request_expires_refused(
        self, conn: aiosqlite.Connection, executor: ToolExecutor, workspace: Path
    ) -> None:
        eventlog = EventLog(conn)
        await eventlog.load_head()
        approvals = ApprovalStore(eventlog, executor, ttl_s=0.15)

        args = {"path": str(workspace / "victim.txt")}
        verdict, digest = executor.preview("fs.delete", args)
        pending = await approvals.request("fs.delete", args, verdict, digest, trace_id="t6")

        assert await approvals.wait(pending) == Resolution.EXPIRED
        out = await executor.execute("fs.delete", args, approval_id=pending.id)
        assert not out.ok
        assert (workspace / "victim.txt").exists()

    async def test_halt_refuses_everything_pending(
        self, store: tuple[ApprovalStore, EventLog, ToolExecutor], workspace: Path
    ) -> None:
        """A stop that leaves three approvals live is not a stop."""
        approvals, eventlog, executor = store
        pendings = []
        for name in ("victim.txt", "other.txt"):
            args = {"path": str(workspace / name)}
            verdict, digest = executor.preview("fs.delete", args)
            pendings.append(
                await approvals.request("fs.delete", args, verdict, digest, trace_id="t7")
            )

        assert await approvals.refuse_all("user requested halt") == 2
        assert approvals.open_requests() == []
        for pending in pendings:
            out = await executor.execute("fs.delete", pending.args, approval_id=pending.id)
            assert not out.ok

        resolved = await _events_of(eventlog, "approval.resolved")
        assert {r["resolution"] for r in resolved} == {Resolution.HALTED}

    async def test_a_cancelled_wait_does_not_leave_the_request_live(
        self, store: tuple[ApprovalStore, EventLog, ToolExecutor], workspace: Path
    ) -> None:
        """If the turn is cancelled, a later click must not execute into the void."""
        approvals, _, executor = store
        args = {"path": str(workspace / "victim.txt")}
        verdict, digest = executor.preview("fs.delete", args)
        pending = await approvals.request("fs.delete", args, verdict, digest, trace_id="t8")

        waiter = asyncio.create_task(approvals.wait(pending))
        await asyncio.sleep(0.05)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert not pending.open
        assert await approvals.resolve(pending.id, True) == Resolution.HALTED
        out = await executor.execute("fs.delete", args, approval_id=pending.id)
        assert not out.ok


class TestWaiting:
    async def test_wait_returns_when_a_human_answers(
        self, store: tuple[ApprovalStore, EventLog, ToolExecutor], workspace: Path
    ) -> None:
        approvals, _, executor = store
        args = {"path": str(workspace / "victim.txt")}
        verdict, digest = executor.preview("fs.delete", args)
        pending = await approvals.request("fs.delete", args, verdict, digest, trace_id="t9")

        async def answer() -> None:
            await asyncio.sleep(0.05)
            await approvals.resolve(pending.id, True)

        answering = asyncio.create_task(answer())
        assert await approvals.wait(pending) == Resolution.APPROVED
        await answering

    async def test_open_requests_lists_only_unanswered_ones(
        self, store: tuple[ApprovalStore, EventLog, ToolExecutor], workspace: Path
    ) -> None:
        approvals, _, executor = store
        args = {"path": str(workspace / "victim.txt")}
        verdict, digest = executor.preview("fs.delete", args)
        pending = await approvals.request("fs.delete", args, verdict, digest, trace_id="t10")

        assert [r["approval_id"] for r in approvals.open_requests()] == [pending.id]
        await approvals.resolve(pending.id, True)
        assert approvals.open_requests() == []
