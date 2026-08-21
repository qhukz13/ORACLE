"""A routed turn that actually does something, end to end.

This is the seam the whole project was arranged around: the same event contract P0
established, now carrying a real tool call. Nothing here talks to Ollama —
`FakeProvider` supplies the two structured answers a turn needs (an intent, then a tool
choice), which is what makes an agent testable at all (docs/TESTING.md#1).

What is being checked is the *shape of the turn*, not the model's taste:

    classify -> select ONE tool -> gate -> (ask) -> execute -> report

and, at each seam, the refusal: a T2 tool does not run unasked, a refused approval does
not run at all, and a project the classifier could not resolve never becomes a path.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from oracle.core.approvals import ApprovalStore
from oracle.core.eventlog import EventLog
from oracle.core.events import Event
from oracle.core.sessions import SessionStore
from oracle.llm.fake import FakeProvider
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.router.intent import IntentClassifier
from oracle.router.pipeline import TurnPipeline
from oracle.router.selection import ToolSelector
from oracle.tools import ToolExecutor, build_registry

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
tools:
  fs.read:   {{ tier: T0, scopes: [projects] }}
  fs.list:   {{ tier: T0, scopes: [projects] }}
  fs.write:  {{ tier: T1, scopes: [projects] }}
  fs.delete: {{ tier: T3, scopes: [projects] }}
  sys.info:  {{ tier: T0 }}
"""


@pytest.fixture
def projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "Projects"
    (root / "Asterim").mkdir(parents=True)
    (root / "Asterim" / "README.md").write_text("hello", encoding="utf-8")
    return root


@pytest.fixture
def executor(tmp_path: Path, projects_root: Path) -> ToolExecutor:
    p = tmp_path / "policy.yaml"
    p.write_text(POLICY.format(root=projects_root.as_posix()), encoding="utf-8")
    return ToolExecutor(
        build_registry(),
        PolicyEngine(load_policy(p)),
        AuditLog(tmp_path / "audit.jsonl"),
    )


class _Harness:
    def __init__(
        self,
        eventlog: EventLog,
        pipeline: TurnPipeline,
        approvals: ApprovalStore,
        provider: FakeProvider,
    ) -> None:
        self.eventlog = eventlog
        self.pipeline = pipeline
        self.approvals = approvals
        self.provider = provider

    async def events(self, kind: str | None = None) -> list[Event]:
        rows = await self.eventlog.read_range(0, self.eventlog.last_seq, 500)
        return [e for e in rows if kind is None or e.type == kind]

    async def said(self) -> str:
        return " ".join(
            str(e.payload.get("text", "")) for e in await self.events("message.completed")
        )


def _harness(
    conn: aiosqlite.Connection,
    executor: ToolExecutor,
    projects_root: Path,
    responses: list[str],
) -> _Harness:
    eventlog = EventLog(conn)
    provider = FakeProvider(responses)
    registry = build_registry()
    approvals = ApprovalStore(eventlog, executor, ttl_s=2.0)
    pipeline = TurnPipeline(
        eventlog,
        provider,
        IntentClassifier(provider, projects=["Asterim"]),
        projects=["Asterim"],
        executor=executor,
        selector=ToolSelector(registry, provider),
        approvals=approvals,
        projects_root=projects_root,
    )
    return _Harness(eventlog, pipeline, approvals, provider)


@pytest_asyncio.fixture
async def session(conn: aiosqlite.Connection) -> AsyncIterator[str]:
    """A real session row. Events reference one by foreign key, and switching the
    constraint off for tests would hide exactly the bug it exists to catch."""
    el = EventLog(conn)
    await el.load_head()
    yield await SessionStore(conn).create(origin="test")


class TestAToolRuns:
    async def test_a_status_request_selects_and_executes_one_tool(
        self, conn: aiosqlite.Connection, session: str, executor: ToolExecutor, projects_root: Path
    ) -> None:
        h = _harness(
            conn,
            executor,
            projects_root,
            [
                '{"intent":"status","project":"Asterim","confidence":"high"}',
                '{"tool":"fs.list","text":""}',
            ],
        )
        await h.pipeline.run(session, "what is in Asterim")

        states = [e.payload.get("state") for e in await h.events("agent.state")]
        assert "planning" in states
        assert "executing" in states

        planning = [e for e in await h.events("agent.state") if e.payload.get("tool")]
        assert planning and planning[0].payload["tool"] == "fs.list"

        finished = await h.events("turn.finished")
        assert finished[-1].payload["outcome"] == "completed"
        assert "entries" in await h.said()

    async def test_the_project_path_is_composed_not_taken_from_the_model(
        self, conn: aiosqlite.Connection, session: str, executor: ToolExecutor, projects_root: Path
    ) -> None:
        """The model names a project. It never writes a path — so even if it tried to
        smuggle one through the free-text field, that field is not where paths come
        from."""
        h = _harness(
            conn,
            executor,
            projects_root,
            [
                '{"intent":"status","project":"Asterim","confidence":"high"}',
                '{"tool":"fs.list","text":"C:\\\\Windows\\\\System32"}',
            ],
        )
        await h.pipeline.run(session, "what is in Asterim")
        said = await h.said()
        assert "System32" not in said
        assert "entries" in said

    async def test_an_unresolved_project_asks_instead_of_guessing(
        self, conn: aiosqlite.Connection, session: str, executor: ToolExecutor, projects_root: Path
    ) -> None:
        h = _harness(
            conn,
            executor,
            projects_root,
            ['{"intent":"status","project":"NotARealProject","confidence":"high"}'],
        )
        await h.pipeline.run(session, "is NotARealProject clean")
        said = await h.said()
        assert "don't know a project" in said
        # Nothing was selected, so nothing was executed.
        assert not [
            e for e in await h.events("agent.state") if e.payload.get("state") == "executing"
        ]


