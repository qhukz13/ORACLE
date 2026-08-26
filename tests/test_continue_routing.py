"""Routing `continue` (docs/PROJECT_STATE.md §5, P12-T2).

`continue` is the first intent whose object is a **project rather than a request**, and
the router's job is correspondingly small: resolve the project, or ask. It does not decide
the work — deciding the work needs the task table, the gate and a planner, none of which
the router may reach.

So what these pin is the seam, in both directions: an unresolvable project **asks and
starts nothing**, and a resolved one hands off exactly once with the resolved name.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiosqlite
import pytest_asyncio

from oracle.core.eventlog import EventLog
from oracle.core.events import Event
from oracle.core.sessions import SessionStore
from oracle.llm.fake import FakeProvider
from oracle.router.intent import IntentClassifier
from oracle.router.pipeline import TurnPipeline


class _Handoffs:
    """Stands in for the daemon's `continue_work` hook. Records, never acts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str]] = []

    def __call__(self, project: str, session_id: str | None, trace: str) -> None:
        self.calls.append((project, session_id, trace))


def _pipeline(
    conn: aiosqlite.Connection,
    responses: list[str],
    *,
    projects: list[str] | None = None,
    hook: _Handoffs | None = None,
) -> tuple[TurnPipeline, EventLog]:
    eventlog = EventLog(conn)
    provider = FakeProvider(responses)
    names = projects if projects is not None else ["Asterim", "GameRecs"]
    pipeline = TurnPipeline(
        eventlog,
        provider,
        IntentClassifier(provider, projects=names),
        projects=names,
    )
    if hook is not None:
        pipeline.continue_work = hook
    return pipeline, eventlog


@pytest_asyncio.fixture
async def session(conn: aiosqlite.Connection) -> AsyncIterator[str]:
    el = EventLog(conn)
    await el.load_head()
    yield await SessionStore(conn).create(origin="test")


async def _said(eventlog: EventLog) -> str:
    rows: list[Event] = await eventlog.read_range(0, eventlog.last_seq, 500)
    return " ".join(str(e.payload.get("text", "")) for e in rows if e.type == "message.completed")


async def _states(eventlog: EventLog) -> list[str]:
    rows: list[Event] = await eventlog.read_range(0, eventlog.last_seq, 500)
    return [str(e.payload.get("state")) for e in rows if e.type == "agent.state"]


class TestItHandsOff:
    async def test_a_resolved_project_reaches_the_hook_once(
        self, conn: aiosqlite.Connection, session: str
    ) -> None:
        hook = _Handoffs()
        pipeline, _ = _pipeline(
            conn,
            ['{"intent":"continue","project":"Asterim","confidence":"high"}'],
            hook=hook,
        )
        await pipeline.run(session, "continue Asterim")

        assert [c[0] for c in hook.calls] == ["Asterim"]
        assert hook.calls[0][1] == session

    async def test_the_turn_announces_planning_and_ends(
        self, conn: aiosqlite.Connection, session: str
    ) -> None:
        """The turn does not wait for the graph, exactly as `delegate` does not wait for
        a delegation: planning plus a run takes minutes, and a session blocked on one
        would stop the user asking anything else."""
        pipeline, eventlog = _pipeline(
            conn,
            ['{"intent":"continue","project":"Asterim","confidence":"high"}'],
            hook=_Handoffs(),
        )
        await pipeline.run(session, "continue Asterim")

        states = await _states(eventlog)
        assert "planning" in states
        assert states[-1] == "idle"

    async def test_a_project_named_only_in_the_text_is_recovered(
        self, conn: aiosqlite.Connection, session: str
    ) -> None:
        """The classifier missing the project is a routing error, not a dead end — the
        same recovery `delegate` already does before it gives up and asks."""
        hook = _Handoffs()
        pipeline, _ = _pipeline(
            conn,
            ['{"intent":"continue","project":null,"confidence":"medium"}'],
            hook=hook,
        )
        await pipeline.run(session, "pick up where we left off in GameRecs")

        assert [c[0] for c in hook.calls] == ["GameRecs"]


