"""The toolhost wire protocol.

Deliberately small. What crosses this boundary is a **pre-authorised invocation** —
the decision has already been made, and the child gets no ability to revisit it
(ADR-0003).

What does NOT cross: policy, scopes, tiers, secrets, the audit log, the event log, or
any way back into the runtime. The child cannot widen its own permissions because it
has none to widen.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1


class Invocation(BaseModel):
    """Parent -> child. Already validated, already resolved, already permitted."""

    model_config = ConfigDict(extra="forbid")

    v: int = PROTOCOL_VERSION
    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    #: Absolute, canonicalised paths. The child never resolves a path itself; doing so
    #: would put the sandbox decision on the wrong side of the boundary.
    resolved: dict[str, str] = Field(default_factory=dict)
    #: Absolute paths of the allowlisted programs this call may spawn, pinned by the
    #: parent. The child never consults `PATH` and never looks up a program by name —
    #: same reason as `resolved` (docs/SECURITY.md#4b).
    programs: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    timeout_s: int = 30
    dry_run: bool = False


class Response(BaseModel):
    """Child -> parent."""

    model_config = ConfigDict(extra="forbid")

    v: int = PROTOCOL_VERSION
    id: str
    ok: bool
    result: dict[str, Any] | None = None
    error_kind: str | None = None
    error_message: str | None = None
    duration_ms: int = 0


class HostEvent(BaseModel):
    """Out-of-band notices on the same channel: readiness, streamed output, exit."""

    model_config = ConfigDict(extra="forbid")

    v: int = PROTOCOL_VERSION
    type: Literal["ready", "output", "log"]
    id: str | None = None
    stream: Literal["stdout", "stderr"] | None = None
    data: str = ""
    tools: list[str] = Field(default_factory=list)
