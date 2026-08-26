"""What happened while I wasn't looking.

docs/PROJECT_STATE.md §6 · [UI.md §7b](../../../docs/UI.md). The activity timeline answers
this question exhaustively, which is the wrong shape for it: the timeline is the debugging
surface, and the briefing is the **glance** surface — the delta since I last acknowledged,
grouped by project, bounded, readable in the three to five seconds
[VISION.md §2](../../../docs/VISION.md) allocates to it.

Four rules do the design's work, and each exists because the obvious alternative is worse:

1. **It advances on acknowledgement, never on render.** A briefing that clears itself on
   sight is a notification, and notifications are how people miss things. Glancing at the
   screen and walking away must not consume it.
2. **`waiting` is current state, not a delta.** Everything else here answers "what changed
   since seq N". A task parked on an approval does not: it is a *block*, and hiding it
   because it started before the watermark would mean acknowledging a briefing could bury
   the thing that most needs a person. It is therefore included unconditionally.
3. **No model is on this path.** Counts, outcomes, timings and cost are arithmetic over
   task rows. A summariser would add latency to the one surface with a 3-5 second budget,
   and would make a hallucinated account of the owner's own work possible. Prose belongs
   to the local mid-tier when one exists; this stays the fallback, because it is the
   version that is always correct.
4. **Empty is a real state.** "Nothing ran since 18:04" — not a placeholder, not a
   skeleton, and never a fabricated summary of nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

import aiosqlite

from oracle.core.project_state import (
    FAILED_STATUSES,
    TERMINAL_STATUSES,
    Project,
    ProjectStore,
)
from oracle.logsink import get_logger

log = get_logger(__name__)

#: Where the system section's watermark lives (migration 0007). Per-project watermarks
#: live on the project row; a daemon restart belongs to no project.
SYSTEM_SEQ_KEY: Final[str] = "briefing.system_seq"

#: Named lines per project. The briefing is a glance, not a report — anything longer
#: belongs in the timeline, and the count is stated so a reader knows there is more.
MAX_HIGHLIGHTS: Final[int] = 5

#: Event types the system section reports on. Deliberately short: this section competes
#: for attention with the projects, and everything here is something a person may need to
#: act on rather than merely know.
SYSTEM_TYPES: Final[frozenset[str]] = frozenset(
    {"system.degraded", "error", "graph.replan_exhausted", "system.boot", "system.shutdown"}
)

_WAITING: Final[str] = "waiting"


@dataclass(frozen=True)
class TaskLine:
    """One task, as the briefing names it."""

    id: str
    objective: str
    status: str
    agent: str | None = None
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATUSES


@dataclass(frozen=True)
class ProjectBrief:
    project: str
    status: str
    completed: int = 0
    failed: int = 0
    #: Current, never watermarked. See rule 2.
    waiting: int = 0
    #: Started and not finished — pending, ready or running. Current state like
    #: `waiting`, and present because "what is running now" is one of the six things
    #: VISION.md §2 requires the screen to answer in three to five seconds. A briefing
    #: that counted only outcomes would go blank in the middle of a long run, which is
    #: precisely when a person most wants to see something.
    in_flight: int = 0
    cancelled: int = 0
    elapsed_s: float = 0.0
    tokens: int = 0
    usd: float = 0.0
    highlights: tuple[TaskLine, ...] = ()
    #: Tasks beyond `MAX_HIGHLIGHTS`. Stated, never silently dropped.
    more: int = 0

    @property
    def empty(self) -> bool:
        return not (
            self.completed or self.failed or self.waiting or self.in_flight or self.cancelled
        )

    @property
    def needs_you(self) -> bool:
        return self.waiting > 0


@dataclass(frozen=True)
class SystemBrief:
    #: When the daemon last started, if it did so inside the window.
    restarted_at: str | None = None
    #: True when the last thing before that start was **not** a clean shutdown.
    #: ADR-0025's main risk is a background service failing invisibly; this is the
    #: mitigation, and it is why `system.shutdown` is emitted at all.
    unclean: bool = False
    #: Capabilities that reported themselves missing, e.g. "Ollama is not reachable".
    degraded: tuple[str, ...] = ()
    errors: int = 0

    @property
    def empty(self) -> bool:
        return not (self.restarted_at or self.degraded or self.errors)


@dataclass(frozen=True)
class Briefing:
    #: The highest sequence this briefing covers. Acknowledging sends it back, so a race
    #: with work arriving mid-render cannot mark unseen events as seen.
    through_seq: int
    since_ts: str | None = None
    projects: tuple[ProjectBrief, ...] = ()
    system: SystemBrief = field(default_factory=SystemBrief)

    @property
    def empty(self) -> bool:
        return self.system.empty and all(p.empty for p in self.projects)

    @property
    def needs_you(self) -> tuple[ProjectBrief, ...]:
        return tuple(p for p in self.projects if p.needs_you)


# --------------------------------------------------------------------------- reading


async def get_meta(conn: aiosqlite.Connection, key: str, default: str = "") -> str:
    async with conn.execute("SELECT value FROM meta WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
    return str(row["value"]) if row is not None else default


async def set_meta(conn: aiosqlite.Connection, key: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await conn.commit()


def _elapsed(started: str | None, finished: str | None) -> float:
    """Seconds between two ISO stamps, tolerantly.

    A task that never started, never finished, or carries a stamp from a schema this code
    has not seen contributes **zero** rather than raising. The briefing is a glance
    surface: a wrong duration is a nuisance, an exception is an outage.
    """
    if not started or not finished:
        return 0.0
    try:
        a = datetime.fromisoformat(started.replace("Z", "+00:00"))
        b = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (b - a).total_seconds())


def _cost(raw: str | None) -> tuple[int, float]:
    if not raw:
        return 0, 0.0
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return 0, 0.0
    cost = parsed.get("cost") if isinstance(parsed, dict) else None
    if not isinstance(cost, dict):
        return 0, 0.0
    tokens, usd = cost.get("tokens"), cost.get("usd")
    return (
        int(tokens) if isinstance(tokens, int) else 0,
        float(usd) if isinstance(usd, (int, float)) else 0.0,
    )


def _error_of(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    err = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(err, dict):
        return None
    message = str(err.get("message") or "")
    return message[:200] or None


async def _project_brief(
    conn: aiosqlite.Connection, project: Project, since_seq: int, through_seq: int
) -> ProjectBrief:
    """One project's delta, plus everything of its that is currently waiting."""
    async with conn.execute(
        "SELECT t.id, t.spec, t.status, t.agent, t.started_at, t.finished_at, t.result "
        "FROM tasks t WHERE t.project = ? AND ("
        "  EXISTS (SELECT 1 FROM events e"
        "          WHERE e.task_id = t.id AND e.seq > ? AND e.seq <= ?)"
        "  OR t.status = ?"  # rule 2: a block is current, not a delta
        ") ORDER BY COALESCE(t.finished_at, t.started_at, t.created_at) DESC, t.id",
        (project.name, since_seq, through_seq, _WAITING),
    ) as cur:
        rows = await cur.fetchall()

    completed = failed = waiting = in_flight = cancelled = tokens = 0
    elapsed = usd = 0.0
    lines: list[TaskLine] = []

    for row in rows:
        status = str(row["status"])
        if status == "succeeded":
            completed += 1
        elif status in FAILED_STATUSES:
            failed += 1
        elif status == _WAITING:
            waiting += 1
        elif status == "cancelled":
            cancelled += 1
        elif status not in TERMINAL_STATUSES:
            in_flight += 1
        elapsed += _elapsed(row["started_at"], row["finished_at"])
        t_tokens, t_usd = _cost(row["result"])
        tokens += t_tokens
        usd += t_usd

        objective = ""
        try:
            spec = json.loads(row["spec"])
            if isinstance(spec, dict):
                objective = str(spec.get("objective") or "")
        except (TypeError, ValueError):
            pass
        lines.append(
            TaskLine(
                id=str(row["id"]),
                objective=objective,
                status=status,
                agent=row["agent"],
                error=_error_of(row["result"]),
            )
        )

    # Waiting first, then failures: the two a person may need to act on, ahead of the
    # ones that merely happened. Same rule as the sidebar's "Waiting on me".
    lines.sort(key=lambda line: (line.status != _WAITING, not line.failed))

    return ProjectBrief(
        project=project.name,
        status=str(project.status),
        completed=completed,
        failed=failed,
        waiting=waiting,
        in_flight=in_flight,
        cancelled=cancelled,
        elapsed_s=elapsed,
        tokens=tokens,
        usd=usd,
        highlights=tuple(lines[:MAX_HIGHLIGHTS]),
        more=max(0, len(lines) - MAX_HIGHLIGHTS),
    )


