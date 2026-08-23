"""External-agent vocabulary (INTEGRATIONS.md §2).

Vendor shapes stop at the adapter boundary, exactly as L8 does for LLM providers:
nothing above `integrations` knows what Claude's stream-json looks like. That is what
keeps the UI, the event log and the task inspector identical regardless of which agent
ran — and what makes the fallback (a packet on disk, no adapter at all) a first-class
member of the same family rather than a special case.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class AgentEventKind(StrEnum):
    """ORACLE's own event vocabulary. Vendor event shapes are normalised into these at
    the adapter boundary and never leak upward. `RETRYING` exists because the vendor
    stream reports transient API retries, and surfacing one as an error would make the
    UI cry wolf about runs that are about to succeed."""

    STARTED = "started"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TEXT = "text"
    RETRYING = "retrying"
    ERROR = "error"
    FINISHED = "finished"


class AgentEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: AgentEventKind
    text: str = ""
    #: Set for TOOL_USE only: the vendor's tool name, verbatim — it names *their* tool,
    #: not one of ORACLE's 33.
    tool: str | None = None
    #: Output of the delegate's own subagent (`parent_tool_use_id` in the vendor
    #: stream). The task inspector indents these.
    from_subagent: bool = False


class AgentCaps(BaseModel):
    model_config = ConfigDict(frozen=True)
    streaming: bool
    resume: bool
    structured_output: bool
    workspace_scoped: bool
    cost_reporting: bool


class Preflight(BaseModel):
    """What makes degradation graceful: if the binary is missing or unauthenticated,
    ORACLE knows *before* building a packet and routes to the fallback with a clear
    explanation instead of failing halfway through (INTEGRATIONS.md §2)."""

    model_config = ConfigDict(frozen=True)
    ok: bool
    version: str | None = None
    reason: str | None = None
    remedy: str | None = None


class HandoffPacket(BaseModel):
    """The vendor-neutral task description (INTEGRATIONS.md §6).

    P6-T1 requirement 3 grows this into the six-file on-disk form with curated context;
    the adapter needs only what reaches the command line. `task_id` names the handoff
    directory, the worktree and the branch, so it is part of the packet rather than an
    argument riding beside it."""

    model_config = ConfigDict(frozen=True)
    task_id: str
    task: str
    constraints: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    #: Prefix-matched vendor rule syntax, e.g. ``Bash(git diff *)``. The space before
    #: ``*`` matters: ``Bash(git diff*)`` would also match ``git diff-index``.
    allowed_tools: tuple[str, ...] = ("Read",)
    #: JSON Schema for the structured result. Never parse structure out of prose.
    result_schema: dict[str, Any] | None = None
    #: Where the rendered six-file packet lives, once written. Set by the delivery
    #: layer; the prompt then points the delegate at the files instead of inlining
    #: them — piped stdin is capped at 10 MB and the prompt is worse.
    context_dir: str | None = None

    def render_prompt(self) -> str:
        """The `-p` argument. Large context never rides here — it goes to disk as the
        rendered packet, and the prompt carries directions to it."""
        parts = [self.task]
        if self.acceptance:
            parts.append("Acceptance criteria:\n" + "\n".join(f"- {a}" for a in self.acceptance))
        if self.constraints:
            parts.append("Constraints:\n" + "\n".join(f"- {c}" for c in self.constraints))
        if self.context_dir:
            parts.append(
                "Before starting, read the task brief and curated context in "
                f"{self.context_dir}: TASK.md, CONTEXT.md, FILES.md, STATE.md, ATTEMPTS.md."
            )
        return "\n\n".join(parts)


@dataclass
class Workspace:
    """An isolated working copy. Requirement 4 owns creation, the scrub, and disposal;
    an adapter only ever runs *inside* one and never in the live project directory."""

    path: Path


@dataclass
class AgentHandle:
    """A running delegation. Mutable on purpose: `events()` stashes what `collect()`
    reports, so the result reflects the stream actually seen, not a second parse."""

    task_id: str
    proc: asyncio.subprocess.Process
    session_id: str | None = None
    #: The vendor `result` event, verbatim, once seen. Its presence is what "the run
    #: reached a semantic end" means — trailing housekeeping events do not count.
    result: dict[str, Any] | None = None
    stderr: bytearray = field(default_factory=bytearray)
    #: The stderr pump task; held here so it is neither garbage-collected mid-run nor
    #: leaked past `collect()`.
    pump: asyncio.Task[None] | None = None
    drained: bool = False


class AgentResult(BaseModel):
    """What `collect()` returns. Success is ORACLE's judgement (exit code + the stream's
    own error flag), never the agent's prose claim — the *verified* half (diff, tests)
    is requirement 4's job and rides beside this, not inside it."""

    model_config = ConfigDict(frozen=True)
    success: bool
    exit_code: int
    result_text: str = ""
    structured: dict[str, Any] | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    session_id: str | None = None
    stderr_tail: str = ""
