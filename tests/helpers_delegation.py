"""Shared harness for the delegation suites (unit + security).

Everything real except the vendor: a real `EventLog` on tmp sqlite, a real
`ApprovalStore`, a real `PolicyEngine` loaded from YAML with the `ai.delegate` rule the
shipped policy declares, a real git repo, and the stub CLI replaying recorded output.
The one deliberate double is `SpyAdapter`: the security suite's whole question is "did
`submit()` happen?", and a counter on the seam answers it without trusting logs.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from oracle.core.approvals import ApprovalStore
from oracle.core.eventlog import EventLog
from oracle.core.events import Event
from oracle.delegation.service import DelegationService
from oracle.integrations.claude import ClaudeCodeAdapter
from oracle.integrations.types import (
    AgentCaps,
    AgentHandle,
    AgentResult,
    HandoffPacket,
    Preflight,
    Workspace,
)
from oracle.integrations.workspace import _git
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.tools import ToolExecutor, build_registry

TESTS = Path(__file__).resolve().parent
STUB = TESTS / "stubs" / "stub_claude.py"
SMOKE = TESTS / "fixtures" / "claude_stream" / "smoke-v2.1.238.jsonl"

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
tools:
  ai.delegate: {{ tier: T2 }}
"""


class SpyAdapter:
    """Counts `submit()` calls on the way through. The security property under test is
    "unreachable without approval", and a counter at the seam is the assertion."""

    def __init__(self, inner: ClaudeCodeAdapter) -> None:
        self.inner = inner
        self.id = inner.id
        self.submits = 0

    def capabilities(self) -> AgentCaps:
        return self.inner.capabilities()

    async def preflight(self) -> Preflight:
        return await self.inner.preflight()

    async def submit(self, packet: HandoffPacket, ws: Workspace) -> AgentHandle:
        self.submits += 1
        return await self.inner.submit(packet, ws)

    def events(self, h: AgentHandle) -> Any:
        return self.inner.events(h)

    async def cancel(self, h: AgentHandle) -> None:
        await self.inner.cancel(h)

    async def collect(self, h: AgentHandle) -> AgentResult:
        return await self.inner.collect(h)


def stub_adapter(grace_s: float = 0.3) -> SpyAdapter:
    return SpyAdapter(ClaudeCodeAdapter(argv=(sys.executable, str(STUB)), grace_s=grace_s))


def make_repo(tmp_path: Path) -> Path:
    """A git project carrying the config a hostile project would (the scrub's diet)."""
    root = tmp_path / "project"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "seed")
    return root


def make_service(
    tmp_path: Path,
    eventlog: EventLog,
    adapter: Any,
    *,
    ttl_s: float = 5.0,
    run_tests: Any = None,
    tokens: Any = None,
    mcp_url: str = "",
) -> tuple[DelegationService, ApprovalStore, PolicyEngine]:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(POLICY.format(root=(tmp_path / "project").as_posix()), encoding="utf-8")
    engine = PolicyEngine(load_policy(policy_path))
    executor = ToolExecutor(build_registry(), engine, AuditLog(tmp_path / "audit.jsonl"))
    approvals = ApprovalStore(eventlog, executor, ttl_s=ttl_s)
    service = DelegationService(
        eventlog,
        approvals,
        engine,
        adapter,
        handoff_root=tmp_path / "handoff",
        run_tests=run_tests,
        tokens=tokens,
        mcp_url=mcp_url,
    )
    return service, approvals, engine


def packet(task_id: str = "t-del-1") -> HandoffPacket:
    return HandoffPacket(
        task_id=task_id,
        task="Count the words in hello.txt.",
        allowed_tools=("Read", "Write"),
        result_schema={"type": "object"},
    )


#: One generous ceiling for every wait — a hung stream should fail the test, not CI.
WAIT_S = 30.0


async def wait_for(eventlog: EventLog, type_: str) -> Event:
    """Block until an event of `type_` lands on the log — via the same `stream()`
    subscription a WS client uses, so the test rides the real fan-out path."""

    async def watch() -> Event:
        async for event in eventlog.stream(0):
            if event.type == type_:
                return event
        raise AssertionError("event stream ended")  # pragma: no cover

    return await asyncio.wait_for(watch(), WAIT_S)


async def wait_state(eventlog: EventLog, state: str) -> Event:
    """Block until `task.updated` reports the given state."""

    async def watch() -> Event:
        async for event in eventlog.stream(0):
            if event.type == "task.updated" and event.payload.get("state") == state:
                return event
        raise AssertionError("event stream ended")  # pragma: no cover

    return await asyncio.wait_for(watch(), WAIT_S)


async def events_of(eventlog: EventLog, type_: str | None = None) -> list[Event]:
    rows = await eventlog.read_range(0, eventlog.last_seq, 1000)
    return [e for e in rows if type_ is None or e.type == type_]