async def _system_brief(
    conn: aiosqlite.Connection, since_seq: int, through_seq: int
) -> SystemBrief:
    """The daemon's own news. Chiefly: did it die while nobody was looking?

    A background service that fails invisibly is the named risk of
    [ADR-0025](../../../docs/DECISIONS.md), and `system.boot` / `system.shutdown` exist so
    the answer is a fact rather than an inference from a silent gap in the log — which is
    indistinguishable from "nothing happened".
    """
    placeholders = ", ".join("?" for _ in SYSTEM_TYPES)
    async with conn.execute(
        # S608 on the next line: `placeholders` is a run of `?`; every value is bound.
        f"SELECT seq, ts, type, payload FROM events "  # noqa: S608
        f"WHERE type IN ({placeholders}) AND seq > ? AND seq <= ? ORDER BY seq",
        (*sorted(SYSTEM_TYPES), since_seq, through_seq),
    ) as cur:
        rows = await cur.fetchall()

    restarted_at: str | None = None
    unclean = False
    degraded: list[str] = []
    errors = 0

    for row in rows:
        etype = str(row["type"])
        if etype == "system.boot":
            restarted_at = str(row["ts"])
            try:
                payload = json.loads(row["payload"])
                unclean = bool(payload.get("unclean")) if isinstance(payload, dict) else False
            except (TypeError, ValueError):
                unclean = False
        elif etype == "system.degraded":
            try:
                payload = json.loads(row["payload"])
                reason = str(payload.get("reason") or payload.get("detail") or "")
            except (TypeError, ValueError):
                reason = ""
            if reason and reason not in degraded:
                degraded.append(reason)
        elif etype in ("error", "graph.replan_exhausted"):
            errors += 1

    return SystemBrief(
        restarted_at=restarted_at,
        unclean=unclean,
        degraded=tuple(degraded),
        errors=errors,
    )