class TestSelectionRefusals:
    async def test_choosing_none_reports_rather_than_improvising(
        self, conn: aiosqlite.Connection, session: str, executor: ToolExecutor, projects_root: Path
    ) -> None:
        h = _harness(
            conn,
            executor,
            projects_root,
            [
                '{"intent":"run","project":"Asterim","confidence":"high"}',
                '{"tool":"none","text":""}',
            ],
        )
        await h.pipeline.run(session, "do the thing")
        assert "don't have a tool for that" in await h.said()

    async def test_a_commit_without_a_message_is_refused_not_invented(
        self, conn: aiosqlite.Connection, session: str, executor: ToolExecutor, projects_root: Path
    ) -> None:
        """Committing "update" over an afternoon of work is worse than asking again."""
        h = _harness(
            conn,
            executor,
            projects_root,
            [
                '{"intent":"modify","project":"Asterim","confidence":"high"}',
                '{"tool":"git.commit","text":""}',
            ],
        )
        await h.pipeline.run(session, "commit my changes in Asterim")
        assert "needs a message" in await h.said()


class TestApprovalInATurn:
    async def test_a_t3_tool_asks_and_waits(
        self, conn: aiosqlite.Connection, session: str, executor: ToolExecutor, projects_root: Path
    ) -> None:
        """The round trip, inside a turn: the card is emitted, the turn parks, and the
        answer decides."""
        victim = projects_root / "Asterim" / "README.md"
        h = _harness(
            conn,
            executor,
            projects_root,
            [
                '{"intent":"modify","project":"Asterim","confidence":"high"}',
                '{"tool":"fs.delete","text":""}',
            ],
        )
        # `fs.delete` is not routable by design, so drive the gated path directly with
        # the same machinery the pipeline uses.
        verdict, digest = executor.preview("fs.delete", {"path": str(victim)})
        pending = await h.approvals.request(
            "fs.delete", {"path": str(victim)}, verdict, digest, trace_id="t", session_id=session
        )

        requested = await h.events("approval.requested")
        assert requested and requested[0].payload["tier"] == "T3"

        waiting = asyncio.create_task(h.approvals.wait(pending))
        await asyncio.sleep(0.02)
        assert not waiting.done(), "the turn did not wait for an answer"

        await h.approvals.resolve(pending.id, False)
        assert await waiting == "refused"
        assert victim.exists()

    async def test_the_turn_reports_a_refusal_rather_than_hanging(
        self, conn: aiosqlite.Connection, session: str, executor: ToolExecutor, projects_root: Path
    ) -> None:
        h = _harness(
            conn,
            executor,
            projects_root,
            [
                '{"intent":"status","project":"Asterim","confidence":"high"}',
                '{"tool":"fs.list","text":""}',
            ],
        )
        # An expired request must resolve, not sit forever holding the turn open.
        verdict, digest = executor.preview("fs.delete", {"path": str(projects_root / "Asterim")})
        pending = await h.approvals.request(
            "fs.delete",
            {"path": str(projects_root / "Asterim")},
            verdict,
            digest,
            trace_id="t",
        )
        pending.ttl_s = 0.1
        assert await h.approvals.wait(pending) == "expired"


class TestWithoutTools:
    async def test_a_pipeline_with_no_executor_says_so(
        self, conn: aiosqlite.Connection, session: str
    ) -> None:
        """P1 behaviour is still reachable, and still honest about what it cannot do."""
        eventlog = EventLog(conn)
        provider = FakeProvider(['{"intent":"run","project":"Asterim","confidence":"high"}'])
        pipeline = TurnPipeline(
            eventlog,
            provider,
            IntentClassifier(provider, projects=["Asterim"]),
            projects=["Asterim"],
        )
        await pipeline.run(session, "run the tests for Asterim")
        rows = await eventlog.read_range(0, eventlog.last_seq, 200)
        said = " ".join(
            str(e.payload.get("text", "")) for e in rows if e.type == "message.completed"
        )
        assert "not wired into this runtime" in said
