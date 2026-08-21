"""Pre-router, intent classification and structured output.

Everything here runs against FakeProvider: no test may require Ollama
(docs/TESTING.md#1-the-three-properties-that-make-this-testable). Accuracy against a
real model is measured separately by scripts/eval_intent.py.
"""

from __future__ import annotations

import json

import pytest

from oracle.llm.fake import FakeProvider
from oracle.llm.structured import (
    StructuredOutputError,
    StructuredStats,
    generate_structured,
)
from oracle.llm.types import CallType, Message
from oracle.router.intent import CONFIDENCE_THRESHOLD, Intent, IntentClassifier
from oracle.router.prerouter import PreRouteKind, help_text, pre_route

PROJECTS = ["Asterim", "GameRecs", "Source2DemViewer"]
PIPELINES = frozenset({"asterim-check", "oracle-selfcheck"})


# ------------------------------------------------------------------- pre-router


class TestPreRouter:
    @pytest.mark.parametrize("text", ["/status", "/st", "/help", "/sessions"])
    def test_slash_commands_match(self, text: str) -> None:
        assert pre_route(text).kind is PreRouteKind.COMMAND

    def test_unknown_slash_still_resolves_deterministically(self) -> None:
        """A typo'd command must not be handed to a language model."""
        r = pre_route("/staus")
        assert r.kind is PreRouteKind.COMMAND
        assert r.command == "help"
        assert "staus" in help_text(r.args)

    @pytest.mark.parametrize("text", ["stop", "STOP", "остановись", "halt!", "cancel"])
    def test_bare_stop_words_halt(self, text: str) -> None:
        assert pre_route(text).kind is PreRouteKind.HALT

    @pytest.mark.parametrize(
        "text",
        ["stop the server from restarting", "I need to halt the deployment pipeline later"],
    )
    def test_stop_words_are_exact_match_only(self, text: str) -> None:
        """Substring matching here would swallow ordinary conversation."""
        assert pre_route(text).kind is not PreRouteKind.HALT

    @pytest.mark.parametrize(
        "text",
        [
            "ask Claude to fix the failing migration",
            "have antigravity look at this",
            "спроси Claude про этот баг",
        ],
    )
    def test_named_agent_delegates_deterministically(self, text: str) -> None:
        """MEASURED: qwen3.5:0.8b called 'ask Claude to fix X' an *investigate*. Naming
        an agent is a fact of the sentence, so it is decided without the model."""
        assert pre_route(text).kind is PreRouteKind.DELEGATE

    def test_agent_name_alone_is_not_delegation(self) -> None:
        assert pre_route("what is claude").kind is not PreRouteKind.DELEGATE

    def test_registered_pipeline_name_matches(self) -> None:
        r = pre_route("run the asterim-check pipeline", pipelines=PIPELINES)
        assert r.kind is PreRouteKind.PIPELINE
        assert r.command == "asterim-check"

    def test_unregistered_pipeline_falls_through(self) -> None:
        assert pre_route("run the nightly pipeline", pipelines=PIPELINES).kind is PreRouteKind.NONE

    def test_ambiguous_text_falls_through_on_purpose(self) -> None:
        assert pre_route("why is the auth broken").kind is PreRouteKind.NONE

    def test_empty_input(self) -> None:
        assert pre_route("   ").kind is PreRouteKind.NONE


# --------------------------------------------------------------- structured out


