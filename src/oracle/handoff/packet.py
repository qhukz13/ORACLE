"""The Handoff Packet renderer (INTEGRATIONS.md §6): curated, redacted, budgeted.

The packet is the core abstraction of delegation — every adapter renders *from* it,
and the fallback simply is it, written to disk. This module owns the last three steps
of context assembly, the ones with teeth:

  7  REDACT   every piece of text that will leave the machine, before rendering, via
              the same `redact_text` the log sink uses — one scanner, no second
              opinion about what a secret looks like (SECURITY.md §7)
  8  BUDGET   an explicit ceiling (30k tokens by default), not "as much as fits":
              excerpts are dropped whole, lowest-priority first, and the drop is
              *recorded* in the packet so the delegate knows context was cut
  9  the egress preview reads its numbers from `WrittenPacket` — files, tokens,
              redactions — so what the owner approves is what was actually rendered

Steps 1-6 (selection) produce this module's *inputs*; they are curation policy, not
rendering, and they live with the retrieval and git layers that own the data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from oracle.context.tokens import DEFAULT_COUNTER, TokenCounter
from oracle.integrations.types import HandoffPacket
from oracle.logsink import get_logger
from oracle.logsink.redact import redact_text

log = get_logger(__name__)

#: The default ceiling. A repository dump wastes the delegate's context, costs money,
#: and buries the signal — the cap is what makes "curated, not dumped" enforceable.
BUDGET_TOKENS = 30_000

#: The six files, in the order the delegate should read them.
FILENAMES = ("TASK.md", "CONTEXT.md", "ATTEMPTS.md", "FILES.md", "STATE.md", "packet.json")


class ContextExcerpt(BaseModel):
    """A curated excerpt with its source — NOT a repo dump. `priority` orders eviction
    when the budget bites: higher survives longer."""

    model_config = ConfigDict(frozen=True)
    source: str
    text: str
    reason: str = ""
    priority: int = 0


class FileEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    reason: str = ""


class Attempt(BaseModel):
    """What was tried before and why it failed — the field that stops a delegate from
    repeating last week's dead end (MEMORY.md §4)."""

    model_config = ConfigDict(frozen=True)
    date: str
    agent: str
    summary: str


class GitState(BaseModel):
    model_config = ConfigDict(frozen=True)
    branch: str = ""
    status: str = ""
    recent_commits: tuple[str, ...] = ()
    failing_tests: str = ""


class PacketOverBudget(Exception):
    """The packet exceeds the ceiling with every droppable excerpt already dropped.
    The task description itself is too big — that is an authoring error upstream, and
    silently truncating TASK.md would hand the delegate half a goal."""


@dataclass(frozen=True)
class WrittenPacket:
    """What landed on disk — the egress preview's ground truth."""

    directory: Path
    files: tuple[str, ...]
    tokens: int
    redactions: tuple[str, ...]
    dropped_excerpts: int


class _Redactor:
    """One pass over every outbound string, done *before* rendering so the budget loop
    can re-render without double-counting redactions. Entropy scanning is ON, unlike
    the log hot path: this text is about to leave the machine, and a false positive is
    visible in the preview while a false negative is an exfiltrated credential."""

    def __init__(self) -> None:
        self.fired: list[str] = []

    def clean(self, text: str, where: str) -> str:
        out, labels = redact_text(text, entropy=True)
        self.fired.extend(f"{where}: {label}" for label in labels)
        return out