class TestItAsksInsteadOfGuessing:
    async def test_an_unresolvable_project_asks_and_starts_nothing(
        self, conn: aiosqlite.Connection, session: str
    ) -> None:
        """The load-bearing refusal. "Continue" names no work, so a wrong project would
        send ORACLE to read some other repository's task documents and plan against
        them — and there is nothing in the request to notice the mistake by."""
        hook = _Handoffs()
        pipeline, eventlog = _pipeline(
            conn,
            ['{"intent":"continue","project":null,"confidence":"medium"}'],
            hook=hook,
        )
        await pipeline.run(session, "continue where we left off")

        assert hook.calls == []
        said = await _said(eventlog)
        assert "Continue which project?" in said
        assert "won't guess" in said

    async def test_the_question_lists_what_it_does_know(
        self, conn: aiosqlite.Connection, session: str
    ) -> None:
        """A question a person cannot answer is not better than a guess."""
        pipeline, eventlog = _pipeline(
            conn,
            ['{"intent":"continue","project":null,"confidence":"medium"}'],
            hook=_Handoffs(),
        )
        await pipeline.run(session, "continue where we left off")

        said = await _said(eventlog)
        assert "Asterim" in said and "GameRecs" in said

    async def test_with_no_projects_at_all_it_still_answers(
        self, conn: aiosqlite.Connection, session: str
    ) -> None:
        pipeline, eventlog = _pipeline(
            conn,
            ['{"intent":"continue","project":null,"confidence":"medium"}'],
            projects=[],
            hook=_Handoffs(),
        )
        await pipeline.run(session, "continue")

        assert "none discovered" in await _said(eventlog)


class TestWithoutTheHook:
    async def test_an_unwired_runtime_says_so(
        self, conn: aiosqlite.Connection, session: str
    ) -> None:
        """`continue_work` unset is a legitimate configuration — the same one `delegate`
        and `pipeline` already have — and it must say so rather than looking broken."""
        pipeline, eventlog = _pipeline(
            conn, ['{"intent":"continue","project":"Asterim","confidence":"high"}']
        )
        await pipeline.run(session, "continue Asterim")

        said = await _said(eventlog)
        assert "isn't wired into this runtime yet" in said
        assert "Asterim" in said

    async def test_it_asks_about_the_project_before_it_admits_the_gap(
        self, conn: aiosqlite.Connection, session: str
    ) -> None:
        """Order matters: an unresolvable project is a question for the user, and saying
        "not wired" first would send them off to fix the wrong thing."""
        pipeline, eventlog = _pipeline(
            conn, ['{"intent":"continue","project":null,"confidence":"medium"}']
        )
        await pipeline.run(session, "continue please")

        said = await _said(eventlog)
        assert "Continue which project?" in said
        assert "isn't wired" not in said


class TestTheLabelIsReachable:
    def test_continue_is_in_the_intent_vocabulary(self) -> None:
        from typing import get_args

        from oracle.router.intent import IntentLabel

        assert "continue" in get_args(IntentLabel)

    def test_the_prompt_teaches_the_boundary_against_run(self) -> None:
        """The named accuracy risk: "run the Asterim tests" and "continue Asterim" are
        one word apart to a 0.8B classifier, so the boundary is stated in the prompt
        rather than left to be inferred. See OQ-25 — the eval has not been re-run."""
        from oracle.router.intent import _EXAMPLES, _SYSTEM

        assert "continue " in _SYSTEM
        assert "NO specific work is named" in _SYSTEM
        assert '"intent":"continue"' in _EXAMPLES

    def test_both_languages_are_represented(self) -> None:
        """Every other label carries a Russian few-shot on the pairs that degraded. A
        label added without one is a label that only works in English."""
        from oracle.router.intent import _EXAMPLES

        russian = [
            line
            for line in _EXAMPLES.splitlines()
            if '"intent":"continue"' in line and any(c in line for c in "абвгдеёжзийклмноп")
        ]
        assert russian, "continue needs a Russian few-shot like every other label"
