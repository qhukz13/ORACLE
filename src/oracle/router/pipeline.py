"""The turn pipeline — the agent can now act.

Emits the same event contract P0 established, so no client changes were needed to swap
a stand-in for a real agent, and none were needed again to give it tools. That was the
point of building the seam first.

The order of a routed turn is the whole design:

    pre-route -> classify -> SELECT ONE TOOL -> gate -> (ask) -> execute -> report

Each step narrows. The pre-router answers without a model where it can; the classifier
picks an intent; selection picks one tool from the *intent-filtered* catalogue and
supplies at most one string; the gate decides the tier; a T2+ tier becomes a question
with a preview, and nothing runs until it is answered.

The model never authors control flow (docs/AGENT_RUNTIME.md#1). It cannot chain tools,
cannot retry a denial, and cannot construct a path — all three are properties of this
file, not of the prompt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from oracle.core.approvals import ApprovalStore, Resolution
from oracle.core.eventlog import EventLog
from oracle.core.events import Event, new_id
from oracle.delegation.service import DelegationService, PacketInputs
from oracle.handoff.gather import gather_git_state, gather_project_docs
from oracle.handoff.packet import Attempt
from oracle.integrations.types import HandoffPacket
from oracle.llm.provider import LLMProvider
from oracle.llm.structured import StructuredOutputError, StructuredStats
from oracle.llm.types import CallType, CompletionRequest, Message, ProviderUnavailable
from oracle.logsink import bind_trace, get_logger
from oracle.policy.model import Decision
from oracle.router.intent import IntentClassifier
from oracle.router.prerouter import PreRouteKind, PreRouteResult, help_text, pre_route
from oracle.router.selection import Selection, ToolSelector
from oracle.tools.executor import ToolExecutor, ToolOutcome

log = get_logger(__name__)

#: Intents where the user wants something DONE. Everything else is answered or asked
#: about; only these reach tool selection.
_ACTIONABLE = frozenset({"run", "modify", "investigate", "search", "status"})

#: Intents we can genuinely satisfy without tools. Everything else is acknowledged.
_CONVERSATIONAL = frozenset({"chat", "question"})

_ANSWER_SYSTEM = (
    "You are ORACLE, a local assistant on the user's PC. Answer briefly and concretely. "
    "If you do not know, say so. Reply in the language the user used."
)

#: Tools whose failure means "the code is broken", not "the tool went wrong". These are
#: the ones that can escalate: a failing test suite is a reproducible failure signature
#: a delegate can work from, whereas a refused `fs.write` is a policy answer and a
#: delegate would hit the same wall (INTEGRATIONS.md §8, step 7).
_VERIFICATION_TOOLS = frozenset({"dev.run_tests", "dev.build", "dev.lint"})


def _failure_signature(tool_id: str, outcome: ToolOutcome) -> str | None:
    """One sentence describing a reproducible failure, or None if there isn't one.

    Deliberately narrow. A tool that could not RUN (denied, missing program, bad path)
    is not a failing build — handing that to a delegate would spend money to be told
    the same thing.
    """
    if tool_id not in _VERIFICATION_TOOLS or outcome.error is not None:
        return None
    result = outcome.result
    failed = int(getattr(result, "failed", 0) or 0) if result is not None else 0
    if failed:
        names = list(getattr(result, "failures", None) or [])[:3]
        detail = ", ".join(str(getattr(f, "name", f)) for f in names)
        return f"{failed} test{'s' if failed != 1 else ''} still failing" + (
            f" ({detail})." if detail else "."
        )
    # A build or lint that exits non-zero without a parsed count.
    if not outcome.ok:
        return f"{tool_id} did not succeed."
    return None


def _named_project(text: str, projects: list[str]) -> str | None:
    """A known project the user named in their own sentence, if exactly one.

    Not a guess — the word is theirs. Two matches is ambiguity and resolves to None,
    because delegating to the wrong repository sends that repository's context to a
    cloud API, which is not the kind of mistake a retry fixes.
    """
    lowered = text.lower()
    found = [p for p in projects if p.lower() in lowered]
    return found[0] if len(found) == 1 else None


def _project_of(args: dict[str, Any], projects_root: Path | None) -> Path | None:
    """The project a tool call was aimed at, as a directory a delegation can own.

    A delegation needs a repository root, and the tool's `path` argument is the only
    evidence in the turn about which one. Refuses anything outside the projects root:
    delegating against a directory the owner never pointed at is not a recovery, it is
    a surprise.
    """
    raw = args.get("path") or args.get("repo")
    if not isinstance(raw, str) or not raw or projects_root is None:
        return None
    try:
        candidate = Path(raw).resolve()
        root = projects_root.resolve()
        if not candidate.is_relative_to(root):
            return None
    except OSError:  # pragma: no cover - unresolvable path is not a project
        return None
    # Walk up to the directory directly under the projects root: `dev.run_tests` may
    # have been aimed at a subdirectory, and a worktree is made of the whole repo.
    while candidate.parent != root and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate if candidate.is_dir() else None


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
        executor: ToolExecutor | None = None,
        selector: ToolSelector | None = None,
        approvals: ApprovalStore | None = None,
        projects_root: Path | None = None,
        delegations: DelegationService | None = None,
        spawn: Callable[[Any], None] | None = None,
    ) -> None:
        self._log = eventlog
        self._provider = provider
        self._classifier = classifier
        self._projects = projects or []
        #: Tools arrive as a set: an executor to run them, a selector to choose one,
        #: and a way to ask. Without all three the pipeline still talks — it just says
        #: it cannot act, which is what P1 did.
        self._executor = executor
        self._selector = selector
        self._approvals = approvals
        self._projects_root = projects_root
        #: Delegation. Absent means the `delegate` intent still says "not built" —
        #: which is what P1 through P5 saw, and is a legitimate configuration.
        self._delegations = delegations
        #: How a delegation outlives the turn that started it. Public, because the
        #: daemon assigns `AppState.spawn` here after building the state — which is
        #: what puts a turn-started delegation under HALT like every other task.
        self.spawn = spawn or self._own_spawn
        self._pipelines = pipelines or frozenset()
        self.stats = stats or StructuredStats()
        self._on_halt = on_halt
        #: Set when the provider is unreachable. Deterministic paths keep working
        #: (ADR-0011); the UI shows a degraded banner rather than looking broken.
        self.degraded: str | None = None
        #: Set by HALT. The pipeline refuses to start a turn while true.
        self.halted = False
        #: Delegations spawned by turns, held so they are neither garbage-collected
        #: mid-run nor invisible at shutdown.
        self._spawned: set[asyncio.Task[Any]] = set()

    def _own_spawn(self, coro: Any) -> None:
        task: asyncio.Task[Any] = asyncio.create_task(coro)
        self._spawned.add(task)
        task.add_done_callback(self._spawned.discard)

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
            if self.halted and not text.strip().lower().lstrip("/").startswith(("resume", "help")):
                await self._say(
                    "ORACLE is halted. Nothing will run until you resume it.",
                    session_id,
                    turn_id,
                    trace,
                )
                await emit("turn.finished", session_id, turn_id, trace, {"outcome": "halted"})
                return

            # 1. deterministic pre-router: no model, no latency, no hallucination
            pre = pre_route(text, pipelines=self._pipelines)
            if pre.matched:
                await emit("agent.state", session_id, turn_id, trace, {"state": "executing"})
                started = await self._handle_preroute(pre, text, session_id, turn_id, trace)
                await emit("agent.state", session_id, turn_id, trace, {"state": "idle"})
                await emit(
                    "turn.finished",
                    session_id,
                    turn_id,
                    trace,
                    {
                        # One vocabulary for both routes: a delegation started here ends
                        # the turn exactly as one started by the model does, because a
                        # client cannot be asked to know which path found it.
                        "outcome": "delegated" if started else "completed",
                        "route": "pre-router",
                        "reason": pre.reason,
                    },
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
            elif cls.intent.intent in _ACTIONABLE and self._can_act:
                await self._act(
                    text,
                    cls.intent.intent,
                    cls.resolved_project,
                    session_id,
                    turn_id,
                    trace,
                )
            elif cls.intent.intent == "delegate" and self._delegations is not None:
                delegated = await self._delegate_intent(
                    text, cls.resolved_project, session_id, turn_id, trace
                )
                if delegated:
                    # The delegation outlives this turn and streams under `task.*`;
                    # holding the turn open for it would block the session for work the
                    # user can already watch.
                    await emit("agent.state", session_id, turn_id, trace, {"state": "idle"})
                    await emit(
                        "turn.finished",
                        session_id,
                        turn_id,
                        trace,
                        {"outcome": "delegated", "intent": "delegate", "route": "model"},
                    )
                    return
            else:
                verb = _PENDING.get(cls.intent.intent, "do that")
                where = f" in {cls.resolved_project}" if cls.resolved_project else ""
                why = (
                    "that needs a phase that isn't built yet"
                    if cls.intent.intent not in _ACTIONABLE
                    else "tools are not wired into this runtime"
                )
                await self._say(
                    f"Understood — you want me to {verb}{where}. I can't: {why}. "
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

    # -------------------------------------------------------------------- tools

    @property
    def _can_act(self) -> bool:
        return self._executor is not None and self._selector is not None

    def _project_path(self, project: str | None) -> Path | None:
        """A project NAME becomes a path here, or nowhere.

        The model never writes a path. It names a project, the classifier checks that
        name against the registry, and the path is composed from a root this process
        owns — so a hallucinated project cannot become a filesystem argument.
        """
        if not project or self._projects_root is None:
            return None
        if project not in self._projects:
            return None
        return self._projects_root / project

    async def _act(
        self,
        text: str,
        intent: str,
        project: str | None,
        session_id: str,
        turn_id: str,
        trace: str,
    ) -> None:
        assert self._selector is not None and self._executor is not None
        emit = self._emit
        # `planning` in the documented state machine (docs/AGENT_RUNTIME.md#3): choosing
        # a tool IS the plan here. One vocabulary, no translation layer for the UI.
        await emit("agent.state", session_id, turn_id, trace, {"state": "planning"})

        project_path = self._project_path(project)
        try:
            selection = await self._selector.select(text, intent, project_path=project_path)
        except (ProviderUnavailable, StructuredOutputError) as exc:
            await self._say(
                f"I couldn't work out which tool to use ({type(exc).__name__}). "
                "Could you say it more directly?",
                session_id,
                turn_id,
                trace,
            )
            return

        await emit(
            "agent.state",
            session_id,
            turn_id,
            trace,
            {
                "state": "planning",
                "tool": selection.tool,
                "candidates": selection.candidates,
                "project": project,
            },
        )

        if selection.chose_nothing:
            # Deliberately not a retry. Asking the model again for the same turn is how
            # an agent talks itself into an action nobody wanted.
            await self._say(
                f"I don't have a tool for that yet — {selection.reason}.",
                session_id,
                turn_id,
                trace,
            )
            return

        assert selection.tool is not None
        await self._run_tool(selection, session_id, turn_id, trace)

    async def _run_tool(
        self, selection: Selection, session_id: str, turn_id: str, trace: str
    ) -> None:
        assert self._executor is not None and selection.tool is not None
        tool_id = selection.tool

        # Ask the gate what WOULD happen before doing anything. This is the same call
        # the Confirmation Center makes, and it produces the digest an approval binds
        # to — so the user approves exactly the arguments that later execute.
        try:
            verdict, digest = self._executor.preview(tool_id, selection.args)
        except Exception as exc:
            await self._say(f"I can't run {tool_id}: {exc}", session_id, turn_id, trace)
            return

        approval_id: str | None = None
        if verdict.decision is Decision.DENY:
            await self._say(
                f"{tool_id} is not allowed here: {verdict.reason or verdict.rule}.",
                session_id,
                turn_id,
                trace,
            )
            return

        if verdict.needs_approval:
            if self._approvals is None:
                await self._say(
                    f"{tool_id} needs your approval and there is no way to ask right now.",
                    session_id,
                    turn_id,
                    trace,
                )
                return
            await self._emit(
                "agent.state", session_id, turn_id, trace, {"state": "awaiting_approval"}
            )
            pending = await self._approvals.request(
                tool_id,
                selection.args,
                verdict,
                digest,
                trace_id=trace,
                session_id=session_id,
                turn_id=turn_id,
                preview=await self._preview_of(tool_id, selection.args),
            )
            resolution = await self._approvals.wait(pending)
            if resolution != Resolution.APPROVED:
                await self._say(
                    f"Not running {tool_id} — the request was {resolution}.",
                    session_id,
                    turn_id,
                    trace,
                )
                return
            approval_id = pending.id

        await self._emit("agent.state", session_id, turn_id, trace, {"state": "executing"})
        # The card the UI renders. `args` and `tier` are here because the point of a
        # tool card is that the user can see what was actually run, not a paraphrase.
        await self._emit(
            "tool.started",
            session_id,
            turn_id,
            trace,
            {"tool": tool_id, "args": selection.args, "tier": verdict.tier.label},
        )
        outcome = await self._executor.execute(tool_id, selection.args, approval_id=approval_id)
        summary = _render(tool_id, outcome)
        await self._emit(
            "tool.finished",
            session_id,
            turn_id,
            trace,
            {
                "tool": tool_id,
                "ok": outcome.ok,
                "duration_ms": outcome.duration_ms,
                "undo_id": outcome.undo_id,
                "summary": summary,
                "error": outcome.error.message if outcome.error else None,
                "error_kind": outcome.error.kind if outcome.error else None,
                **_citations_of(tool_id, outcome),
            },
        )
        await self._emit("agent.state", session_id, turn_id, trace, {"state": "idle"})
        await self._say(summary, session_id, turn_id, trace)

        # The escalation signal (INTEGRATIONS.md §8, step 7). Deterministic on purpose:
        # "a verification tool reported failure" is a fact about this turn, not a
        # judgement, and a model deciding to spend money on a stronger model is a loop
        # nobody asked for. Nothing egresses here — the delegation it starts asks for
        # the egress preview like any other, which is the human gate.
        failure = _failure_signature(tool_id, outcome)
        if failure is not None:
            await self._escalate(failure, selection, session_id, turn_id, trace)

    async def _delegate_intent(
        self, text: str, project: str | None, session_id: str, turn_id: str, trace: str
    ) -> bool:
        """ "Ask Claude to …" — the explicit route. Returns whether one started.

        An unresolvable project **asks** rather than guessing. A wrong answer costs a
        retry; a delegation against the wrong repository costs a worktree, a packet of
        that repository's context sent to a cloud API, and the owner's trust.
        """
        path = self._project_path(project or _named_project(text, self._projects))
        if path is None:
            known = ", ".join(self._projects) or "none discovered"
            await self._say(
                "Which project should I hand that to? I won't guess — a delegation "
                f"builds a packet from the project's own files. Known: {known}.",
                session_id,
                turn_id,
                trace,
            )
            return False
        await self._start_delegation(
            task=text, project=path, session_id=session_id, turn_id=turn_id, trace=trace
        )
        return True

    async def _escalate(
        self,
        failure: str,
        selection: Selection,
        session_id: str,
        turn_id: str,
        trace: str,
    ) -> None:
        """Hand the same work to a delegate, carrying what this turn already learned."""
        if self._delegations is None:
            return
        project = _project_of(selection.args, self._projects_root)
        if project is None:
            return
        await self._say(
            f"{failure} I'll hand this to a coding agent — you'll see exactly what would be "
            "sent before anything leaves the machine.",
            session_id,
            turn_id,
            trace,
        )
        await self._start_delegation(
            task=f"Fix the failing tests in {project.name}. {failure}",
            project=project,
            session_id=session_id,
            turn_id=turn_id,
            trace=trace,
            attempts=(
                Attempt(
                    date=date.today().isoformat(),
                    agent="oracle",
                    summary=(
                        f"ran {selection.tool} and it reported failure: {failure} "
                        "Do not simply re-run it; find the cause."
                    ),
                ),
            ),
        )

    async def _start_delegation(
        self,
        *,
        task: str,
        project: Path,
        session_id: str,
        turn_id: str,
        trace: str,
        attempts: tuple[Attempt, ...] = (),
    ) -> None:
        """Spawn a delegation and let the turn end.

        The turn does NOT wait for it: a delegation runs for minutes, and a session
        blocked on one would stop the user asking anything else — while the panel is
        already showing them the run (docs/UI.md, the delegation panel).
        """
        assert self._delegations is not None
        await self._emit("agent.state", session_id, turn_id, trace, {"state": "delegating"})
        packet = HandoffPacket(
            task_id=new_id("dlg"),
            task=task,
            allowed_tools=("Read", "Edit", "Write"),
        )
        inputs = PacketInputs(
            excerpts=await asyncio.to_thread(gather_project_docs, project),
            state=await asyncio.to_thread(gather_git_state, project),
            attempts=attempts,
        )
        self.spawn(
            self._delegations.run(packet, project, inputs, session_id=session_id, trace_id=trace)
        )

    async def _preview_of(self, tool_id: str, args: dict[str, Any]) -> dict[str, Any]:
        """What the confirmation card shows under EFFECT.

        docs/UI.md#9 requires a **real preview, never a paraphrase** — an actual file
        list, an actual set of commits. So where the tool supports a dry run, we run
        one: `dry_run=True` means the call performs nothing, which is exactly why it
        needs no approval of its own and cannot become a way to act before asking.

        The contract's `side_effects` sentence goes alongside it rather than instead of
        it. It says what *kind* of thing is about to happen; the dry run says what
        *this* one would do.
        """
        assert self._executor is not None
        contract = self._executor.registry.get(tool_id)
        preview: dict[str, Any] = {"summary": contract.side_effects, "args": args}

        if not contract.dry_run:
            return preview
        try:
            rehearsal = await self._executor.execute(tool_id, args, dry_run=True)
        except Exception as exc:  # pragma: no cover - a preview must never break the ask
            log.warning("preview.failed", tool=tool_id, error=str(exc))
            return preview
        if rehearsal.ok and rehearsal.result is not None:
            detail = getattr(rehearsal.result, "output", None) or getattr(
                rehearsal.result, "would_delete", None
            )
            if detail:
                preview["detail"] = detail
                preview["summary"] = f"{contract.side_effects}"
        elif rehearsal.error is not None:
            # A preview that failed is itself worth showing: "this would not work" is
            # the most useful thing the card could tell you before you approve it.
            preview["detail"] = f"a rehearsal of this failed: {rehearsal.error.message}"
        return preview

    # ------------------------------------------------------------------- parts

    async def _handle_preroute(
        self, pre: PreRouteResult, text: str, session_id: str, turn_id: str, trace: str
    ) -> bool:
        """Returns whether a delegation was started, which changes the turn's outcome."""
        if pre.kind is PreRouteKind.HALT:
            if self._on_halt:
                self._on_halt()
            await self._say("Stopped.", session_id, turn_id, trace)
        elif pre.kind is PreRouteKind.COMMAND:
            await self._say(
                self._run_command(pre.command or "help", pre.args), session_id, turn_id, trace
            )
        elif pre.kind is PreRouteKind.DELEGATE:
            # "ask Claude to ..." is recognised deterministically in ~5 ms, which is
            # the pre-router's whole point (OQ-15). It resolves no project, so the
            # delegate path looks for one the user named themselves and asks otherwise.
            if self._delegations is None:
                await self._say(
                    "That names an external coding agent, and delegation is not wired "
                    "into this runtime.",
                    session_id,
                    turn_id,
                    trace,
                )
            else:
                return await self._delegate_intent(text, None, session_id, turn_id, trace)
        elif pre.kind is PreRouteKind.PIPELINE:
            await self._say(
                f"Recognised pipeline {pre.command!r}. Pipelines arrive in Phase 7.",
                session_id,
                turn_id,
                trace,
            )
        return False

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