def write_packet(
    packet: HandoffPacket,
    root: Path,
    *,
    excerpts: tuple[ContextExcerpt, ...] = (),
    files: tuple[FileEntry, ...] = (),
    attempts: tuple[Attempt, ...] = (),
    state: GitState | None = None,
    budget_tokens: int = BUDGET_TOKENS,
    counter: TokenCounter = DEFAULT_COUNTER,
) -> WrittenPacket:
    """Render `.oracle/handoff/<task-id>/` — redacted, budgeted, attributed.

    Idempotent per task_id: an existing directory is re-rendered in place, because the
    packet is a projection of its inputs, never a store.
    """
    r = _Redactor()
    clean_packet = packet.model_copy(
        update={
            "task": r.clean(packet.task, "TASK.md"),
            "acceptance": tuple(r.clean(a, "TASK.md") for a in packet.acceptance),
            "constraints": tuple(r.clean(c, "TASK.md") for c in packet.constraints),
        }
    )
    kept = sorted(excerpts, key=lambda e: e.priority, reverse=True)
    clean_excerpts = [e.model_copy(update={"text": r.clean(e.text, e.source)}) for e in kept]
    clean_attempts = tuple(
        a.model_copy(update={"summary": r.clean(a.summary, "ATTEMPTS.md")}) for a in attempts
    )
    s = state or GitState()
    clean_state = s.model_copy(
        update={
            "status": r.clean(s.status, "STATE.md"),
            "recent_commits": tuple(r.clean(c, "STATE.md") for c in s.recent_commits),
            "failing_tests": r.clean(s.failing_tests, "STATE.md"),
        }
    )

    dropped = 0
    while True:
        rendered = _render(
            clean_packet, clean_excerpts, files, clean_attempts, clean_state, dropped
        )
        tokens = sum(counter.count(body) for body in rendered.values())
        if tokens <= budget_tokens:
            break
        if not clean_excerpts:
            raise PacketOverBudget(
                f"{packet.task_id}: {tokens} tokens with zero excerpts against a "
                f"budget of {budget_tokens} — the task description itself is too big"
            )
        clean_excerpts.pop()  # lowest priority is last after the sort above
        dropped += 1

    directory = root / packet.task_id
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in rendered.items():
        (directory / name).write_text(body, encoding="utf-8", newline="\n")

    log.info(
        "handoff.packet_written",
        task_id=packet.task_id,
        tokens=tokens,
        files=len(rendered),
        redactions=len(r.fired),
        dropped_excerpts=dropped,
    )
    return WrittenPacket(
        directory=directory,
        files=tuple(rendered),
        tokens=tokens,
        redactions=tuple(r.fired),
        dropped_excerpts=dropped,
    )


# -- rendering (pure: inputs are already redacted) ---------------------------


def _render(
    packet: HandoffPacket,
    excerpts: list[ContextExcerpt],
    files: tuple[FileEntry, ...],
    attempts: tuple[Attempt, ...],
    state: GitState,
    dropped: int,
) -> dict[str, str]:
    task_md = [f"# TASK\n\n{packet.task}\n"]
    if packet.acceptance:
        task_md.append("\n## Acceptance criteria\n\n")
        task_md.extend(f"- [ ] {a}\n" for a in packet.acceptance)
    if packet.constraints:
        task_md.append("\n## Constraints\n\n")
        task_md.extend(f"- {c}\n" for c in packet.constraints)

    context_md = ["# CONTEXT\n\nCurated excerpts with sources — not a repository dump.\n"]
    if dropped:
        context_md.append(
            f"\n> {dropped} lower-priority excerpt(s) were dropped to fit the token "
            "budget. Ask for them by source if something is missing.\n"
        )
    for e in excerpts:
        header = f"\n## {e.source}" + (f" — {e.reason}" if e.reason else "") + "\n\n"
        context_md.append(header + e.text.rstrip() + "\n")

    attempts_md = ["# PRIOR ATTEMPTS\n"]
    if attempts:
        attempts_md.extend(f"\n- {a.date}, {a.agent}: {a.summary}\n" for a in attempts)
    else:
        attempts_md.append("\nNone recorded. This is the first attempt.\n")

    files_md = ["# FILES\n\nThe files that matter, and why each is included.\n\n"]
    files_md.extend(f"- `{f.path}` — {f.reason}\n" for f in files)

    state_md = [
        "# STATE\n\n",
        f"- branch: `{state.branch or 'unknown'}`\n",
        "\n## git status\n\n```\n" + state.status + "\n```\n",
    ]
    if state.recent_commits:
        state_md.append("\n## recent commits\n\n")
        state_md.extend(f"- {c}\n" for c in state.recent_commits)
    if state.failing_tests:
        state_md.append("\n## failing tests\n\n```\n" + state.failing_tests + "\n```\n")

    machine = {
        "task_id": packet.task_id,
        "task": packet.task,
        "acceptance": list(packet.acceptance),
        "constraints": list(packet.constraints),
        "allowed_tools": list(packet.allowed_tools),
        "result_schema": packet.result_schema,
        "files": [f.model_dump() for f in files],
        "excerpts": [e.model_dump() for e in excerpts],
        "attempts": [a.model_dump() for a in attempts],
        "state": state.model_dump(),
        "dropped_excerpts": dropped,
    }

    return {
        "TASK.md": "".join(task_md),
        "CONTEXT.md": "".join(context_md),
        "ATTEMPTS.md": "".join(attempts_md),
        "FILES.md": "".join(files_md),
        "STATE.md": "".join(state_md),
        "packet.json": json.dumps(machine, indent=2, ensure_ascii=False) + "\n",
    }
