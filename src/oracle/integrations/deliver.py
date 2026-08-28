"""Live delegation or the packet on disk — decided by preflight, never by failure.

ROADMAP Phase 6 acceptance: *"If the CLI is missing, the fallback packet path engages
automatically with a clear explanation."* The decision happens **before** a workspace
is created or a payload built for egress — `preflight()` is exactly the honest early
signal the adapter protocol demands — and both paths render the *same* packet, because
the fallback is not a degraded copy of delegation; it is the packet itself
(INTEGRATIONS.md §6), left where a human or another tool can pick it up.

What this module deliberately does not do: show the egress preview (P6-T2's UI owns
approval; callers must gate `mode == "live"` behind it) and watch the fallback packet
for an external diff (the same task owns that loop).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
from oracle.integrations.types import AgentHandle, HandoffPacket, Workspace
from oracle.logsink import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Delegation:
    """What `deliver` produced. `live` carries a running handle; `fallback` carries
    only the packet directory and the reason there is no handle."""

    mode: Literal["live", "fallback"]
    packet: WrittenPacket
    handle: AgentHandle | None = None
    explanation: str = ""


async def deliver(
    adapter: ExternalAgentAdapter,
    packet: HandoffPacket,
    *,
    handoff_root: Path,
    make_workspace: Callable[[], Workspace],
    excerpts: tuple[ContextExcerpt, ...] = (),
    files: tuple[FileEntry, ...] = (),
    attempts: tuple[Attempt, ...] = (),
    state: GitState | None = None,
    budget_tokens: int = BUDGET_TOKENS,
) -> Delegation:
    """Render the packet, then delegate live if preflight allows — or leave the packet
    on disk with the reason it stayed there.

    `make_workspace` is a callable, not a workspace: creating a worktree costs a git
    checkout and a scrub, and a preflight that fails must cost nothing but the packet.
    """
    written = write_packet(
        packet,
        handoff_root,
        excerpts=excerpts,
        files=files,
        attempts=attempts,
        state=state,
        budget_tokens=budget_tokens,
    )

    pre = await adapter.preflight()
    if not pre.ok:
        explanation = ". ".join(part for part in (pre.reason, pre.remedy) if part)
        log.info(
            "delegate.fallback",
            adapter=adapter.id,
            task_id=packet.task_id,
            reason=pre.reason,
        )
        return Delegation(mode="fallback", packet=written, explanation=explanation)

    ws = make_workspace()
    live_packet = packet.model_copy(update={"context_dir": str(written.directory)})
    handle = await adapter.submit(live_packet, ws)
    return Delegation(mode="live", packet=written, handle=handle)