def _citations_of(tool_id: str, outcome: ToolOutcome) -> dict[str, Any]:
    """Attribution for a `know.*` result, for the UI to render (RAG.md §7).

    Metadata only — the chunk text is stripped. Two reasons, and the second is the
    binding one: the payload is persisted in the event log forever, and a search
    returning eight full chunks would write ~10 KB of prose into it per call. The text
    is already in `knowledge.db`, and the citation is a pointer to it.

    Emitted for `know.*` alone rather than for every tool, because a citation is only
    meaningful where the result *is* someone else's words.
    """
    if not tool_id.startswith("know.") or outcome.result is None:
        return {}
    payload = outcome.result.model_dump()
    rows = payload.get("results") or payload.get("citations") or []
    if not isinstance(rows, list):
        return {}
    return {
        "citations": [
            {k: v for k, v in row.items() if k != "text"} for row in rows if isinstance(row, dict)
        ],
        "tainted": bool(payload.get("tainted")),
        "degraded": bool(payload.get("degraded")),
    }


def _render(tool_id: str, outcome: ToolOutcome) -> str:
    """Turn a structured result into one sentence a human reads.

    Deliberately hand-written per tool rather than dumping the result object. The model
    already has the structured data; the *person* wants to know what happened, and
    "{'passed': 41, 'failed': 0, ...}" is not that. When there is no phrasing for a
    tool, the fallback says what ran rather than pretending to summarise it.
    """
    if not outcome.ok:
        err = outcome.error
        detail = err.message if err else "no detail"
        return f"{tool_id} failed: {detail}"

    r: Any = outcome.result
    undo = f" (undo: {outcome.undo_id})" if outcome.undo_id else ""

    if tool_id == "git.status":
        if getattr(r, "clean", False):
            return f"{r.branch} is clean."
        return (
            f"{r.branch}: {len(r.staged)} staged, {len(r.unstaged)} changed, "
            f"{len(r.untracked)} untracked."
        )
    if tool_id == "git.commit":
        return (
            f"Committed {r.short} on {r.branch} — {r.files_changed} file(s), "
            f"+{r.insertions}/-{r.deletions}.{undo}"
        )
    if tool_id == "git.add":
        return f"Staged {len(r.staged)} file(s).{undo}"
    if tool_id == "git.log":
        if not r.commits:
            return "No commits yet."
        newest = r.commits[0]
        return f"{len(r.commits)} commit(s); latest {newest.short} — {newest.subject}"
    if tool_id == "git.diff":
        return f"{r.files_changed} file(s) changed, +{r.insertions}/-{r.deletions}."
    if tool_id == "dev.run_tests":
        verdict = "passed" if r.ok else "FAILED"
        first = f" First failure: {r.failures[0].name}." if r.failures else ""
        return (
            f"Tests {verdict}: {r.passed} passed, {r.failed} failed, {r.skipped} skipped "
            f"in {r.duration_s}s ({r.runner}, {r.source}).{first}"
        )
    if tool_id in ("dev.build", "dev.lint"):
        verb = "Build" if tool_id == "dev.build" else "Lint"
        if r.ok:
            return f"{verb} succeeded in {r.duration_s}s."
        return f"{verb} failed (exit {r.exit_code}). " + (
            f"First problem: {r.diagnostics[0]}" if r.diagnostics else "See the log."
        )
    if tool_id == "fs.list":
        return f"{len(r.entries)} entries" + (" (truncated)" if r.truncated else "") + "."
    if tool_id == "sys.info":
        return f"CPU {r.cpu_percent}%, RAM {r.ram_used_gb}/{r.ram_total_gb} GB."
    if tool_id == "sys.processes":
        return f"{r.total} matching process(es)."

    return f"{tool_id} completed in {outcome.duration_ms} ms.{undo}"
