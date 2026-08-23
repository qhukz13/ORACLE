"""The delegation lifecycle: render → egress approval → run → verify → report.

One delegation is one background task spawned through `AppState.spawn`, for the same
reason the knowledge watcher is (rag/service.py): HALT cancels every tracked task, so
the emergency stop reaches a running delegation — and this service turns that
cancellation into `adapter.cancel()`, so the *child process* dies too, not just the
coroutine watching it.

The egress preview is not a new mechanism. It is an `ApprovalStore` request for
`ai.delegate` — the tool the policy file has declared T2 since Phase 2 — so digest
binding, TTL, idempotent resolve and HALT-refuses-all are all inherited, not
reimplemented. Two properties are this module's own:

* **The digest binds the rendered packet bytes.** Not a summary, not the arguments
  that produced it: the hash of the files that will leave the machine. After approval
  the digest is recomputed; a packet that changed between preview and submit is
  refused with `Resolution` semantics, exactly like a mutated tool call.
* **`submit()` is unreachable without that approval.** The only call site is behind
  the resolution check and the recomputed digest — asserted by the egress suite in
  `tests/security/`, which is the phase's headline criterion.

Delegation runs in the daemon, not the toolhost: it is minutes-long and owns a child
process, and a toolhost invocation is neither (ADR-0003's boundary is for tools, and
`ai.delegate` the *policy entry* is how the gate prices this action, not where it
executes).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from oracle.core.approvals import ApprovalStore, Resolution
from oracle.core.eventlog import EventLog
from oracle.core.events import Event, new_id
from oracle.handoff.packet import (
    BUDGET_TOKENS,
    Attempt,
    ContextExcerpt,
    FileEntry,
    GitState,
    WrittenPacket,
    write_packet,
)
from oracle.integrations.adapter import ExternalAgentAdapter
from oracle.integrations.types import AgentEventKind, AgentHandle, HandoffPacket
from oracle.integrations.workspace import Worktree, create_worktree
from oracle.logsink import get_logger
from oracle.policy.engine import PolicyEngine
from oracle.policy.model import Capability, Decision, Provenance, Tier

log = get_logger(__name__)

#: The policy entry the gate prices this action under (config/policy.yaml, TOOLS.md §ai).
TOOL_ID = "ai.delegate"
#: Where the delegate's traffic goes. Stated in the preview because "sending to the
#: cloud" without naming the cloud is not a preview.
DESTINATION = "api.anthropic.com"
#: Event-feed hygiene: a delegate's thinking can be pages; the feed carries the head.
EVENT_TEXT_CAP = 500


class DelegationState:
    RENDERING = "rendering"
    AWAITING_EGRESS = "awaiting_egress"
    RUNNING = "running"
    VERIFYING = "verifying"


class Outcome:
    SUCCESS = "success"
    FAILED = "failed"
    FALLBACK = "fallback"
    REFUSED = "refused"
    EXPIRED = "expired"
    HALTED = "halted"


def packet_digest(directory: Path) -> str:
    """The value the egress approval binds to: the bytes on disk, in name order.
    Anything that re-renders the packet changes this, which is the point."""
    h = sha256()
    for file in sorted(p for p in directory.iterdir() if p.is_file()):
        h.update(file.name.encode("utf-8"))
        h.update(b"\0")
        h.update(file.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


@dataclass
class PacketInputs:
    """Requirement-6 curation output, carried as one value so `start()` stays legible."""

    excerpts: tuple[ContextExcerpt, ...] = ()
    files: tuple[FileEntry, ...] = ()
    attempts: tuple[Attempt, ...] = ()
    state: GitState | None = None
    #: Sources whose provenance is untrusted (`local_foreign` / `external`). Shown in
    #: the preview and passed to the gate, which escalates the tier — approving
    #: tainted egress is a stronger decision and the policy says so, not the UI.
    tainted_sources: tuple[str, ...] = ()
    budget_tokens: int = BUDGET_TOKENS


@dataclass
class ActiveDelegation:
    task_id: str
    handle: AgentHandle | None = None
    worktree: Worktree | None = None
    written: WrittenPacket | None = None
    outcome: str | None = None
    result: dict[str, Any] = field(default_factory=dict)


class DelegationService:
    """Owns every live delegation. One instance per daemon, like `TerminalBridge`."""

    def __init__(
        self,
        eventlog: EventLog,
        approvals: ApprovalStore,
        engine: PolicyEngine,
        adapter: ExternalAgentAdapter,
        *,
        handoff_root: Path | None = None,
        workspace_factory: Callable[[Path, str], Worktree] = create_worktree,
        run_tests: Callable[[Path], Awaitable[dict[str, Any] | None]] | None = None,
    ) -> None:
        self._log = eventlog
        self._approvals = approvals
        self._engine = engine
        self._adapter = adapter
        #: When None, packets land beside their project (`<repo>/.oracle/handoff/`),
        #: the INTEGRATIONS.md §6 layout; tests pin an explicit root instead.
        self._handoff_root = handoff_root
        self._workspace_factory = workspace_factory
        #: The independent verification step — `dev.run_tests` routed through the
        #: gate. Injected, because the service must not import its way around the
        #: executor; when absent, the report says "not verified" rather than implying.
        self._run_tests = run_tests
        self._active: dict[str, ActiveDelegation] = {}

    # ------------------------------------------------------------------ lifecycle

    def get(self, task_id: str) -> ActiveDelegation | None:
        return self._active.get(task_id)

    async def run(
        self,
        packet: HandoffPacket,
        source_repo: Path,
        inputs: PacketInputs | None = None,
        *,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> ActiveDelegation:
        """The whole lifecycle. Designed to be spawned via `AppState.spawn`; returns
        the (terminal) `ActiveDelegation` for callers that await it directly."""
        inputs = inputs or PacketInputs()
        trace = trace_id or new_id("tr")
        active = ActiveDelegation(task_id=packet.task_id)
        self._active[packet.task_id] = active

        await self._emit(
            "task.created",
            active,
            session_id,
            trace,
            {"tool": TOOL_ID, "task": packet.task, "adapter": self._adapter.id},
        )
        try:
            await self._run_inner(packet, source_repo, inputs, active, session_id, trace)
        except asyncio.CancelledError:
            # HALT, shutdown, or a cancelled turn. The child must not outlive the
            # coroutine that was watching it.
            if active.handle is not None:
                await self._adapter.cancel(active.handle)
            active.outcome = Outcome.HALTED
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._finish(active, session_id, trace, {"reason": "cancelled"})
            raise
        except Exception as exc:  # pragma: no cover - defensive; a dead delegation must report
            log.warning("delegate.crashed", task_id=active.task_id, error=str(exc))
            active.outcome = Outcome.FAILED
            await self._finish(active, session_id, trace, {"error": str(exc)})
        return active

    async def _run_inner(
        self,
        packet: HandoffPacket,
        source_repo: Path,
        inputs: PacketInputs,
        active: ActiveDelegation,
        session_id: str | None,
        trace: str,
    ) -> None:
        # 1 — render. The packet exists before any question is asked, because the
        # preview shows what was actually rendered, never a promise of it. Rendering,
        # hashing, and later the git work all run off the event loop — the P5-T2
        # watcher taught what a blocking call on the loop costs (rag/service.py).
        await self._state(active, DelegationState.RENDERING, session_id, trace)
        written = await asyncio.to_thread(
            write_packet,
            packet,
            self._handoff_root or (source_repo / ".oracle" / "handoff"),
            excerpts=inputs.excerpts,
            files=inputs.files,
            attempts=inputs.attempts,
            state=inputs.state,
            budget_tokens=inputs.budget_tokens,
        )
        active.written = written
        digest = await asyncio.to_thread(packet_digest, written.directory)

        # 2 — preflight. A missing vendor is a routing fact: the packet stays on disk
        # and nothing needed anyone's permission, because nothing egressed.
        pre = await self._adapter.preflight()
        if not pre.ok:
            active.outcome = Outcome.FALLBACK
            explanation = ". ".join(p for p in (pre.reason, pre.remedy) if p)
            await self._finish(
                active,
                session_id,
                trace,
                {"explanation": explanation, "packet_dir": str(written.directory)},
            )
            return

        # 3 — the gate prices the egress. Taint escalates T2 → T3 (confirm_strong):
        # approving tainted context is a stronger decision, and policy says so.
        provenances = (
            frozenset({Provenance.LOCAL_FOREIGN}) if inputs.tainted_sources else frozenset()
        )
        verdict = self._engine.evaluate(
            TOOL_ID,
            capabilities=frozenset({Capability.AGENT_DELEGATE, Capability.NET_EGRESS}),
            provenances=provenances,
            declared_tier=Tier.T2,
        )
        if verdict.decision is Decision.DENY:
            active.outcome = Outcome.REFUSED
            await self._finish(active, session_id, trace, {"rule": verdict.rule})
            return

        # 4 — the egress preview, as a real approval. Everything the owner needs to
        # decide is in the payload; what is not here they did not see.
        await self._state(active, DelegationState.AWAITING_EGRESS, session_id, trace)
        pending = await self._approvals.request(
            TOOL_ID,
            {"task_id": packet.task_id, "packet_sha256": digest},
            verdict,
            digest,
            trace_id=trace,
            session_id=session_id,
            preview={
                "destination": DESTINATION,
                "adapter": self._adapter.id,
                "files": list(written.files),
                "tokens": written.tokens,
                "redactions": list(written.redactions),
                "dropped_excerpts": written.dropped_excerpts,
                "allowed_tools": list(packet.allowed_tools),
                "tainted_sources": list(inputs.tainted_sources),
                "packet_dir": str(written.directory),
            },
        )
        resolution = await self._approvals.wait(pending)
        if resolution != Resolution.APPROVED:
            active.outcome = (
                Outcome.EXPIRED if resolution == Resolution.EXPIRED else Outcome.REFUSED
            )
            await self._finish(active, session_id, trace, {"resolution": resolution})
            return

        # 5 — what was approved is what egresses, byte for byte.
        if await asyncio.to_thread(packet_digest, written.directory) != digest:
            active.outcome = Outcome.REFUSED
            await self._finish(
                active, session_id, trace, {"reason": "packet changed since approval"}
            )
            return

        # 6 — only now does anything cost a checkout: worktree, scrub included.
        worktree = await asyncio.to_thread(self._workspace_factory, source_repo, packet.task_id)
        active.worktree = worktree
        live = packet.model_copy(update={"context_dir": str(written.directory)})
        active.handle = await self._adapter.submit(live, worktree.ws)
        await self._state(
            active, DelegationState.RUNNING, session_id, trace, {"pid": active.handle.proc.pid}
        )

        # 7 — the feed. Coalescable by design: a dropped `thinking` is cosmetic,
        # the decisions all live on `task.*`.
        async for event in self._adapter.events(active.handle):
            await self._emit(
                "delegate.event",
                active,
                session_id,
                trace,
                {
                    "kind": str(event.kind),
                    "text": event.text[:EVENT_TEXT_CAP],
                    "tool": event.tool,
                    "from_subagent": event.from_subagent,
                },
            )
            if event.kind is AgentEventKind.FINISHED or event.kind is AgentEventKind.ERROR:
                break

        result = await self._adapter.collect(active.handle)

        # 8 — verification is ORACLE's, not the agent's. The diff is read from the
        # worktree and the tests run through the gate; prose claims verify nothing.
        await self._state(active, DelegationState.VERIFYING, session_id, trace)
        diff = await asyncio.to_thread(worktree.diff)
        untracked = await asyncio.to_thread(worktree.untracked)
        tests = await self._run_tests(worktree.ws.path) if self._run_tests else None

        active.outcome = Outcome.SUCCESS if result.success else Outcome.FAILED
        active.result = {
            "exit_code": result.exit_code,
            "cost_usd": result.cost_usd,
            "num_turns": result.num_turns,
            "structured": result.structured,
            "result_text": result.result_text[:EVENT_TEXT_CAP],
            "diff_lines": len(diff.splitlines()),
            "untracked": untracked,
            "tests": tests if tests is not None else {"ran": False, "reason": "no verifier wired"},
            "workspace": str(worktree.ws.path),
            "branch": worktree.branch,
        }
        await self._finish(active, session_id, trace, active.result)

    # ------------------------------------------------------------------ disposal

    async def discard(self, task_id: str) -> bool:
        """Throw away a finished delegation's worktree and branch. The packet stays —
        it is the record of what was sent."""
        active = self._active.get(task_id)
        if active is None or active.worktree is None:
            return False
        await asyncio.to_thread(active.worktree.discard)
        active.worktree = None
        return True

    # ------------------------------------------------------------------ plumbing

    async def _state(
        self,
        active: ActiveDelegation,
        state: str,
        session_id: str | None,
        trace: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        await self._emit(
            "task.updated", active, session_id, trace, {"state": state, **(extra or {})}
        )

    async def _finish(
        self,
        active: ActiveDelegation,
        session_id: str | None,
        trace: str,
        payload: dict[str, Any],
    ) -> None:
        log.info("delegate.finished", task_id=active.task_id, outcome=active.outcome)
        await self._emit(
            "task.finished", active, session_id, trace, {"outcome": active.outcome, **payload}
        )

    async def _emit(
        self,
        event_type: str,
        active: ActiveDelegation,
        session_id: str | None,
        trace: str,
        payload: dict[str, Any],
    ) -> None:
        await self._log.append(
            Event(
                type=event_type,
                session_id=session_id,
                task_id=active.task_id,
                trace_id=trace,
                payload=payload,
            )
        )