async def build(
    conn: aiosqlite.Connection, projects: ProjectStore, *, through_seq: int
) -> Briefing:
    """The whole briefing. `through_seq` is pinned by the caller — normally the event
    log's head at the moment of the request — so that work arriving mid-render is not
    silently marked as seen when the reader acknowledges."""
    tracked = await projects.all(include_archived=False)
    since = min((p.briefed_through_seq for p in tracked), default=0)

    briefs: list[ProjectBrief] = []
    for project in tracked:
        brief = await _project_brief(conn, project, project.briefed_through_seq, through_seq)
        if not brief.empty:
            briefs.append(brief)

    # Waiting first, then failures, then the rest — the sidebar's rule, applied to the
    # only other surface that ranks projects against each other.
    briefs.sort(key=lambda b: (not b.needs_you, b.failed == 0, b.project))

    system_seq = int(await get_meta(conn, SYSTEM_SEQ_KEY, "0") or 0)
    system = await _system_brief(conn, system_seq, through_seq)

    since_ts: str | None = None
    if since:
        async with conn.execute("SELECT ts FROM events WHERE seq = ?", (since,)) as cur:
            row = await cur.fetchone()
        since_ts = str(row["ts"]) if row is not None else None

    return Briefing(
        through_seq=through_seq,
        since_ts=since_ts,
        projects=tuple(briefs),
        system=system,
    )


