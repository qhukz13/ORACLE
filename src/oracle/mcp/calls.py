"""What a delegate's tool call does once it reaches the daemon.

This is the whole point of the MCP server (INTEGRATIONS.md §4): the delegate's calls go
through **the same `ToolExecutor.execute`** as everything else, so they obey the same
scopes and tiers, land in the same audit log, and appear in the same UI. There is no
second gate here, and adding one would be the bug this module exists to prevent.

What this layer adds on top of the executor, and only this:

* **Capability narrowing.** The token's allowlist and worktree are checked first, so a
  delegate cannot reach a tool it was not lent or a path outside its own workspace —
  even for a tool the *owner* could run there.
* **No prompting, ever.** A T2+ verdict is refused, not queued as an approval. An
  unattended delegate that could raise confirmation dialogs would be prompt fatigue as
  a service, and the owner is not sitting in front of a delegation for minutes at a
  time (SECURITY.md §2: the answer to fatigue is fewer prompts).
* **Attribution.** `tool.started` / `tool.finished` carry the `task_id` and an actor
  naming the delegate, so "who did this" stays answerable once a second agent exists.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oracle.core.eventlog import EventLog
from oracle.core.events import Event, new_id
from oracle.logsink import get_logger
from oracle.mcp.tokens import Capability, TokenError, TokenStore
from oracle.policy.model import Tier
from oracle.tools.executor import ToolExecutor

log = get_logger(__name__)

#: The ceiling for anything a delegate calls. T1 (reversible, journalled) is as far as
#: an unattended agent goes; T2 means "externally visible or costly" and that is a
#: decision for a human, not a background process.
MAX_TIER = Tier.T1

#: Argument fields that name a path. Checked against the capability's root before the
#: executor sees them — the gate would catch an out-of-scope path, but "out of scope"
#: and "outside YOUR worktree" are different refusals and the second is this layer's.
PATH_FIELDS = ("path", "repo", "file", "dir")


@dataclass(frozen=True)
class CallResult:
    ok: bool
    #: JSON-ready payload for the bridge. On refusal it is the reason, in the plain
    #: words a delegate can act on ("not permitted"), never the internal rule name.
    payload: dict[str, Any]


class McpCallHandler:
    """Executes a delegate's tool call, or refuses it. Owned by the daemon."""

    def __init__(
        self,
        tokens: TokenStore,
        executor: ToolExecutor,
        eventlog: EventLog,
        *,
        max_tier: Tier = MAX_TIER,
    ) -> None:
        self._tokens = tokens
        self._executor = executor
        self._log = eventlog
        self._max_tier = max_tier

    async def call(self, token: str, tool: str, args: dict[str, Any]) -> CallResult:
        try:
            cap = self._tokens.verify(token)
        except TokenError as exc:
            # The delegate learns "refused", not which check refused it.
            log.warning("mcp.token_rejected", tool=tool, reason=str(exc))
            return CallResult(False, {"error": "not permitted"})

        refusal = self._narrow(cap, tool, args)
        if refusal is not None:
            log.warning("mcp.call_refused", task_id=cap.task_id, tool=tool, reason=refusal)
            return CallResult(False, {"error": refusal})

        # The tier check needs the gate's own verdict, so it happens after preview and
        # before execute — the one place where knowing the answer changes what we do.
        try:
            verdict, _ = self._executor.preview(tool, args)
        except Exception as exc:
            log.info("mcp.preview_failed", task_id=cap.task_id, tool=tool, error=str(exc))
            return CallResult(False, {"error": f"{tool} cannot run with those arguments"})
        if verdict.tier > self._max_tier:
            log.warning("mcp.tier_refused", task_id=cap.task_id, tool=tool, tier=verdict.tier.label)
            return CallResult(
                False,
                {
                    "error": (
                        f"{tool} is {verdict.tier.label} and a delegated agent may not run it. "
                        "Ask for this in your result instead; a human decides it."
                    )
                },
            )

        trace = new_id("tr")
        await self._emit(
            "tool.started", cap, trace, {"tool": tool, "args": args, "tier": verdict.tier.label}
        )
        started = time.perf_counter()
        outcome = await self._executor.execute(tool, args)
        elapsed = round((time.perf_counter() - started) * 1000)

        await self._emit(
            "tool.finished",
            cap,
            trace,
            {
                "tool": tool,
                "ok": outcome.ok,
                "duration_ms": elapsed,
                "error": str(outcome.error) if outcome.error else None,
                "summary": f"delegated · {tool}",
            },
        )
        if not outcome.ok:
            return CallResult(False, {"error": str(outcome.error) if outcome.error else "failed"})
        return CallResult(True, outcome.result.model_dump() if outcome.result else {})

    # ------------------------------------------------------------------ narrowing

    def _narrow(self, cap: Capability, tool: str, args: dict[str, Any]) -> str | None:
        """The capability's own limits. Returns a refusal reason, or None to proceed."""
        if not cap.allows(tool):
            return f"{tool} was not lent to this delegation"
        for name in PATH_FIELDS:
            raw = args.get(name)
            if isinstance(raw, str) and raw and not cap.contains(Path(raw)):
                # Named precisely: a delegate that mistyped a path should be able to
                # correct it, and the worktree path is not a secret from its occupant.
                return f"{name} is outside this delegation's workspace ({cap.root})"
        return None

    async def _emit(
        self, event_type: str, cap: Capability, trace: str, payload: dict[str, Any]
    ) -> None:
        await self._log.append(
            Event(
                type=event_type,
                task_id=cap.task_id,
                trace_id=trace,
                # Not "system". Once a second agent can act, an unattributed action in
                # the log is a question nobody can answer later.
                actor="delegate",
                payload={**payload, "delegated": True},
            )
        )