class TestStructuredOutput:
    async def test_parses_valid_json(self) -> None:
        p = FakeProvider(['{"intent":"run","project":"Asterim","confidence":"high"}'])
        r = await generate_structured(p, [Message(role="user", content="x")], Intent)
        assert r.value.intent == "run"
        assert r.repairs == 0

    async def test_thinking_is_always_disabled(self) -> None:
        """Qwen3.5 defaults to thinking and returns an EMPTY response field; it spent
        229 tokens deciding how to say 'hello'. This must never regress."""
        p = FakeProvider(['{"intent":"chat","confidence":"high"}'])
        await generate_structured(p, [Message(role="user", content="x")], Intent)
        assert p.calls[0].think is False

    async def test_strips_code_fences(self) -> None:
        p = FakeProvider(['```json\n{"intent":"chat","confidence":"low"}\n```'])
        r = await generate_structured(p, [Message(role="user", content="x")], Intent)
        assert r.value.intent == "chat"

    async def test_repairs_once_then_succeeds(self) -> None:
        p = FakeProvider(["not json at all", '{"intent":"search","confidence":"high"}'])
        stats = StructuredStats()
        r = await generate_structured(p, [Message(role="user", content="x")], Intent, stats=stats)
        assert r.value.intent == "search"
        assert stats.repairs == 1
        assert stats.failures == 0

    async def test_repair_prompt_includes_the_actual_error(self) -> None:
        p = FakeProvider(["garbage", '{"intent":"chat","confidence":"low"}'])
        await generate_structured(p, [Message(role="user", content="x")], Intent)
        repair = p.calls[1].messages[-1].content
        assert "did not validate" in repair
        assert "garbage" in p.calls[1].messages[-2].content

    async def test_gives_up_after_one_repair(self) -> None:
        """Never a third attempt: a model that failed twice under constrained decoding
        will not succeed on the third, and the latency is real."""
        p = FakeProvider(["bad", "still bad", "would have worked"])
        stats = StructuredStats()
        with pytest.raises(StructuredOutputError):
            await generate_structured(p, [Message(role="user", content="x")], Intent, stats=stats)
        assert len(p.calls) == 2
        assert stats.failures == 1

    async def test_failure_rate_is_tracked(self) -> None:
        stats = StructuredStats()
        for _ in range(2):
            with pytest.raises(StructuredOutputError):
                await generate_structured(
                    FakeProvider(["x", "y"]),
                    [Message(role="user", content="q")],
                    Intent,
                    stats=stats,
                )
        assert stats.failure_rate > 0
        assert stats.snapshot()["failures"] == 2

    async def test_schema_is_sent_to_the_provider(self) -> None:
        p = FakeProvider(['{"intent":"chat","confidence":"high"}'])
        await generate_structured(p, [Message(role="user", content="x")], Intent)
        assert p.calls[0].schema_ is not None
        assert "intent" in json.dumps(p.calls[0].schema_)


# ------------------------------------------------------------------ classifier


def _fake(intent: str, project: str | None, confidence: str = "high") -> FakeProvider:
    return FakeProvider(
        [json.dumps({"intent": intent, "project": project, "confidence": confidence})]
    )


class TestIntentClassifier:
    async def test_resolves_a_known_project(self) -> None:
        clf = IntentClassifier(_fake("run", "Asterim"), projects=PROJECTS)
        r = await clf.classify("run the tests for Asterim")
        assert r.resolved_project == "Asterim"
        assert not r.needs_clarification

    async def test_project_match_is_case_insensitive(self) -> None:
        clf = IntentClassifier(_fake("run", "asterim"), projects=PROJECTS)
        assert (await clf.classify("x")).resolved_project == "Asterim"

    async def test_hallucinated_project_never_becomes_a_path(self) -> None:
        """The load-bearing safety rule: a project name the model invented resolves to
        None and asks, rather than turning into a filesystem path."""
        clf = IntentClassifier(_fake("run", "SCRAPSHIFT"), projects=PROJECTS)
        r = await clf.classify("run the tests for SCRAPSHIFT")
        assert r.resolved_project is None
        assert r.needs_clarification
        assert "SCRAPSHIFT" in (r.clarification or "")

    async def test_low_confidence_asks_instead_of_guessing(self) -> None:
        clf = IntentClassifier(_fake("run", None, "low"), projects=PROJECTS)
        r = await clf.classify("run the tests")
        assert r.needs_clarification
        assert r.intent.score < CONFIDENCE_THRESHOLD

    async def test_medium_confidence_proceeds(self) -> None:
        clf = IntentClassifier(_fake("search", None, "medium"), projects=PROJECTS)
        assert not (await clf.classify("find the thing")).needs_clarification

    async def test_route_call_stays_within_budget(self) -> None:
        """The budget guarantee, asserted rather than hoped."""
        clf = IntentClassifier(_fake("chat", None), projects=PROJECTS)
        r = await clf.classify("hello " * 500)
        from oracle.llm.types import BUDGETS

        assert r.tokens_used <= BUDGETS[CallType.ROUTE]

    async def test_uses_the_route_call_type(self) -> None:
        p = _fake("chat", None)
        await IntentClassifier(p, projects=PROJECTS).classify("hi")
        assert p.calls[0].call_type is CallType.ROUTE
