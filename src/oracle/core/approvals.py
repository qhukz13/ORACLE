"""Approval issuance: the `approval.requested` / `approval.resolved` round trip.

The gate decides *whether* to ask. This decides *how the asking works*, and there are
only a few properties that matter, all of them about not being a rubber stamp:

  * **An approval is bound to the exact arguments it was shown for.** The digest comes
    from the preview and is checked again at execution; approving a plan does not
    approve a mutated version of it. That check lives in the executor, and this module
    exists to make sure the two see the same digest.
  * **An unanswered request expires.** A pending approval that waits forever is a
    turn that never finishes and a lock nobody can clear. Expiry resolves it as
    *refused*, because "nobody answered" is not consent.
  * **Nothing auto-approves.** There is no timeout that grants, no "remember this
    choice", no batching. Prompt fatigue is a security failure
    (docs/SECURITY.md#2), and the answer to it is fewer prompts — via reversibility
    and the T1 tier — never cheaper ones.
  * **HALT refuses everything pending**, immediately. A stop that leaves three
    approvals live is not a stop.

What is emitted is a preview: the tool, the tier, the rule that fired, and a rendered
description of the effect. The card the user sees comes from the event, so whatever is
not in the event cannot be part of their decision.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from oracle.core.eventlog import EventLog
from oracle.core.events import Event, new_id
from oracle.logsink import get_logger
from oracle.policy.model import PolicyVerdict
from oracle.tools.executor import Approval, ToolExecutor

log = get_logger(__name__)

#: How long a request stays answerable. Long enough to walk back to the desk, short
#: enough that a forgotten card does not sit live for an afternoon.
DEFAULT_TTL_S = 180.0


class Resolution:
    APPROVED = "approved"
    REFUSED = "refused"
    EXPIRED = "expired"
    HALTED = "halted"


@dataclass
class PendingApproval:
    id: str
    tool: str
    args: dict[str, Any]
    digest: str
    verdict: PolicyVerdict
    preview: dict[str, Any]
    session_id: str | None
    turn_id: str | None
    trace_id: str
    created_at: float = field(default_factory=time.time)
    ttl_s: float = DEFAULT_TTL_S
    resolution: str | None = None
    future: asyncio.Future[str] = field(default_factory=asyncio.Future)

    @property
    def expires_at(self) -> float:
        return self.created_at + self.ttl_s

    @property
    def open(self) -> bool:
        return self.resolution is None

    def wire(self) -> dict[str, Any]:
        """What the confirmation card is built from. If it is not here, the user did
        not see it, and it cannot have informed their decision."""
        return {
            "approval_id": self.id,
            "tool": self.tool,
            "tier": self.verdict.tier.label,
            "decision": str(self.verdict.decision),
            "rule": self.verdict.rule,
            "tainted": self.verdict.tainted,
            "escalated": self.verdict.escalated,
            "args": self.args,
            "preview": self.preview,
            "expires_in_s": round(max(0.0, self.expires_at - time.time()), 1),
        }


class ApprovalStore:
    """Issues approval requests and waits for a human to answer them."""

    def __init__(
        self, eventlog: EventLog, executor: ToolExecutor, *, ttl_s: float = DEFAULT_TTL_S
    ) -> None:
        self._log = eventlog
        self._executor = executor
        self._ttl = ttl_s
        self._pending: dict[str, PendingApproval] = {}

    # ------------------------------------------------------------------ request

    async def request(
        self,
        tool: str,
        args: dict[str, Any],
        verdict: PolicyVerdict,
        digest: str,
        *,
        trace_id: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        preview: dict[str, Any] | None = None,
    ) -> PendingApproval:
        pending = PendingApproval(
            id=new_id("ap"),
            tool=tool,
            args=args,
            digest=digest,
            verdict=verdict,
            preview=preview or {},
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            ttl_s=self._ttl,
        )
        self._pending[pending.id] = pending
        await self._log.append(
            Event(
                type="approval.requested",
                session_id=session_id,
                turn_id=turn_id,
                trace_id=trace_id,
                payload=pending.wire(),
            )
        )
        log.info("approval.requested", approval=pending.id, tool=tool, tier=verdict.tier.label)
        return pending

    async def wait(self, pending: PendingApproval) -> str:
        """Block until answered, or until the request expires.

        Expiry resolves as REFUSED-by-another-name rather than as an error: an
        unanswered question is not a yes, and the caller needs a definite outcome to
        report either way.
        """
        remaining = pending.expires_at - time.time()
        try:
            return await asyncio.wait_for(
                asyncio.shield(pending.future), timeout=max(0.0, remaining)
            )
        except TimeoutError:
            await self._finish(pending, Resolution.EXPIRED, by="timeout")
            return Resolution.EXPIRED
        except asyncio.CancelledError:
            # The turn was cancelled (HALT, disconnect). Leaving the request live would
            # let a later click execute something nobody is watching for any more.
            if pending.open:
                await self._finish(pending, Resolution.HALTED, by="cancelled")
            raise

    # ------------------------------------------------------------------- resolve

    async def resolve(self, approval_id: str, approved: bool, *, by: str = "user") -> str:
        """Answer a pending request. Unknown or already-answered ids are a no-op.

        Deliberately idempotent: a double-click, a retried WS frame or a stale UI must
        not be able to produce two grants for one question.
        """
        pending = self._pending.get(approval_id)
        if pending is None:
            log.warning("approval.unknown", approval=approval_id)
            return "unknown"
        if not pending.open:
            log.info("approval.already_resolved", approval=approval_id, was=pending.resolution)
            return pending.resolution or "unknown"

        if approved:
            # The grant is created HERE, from the digest the user was shown — not from
            # whatever the caller supplies at execution time.
            self._executor.grant(
                Approval(
                    approval_id=pending.id,
                    tool=pending.tool,
                    args_digest=pending.digest,
                    tier=pending.verdict.tier,
                    expires_at=pending.expires_at,
                )
            )
        return await self._finish(
            pending, Resolution.APPROVED if approved else Resolution.REFUSED, by=by
        )

    async def refuse_all(self, reason: str) -> int:
        """HALT. A stop that leaves approvals live is not a stop."""
        open_ones = [p for p in self._pending.values() if p.open]
        for pending in open_ones:
            await self._finish(pending, Resolution.HALTED, by=reason)
        return len(open_ones)

    async def _finish(self, pending: PendingApproval, resolution: str, *, by: str) -> str:
        pending.resolution = resolution
        if not pending.future.done():
            pending.future.set_result(resolution)
        await self._log.append(
            Event(
                type="approval.resolved",
                session_id=pending.session_id,
                turn_id=pending.turn_id,
                trace_id=pending.trace_id,
                actor=by,
                payload={
                    "approval_id": pending.id,
                    "tool": pending.tool,
                    "resolution": resolution,
                    "by": by,
                },
            )
        )
        log.info("approval.resolved", approval=pending.id, resolution=resolution, by=by)
        return resolution

    # --------------------------------------------------------------------- views

    def get(self, approval_id: str) -> PendingApproval | None:
        return self._pending.get(approval_id)

    def open_requests(self) -> list[dict[str, Any]]:
        self._sweep()
        return [p.wire() for p in self._pending.values() if p.open]

    def _sweep(self) -> None:
        """Drop answered requests once they are well past their TTL, so the dict does
        not grow for the life of the process."""
        cutoff = time.time() - self._ttl
        for key in [k for k, p in self._pending.items() if not p.open and p.created_at < cutoff]:
            self._pending.pop(key, None)


__all__ = ["DEFAULT_TTL_S", "ApprovalStore", "PendingApproval", "Resolution"]