async def acknowledge(
    conn: aiosqlite.Connection,
    projects: ProjectStore,
    *,
    through_seq: int,
    project_id: str | None = None,
) -> None:
    """Advance the watermark. **Only called from an explicit acknowledgement.**

    `project_id` acknowledges one project; `None` acknowledges everything, including the
    system section. Both are monotonic — `ProjectStore.acknowledge_briefing` takes a MAX —
    so a late acknowledgement carrying a lower sequence cannot re-show work already seen.
    """
    if project_id is not None:
        await projects.acknowledge_briefing(project_id, through_seq)
        log.info("briefing.acknowledged", project=project_id, through_seq=through_seq)
        return

    for project in await projects.all(include_archived=True):
        await projects.acknowledge_briefing(project.id, through_seq)
    current = int(await get_meta(conn, SYSTEM_SEQ_KEY, "0") or 0)
    await set_meta(conn, SYSTEM_SEQ_KEY, str(max(current, through_seq)))
    log.info("briefing.acknowledged", project="*", through_seq=through_seq)


# --------------------------------------------------------------------------- rendering


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{seconds / 3600:.1f}h"


def render(briefing: Briefing) -> str:
    """The deterministic template.

    Arithmetic over task rows — no model, so no latency on the glance path and no way for
    a summary of the owner's own work to be invented. This stays the permanent fallback
    even once a local summariser exists, because it is the version that is always right.
    """
    since = f" since {briefing.since_ts}" if briefing.since_ts else ""
    if briefing.empty:
        return f"Nothing ran{since or ' yet'}."

    out: list[str] = [f"Since{since or ' the beginning'}:"]

    for brief in briefing.projects:
        head = [f"{brief.project}"]
        counts = []
        if brief.waiting:
            counts.append(f"{brief.waiting} waiting on you")
        if brief.failed:
            counts.append(f"{brief.failed} failed")
        if brief.in_flight:
            counts.append(f"{brief.in_flight} running")
        if brief.completed:
            counts.append(f"{brief.completed} completed")
        if brief.cancelled:
            counts.append(f"{brief.cancelled} cancelled")
        if brief.elapsed_s:
            counts.append(_duration(brief.elapsed_s))
        if brief.usd:
            counts.append(f"${brief.usd:.2f}")
        head.append(" · ".join(counts))
        out.append("")
        out.append("  " + "  ".join(part for part in head if part))

        for line in brief.highlights:
            label = line.objective or "(no objective recorded)"
            suffix = f" — {line.error}" if line.error else ""
            out.append(f"    [{line.status}] {label}{suffix}")
        if brief.more:
            out.append(f"    …and {brief.more} more")

    system = briefing.system
    if not system.empty:
        out.append("")
        out.append("  System")
        if system.restarted_at:
            how = "stopped unexpectedly and restarted" if system.unclean else "restarted"
            out.append(f"    ORACLE {how} at {system.restarted_at}")
        for reason in system.degraded:
            out.append(f"    degraded: {reason}")
        if system.errors:
            out.append(f"    {system.errors} error(s) recorded")

    return "\n".join(out)


def wire(briefing: Briefing) -> dict[str, Any]:
    """The API shape. Every field is present on every response — a client must never have
    to read a missing key as a value."""
    return {
        "through_seq": briefing.through_seq,
        "since_ts": briefing.since_ts,
        "empty": briefing.empty,
        "text": render(briefing),
        "projects": [
            {
                "project": b.project,
                "status": b.status,
                "completed": b.completed,
                "failed": b.failed,
                "waiting": b.waiting,
                "in_flight": b.in_flight,
                "cancelled": b.cancelled,
                "elapsed_s": round(b.elapsed_s, 3),
                "tokens": b.tokens,
                "usd": round(b.usd, 6),
                "needs_you": b.needs_you,
                "more": b.more,
                "highlights": [
                    {
                        "id": line.id,
                        "objective": line.objective,
                        "status": line.status,
                        "agent": line.agent,
                        "error": line.error,
                    }
                    for line in b.highlights
                ],
            }
            for b in briefing.projects
        ],
        "system": {
            "restarted_at": briefing.system.restarted_at,
            "unclean": briefing.system.unclean,
            "degraded": list(briefing.system.degraded),
            "errors": briefing.system.errors,
        },
    }


__all__ = [
    "MAX_HIGHLIGHTS",
    "SYSTEM_SEQ_KEY",
    "SYSTEM_TYPES",
    "Briefing",
    "ProjectBrief",
    "SystemBrief",
    "TaskLine",
    "acknowledge",
    "build",
    "get_meta",
    "render",
    "set_meta",
    "wire",
]
