"""Event envelope and the event-type vocabulary.

One vocabulary shared by the WS protocol, the log sink and the UI (docs/API.md,
docs/LOGGING.md). Clients MUST ignore unknown types so an older client survives a
newer server.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION: Final[int] = 1


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# Events that must never be dropped under backpressure. Everything else is
# coalescable: on a full queue we shed those first (docs/API.md#backpressure).
CRITICAL_TYPES: Final[frozenset[str]] = frozenset(
    {
        "session.created",
        "session.resync",
        "turn.started",
        "turn.finished",
        "agent.state",
        "approval.requested",
        "approval.resolved",
        "task.created",
        "task.updated",
        "task.finished",
        "error",
        "system.degraded",
    }
)

# Known types. Not enforced on ingest (forward compatibility), but used to catch typos
# in our own code via `is_known`.
KNOWN_TYPES: Final[frozenset[str]] = CRITICAL_TYPES | frozenset(
    {
        "message.delta",
        "message.completed",
        # One card per tool call in the UI. Coalescable rather than critical: a lost
        # card is a cosmetic gap, and `turn.finished` still closes the turn. The
        # *approval* for a tool call is the critical part, and that is a separate type.
        "tool.started",
        "tool.finished",
        # Terminal traffic. Coalescable: a dropped chunk of build output is a
        # gap in a log, and the backpressure rules shed these before anything
        # that carries a decision (docs/API.md#backpressure).
        "term.output",
        "term.opened",
        "term.closed",
        "log.entry",
        "system.metrics",
    }
)


def is_known(event_type: str) -> bool:
    return event_type in KNOWN_TYPES


class Event(BaseModel):
    """A persisted, sequenced fact. `seq` is assigned by the log on append."""

    model_config = ConfigDict(frozen=True)

    v: int = PROTOCOL_VERSION
    seq: int = 0
    ts: str = Field(default_factory=now_iso)
    type: str
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    trace_id: str
    actor: str = "system"
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def critical(self) -> bool:
        return self.type in CRITICAL_TYPES

    def wire(self) -> dict[str, Any]:
        """The exact shape sent to clients."""
        return {
            "v": self.v,
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "trace_id": self.trace_id,
            "payload": self.payload,
        }


class ClientCommand(BaseModel):
    """Client -> server frame. Correlated by `id`, not by `seq`."""

    model_config = ConfigDict(extra="forbid")

    v: int = PROTOCOL_VERSION
    id: str | None = None
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
