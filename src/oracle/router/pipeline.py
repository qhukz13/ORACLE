"""The turn pipeline — replaces EchoAgent.

Emits the same event contract P0 established, so no client changes were needed to swap
a stand-in for a real agent. That was the point of building the seam first.

Scope discipline: this phase talks, it does not act. There are no tools and no side
effects until the policy gate exists in Phase 2
(docs/ROADMAP.md sequencing rule 2). When the classifier decides the user wants
something done, the pipeline says so and stops.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from oracle.core.eventlog import EventLog
from oracle.core.events import Event, new_id
from oracle.llm.provider import LLMProvider
from oracle.llm.structured import StructuredOutputError, StructuredStats
from oracle.llm.types import CallType, CompletionRequest, Message, ProviderUnavailable
from oracle.logsink import bind_trace, get_logger
from oracle.router.intent import IntentClassifier
from oracle.router.prerouter import PreRouteKind, PreRouteResult, help_text, pre_route

log = get_logger(__name__)

#: Intents we can genuinely satisfy without tools. Everything else is acknowledged.
_CONVERSATIONAL = frozenset({"chat", "question"})

_ANSWER_SYSTEM = (
    "You are ORACLE, a local assistant on the user's PC. Answer briefly and concretely. "
    "If you do not know, say so. Reply in the language the user used."
)

_PENDING = {
    "run": "run that",
    "modify": "make that change",
    "investigate": "investigate that",
    "search": "search for that",
    "status": "check that",
    "delegate": "hand that to a coding agent",
    "pipeline": "run that pipeline",
    "control": "do that",
}


class TurnPipeline:
    def __init__(
        self,
        eventlog: EventLog,
        provider: LLMProvider | None,
        classifier: IntentClassifier | None,
        *,
        projects: list[str] | None = None,
        pipelines: frozenset[str] | None = None,
        stats: StructuredStats | None = None,
        on_halt: Callable[[], None] | None = None,
    ) -> None:
        self._log = eventlog
        self._provider = provider
        self._classifier = classifier
        self._projects = projects or []
        self._pipelines = pipelines or frozenset()
        self.stats = stats or StructuredStats()
        self._on_halt = on_halt
        #: Set when the provider is unreachable. Deterministic paths keep working
        #: (ADR-0011); the UI shows a degraded banner rather than looking broken.
        self.degraded: str | None = None

    # ------------------------------------------------------------------ helpers

    async def _emit(
        self,
        etype: str,
        session_id: str,
        turn_id: str,
        trace: str,
        payload: dict[str, object],
        actor: str = "agent",
    ) -> None:
        await self._log.append(
            Event(
                type=etype,
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace,
                actor=actor,
                payload=payload,
            )
        )

    async def _say(self, text: str, session_id: str, turn_id: str, trace: str) -> None:
        """Deterministic reply: emitted as one completed message, not faked deltas."""
        await self._emit("message.completed", session_id, turn_id, trace, {"text": text})

    # --------------------------------------------------------------------- run

    async def run(self, session_id: str, text: str, trace_id: str | None = None) -> None:
        trace = bind_trace(trace_id)
        turn_id = new_id("t")
        emit = self._emit

        await emit("turn.started", session_id, turn_id, trace, {"text": text}, actor="user")
        try:
            # 1. deterministic pre-router: no model, no latency, no hallucination
            pre = pre_route(text, pipelines=self._pipelines)
            if pre.matched:
                await emit("agent.state", session_id, turn_id, trace, {"state": "executing"})
                await self._handle_preroute(pre, session_id, turn_id, trace)
                await emit("agent.state", session_id, turn_id, trace, {"state": "idle"})
                await emit(
                    "turn.finished",
                    session_id,
                    turn_id,
                    trace,
                    {"outcome": "completed", "route": "pre-router", "reason": pre.reason},
                )
                return

            # 2. the model is needed
            if self._provider is None or self._classifier is None or self.degraded:
                await self._say(
                    "Reasoning is offline"
                    + (f" ({self.degraded})" if self.degraded else "")
                    + ". Slash commands still work — try /help.",
                    session_id,
                    turn_id,
                    trace,
                )
                await emit(
                    "turn.finished",
                    session_id,
                    turn_id,
                    trace,
                    {"outcome": "degraded"},
                )
                return

            await emit("agent.state", session_id, turn_id, trace, {"state": "understanding"})
            try:
                cls = await self._classifier.classify(text)
            except ProviderUnavailable as exc:
                await self._degrade(exc, session_id, turn_id, trace)
                return
            except StructuredOutputError:
                # Both attempts failed: degrade deterministically, never a third try.
                await self._say(
                    "I couldn't parse that into an action. Could you rephrase?",
                    session_id,
                    turn_id,
                    trace,
                )
                await emit(
                    "turn.finished",
                    session_id,
                    turn_id,
                    trace,
                    {"outcome": "completed", "route": "structured-failure"},
                )
                return

            await emit(
                "agent.state",
                session_id,
                turn_id,
                trace,
                {
                    "state": "planning",
                    "intent": cls.intent.intent,
                    "project": cls.resolved_project,
                    "confidence": cls.intent.confidence,
                    "route_ms": cls.usage.ttft_ms + cls.usage.eval_ms if cls.usage else None,
                    "route_tokens": cls.tokens_used,
                },
            )

            # 3. act on the classification
            if cls.needs_clarification:
                await self._say(
                    cls.clarification or "Could you be more specific?", session_id, turn_id, trace
                )
            elif cls.intent.intent in _CONVERSATIONAL:
                await emit("agent.state", session_id, turn_id, trace, {"state": "executing"})
                await self._stream_answer(text, session_id, turn_id, trace)
            else:
                verb = _PENDING.get(cls.intent.intent, "do that")
                where = f" in {cls.resolved_project}" if cls.resolved_project else ""
                await self._say(
                    f"Understood — you want me to {verb}{where}. "
                    f"I can't yet: tools arrive in Phase 2, behind the policy gate. "
                    f"(intent: {cls.intent.intent}, confidence: {cls.intent.confidence})",
                    session_id,
                    turn_id,
                    trace,
                )

            await emit("agent.state", session_id, turn_id, trace, {"state": "idle"})
            await emit(
                "turn.finished",
                session_id,
                turn_id,
                trace,
                {"outcome": "completed", "intent": cls.intent.intent, "route": "model"},
            )

        except asyncio.CancelledError:
            await emit("agent.state", session_id, turn_id, trace, {"state": "idle"})
            await emit("turn.finished", session_id, turn_id, trace, {"outcome": "cancelled"})
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("pipeline.failed")
            await emit(
                "error",
                session_id,
                turn_id,
                trace,
                {"kind": "execution_failed", "message": str(exc)},
            )
            await emit("agent.state", session_id, turn_id, trace, {"state": "error"})
            await emit("turn.finished", session_id, turn_id, trace, {"outcome": "error"})

    # ------------------------------------------------------------------- parts

    async def _handle_preroute(
        self, pre: PreRouteResult, session_id: str, turn_id: str, trace: str
    ) -> None:
        if pre.kind is PreRouteKind.HALT:
            if self._on_halt:
                self._on_halt()
            await self._say("Stopped.", session_id, turn_id, trace)
        elif pre.kind is PreRouteKind.COMMAND:
            await self._say(
                self._run_command(pre.command or "help", pre.args), session_id, turn_id, trace
            )
        elif pre.kind is PreRouteKind.DELEGATE:
            await self._say(
                "That names an external coding agent. Delegation arrives in Phase 6 — "
                "for now I can only route it.",
                session_id,
                turn_id,
                trace,
            )
        elif pre.kind is PreRouteKind.PIPELINE:
            await self._say(
                f"Recognised pipeline {pre.command!r}. Pipelines arrive in Phase 7.",
                session_id,
                turn_id,
                trace,
            )

    def _run_command(self, command: str, args: str) -> str:
        if command == "help":
            return help_text(args)
        if command == "status":
            model = self._provider.model if self._provider else "none"
            state = self.degraded or "ready"
            return (
                f"model: {model}\nstate: {state}\n"
                f"projects: {', '.join(self._projects) or 'none'}\n"
                f"structured output: {self.stats.snapshot()}"
            )
        if command == "clear":
            return "(the view is cleared client-side; history is kept in the event log)"
        return f"/{command} is recognised but not implemented yet."

    async def _stream_answer(self, text: str, session_id: str, turn_id: str, trace: str) -> None:
        assert self._provider is not None
        req = CompletionRequest(
            messages=[
                Message(role="system", content=_ANSWER_SYSTEM),
                Message(role="user", content=text),
            ],
            call_type=CallType.ANSWER,
            think=False,
            temperature=0.3,
            max_tokens=400,
        )
        buf: list[str] = []
        try:
            async for delta in self._provider.stream(req):
                if delta.text:
                    buf.append(delta.text)
                    await self._emit(
                        "message.delta", session_id, turn_id, trace, {"text": delta.text}
                    )
        except ProviderUnavailable as exc:
            await self._degrade(exc, session_id, turn_id, trace)
            return
        await self._emit(
            "message.completed", session_id, turn_id, trace, {"text": "".join(buf).strip()}
        )

    async def _degrade(
        self, exc: ProviderUnavailable, session_id: str, turn_id: str, trace: str
    ) -> None:
        self.degraded = exc.reason
        await self._log.append(
            Event(
                type="system.degraded",
                session_id=session_id,
                trace_id=trace,
                payload={"component": "llm", "reason": exc.reason, "remedy": exc.remedy},
            )
        )
        await self._say(
            f"Reasoning is offline: {exc.reason}."
            + (f" Try: {exc.remedy}." if exc.remedy else "")
            + " Slash commands still work — /help.",
            session_id,
            turn_id,
            trace,
        )
        await self._emit("turn.finished", session_id, turn_id, trace, {"outcome": "degraded"})
