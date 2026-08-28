"""The adapter seam (INTEGRATIONS.md §2).

Everything above this protocol talks in ORACLE's vocabulary; everything below it is
vendor-specific and disposable. A new agent is added by implementing this honestly —
`preflight()` especially, because the degradation path depends on it — and normalising
its events; nothing above the seam changes (INTEGRATIONS.md §9).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from oracle.integrations.types import (
    AgentCaps,
    AgentEvent,
    AgentHandle,
    AgentResult,
    HandoffPacket,
    Preflight,
    Workspace,
)


@runtime_checkable
class ExternalAgentAdapter(Protocol):
    id: str

    def capabilities(self) -> AgentCaps: ...

    async def preflight(self) -> Preflight:
        """Binary present? Authenticated? Version? Never raises — a missing vendor is a
        routing fact (use the fallback), not an exception."""
        ...

    async def submit(self, packet: HandoffPacket, ws: Workspace) -> AgentHandle: ...

    def events(self, h: AgentHandle) -> AsyncIterator[AgentEvent]:
        """Normalised progress, ending at the vendor's semantic end. Vendor kinds that
        have no place in ORACLE's vocabulary are logged and skipped, never fatal."""
        ...

    async def cancel(self, h: AgentHandle) -> None:
        """Graceful first, then escalate. Must leave no child process behind."""
        ...

    async def collect(self, h: AgentHandle) -> AgentResult: ...
