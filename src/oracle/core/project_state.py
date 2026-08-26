"""A project as a durable entity, and the reader for everything git owns.

docs/PROJECT_STATE.md · [ADR-0024]. Until this module existed a "project" was whatever
`discover_projects()` found on disk this boot: `memory_facts`, `memory_attempts` and
`TaskSpec` were all keyed by a project *string* with nothing behind the key, so "what did
we do to Asterim last?" had no answer and "continue Asterim" had nothing to read.

**The one rule.** There are two kinds of project state and conflating them is the failure
mode this module is shaped to avoid:

  * **Observed state** — branch, ahead/behind, dirty count, last commit, build commands.
    git and the filesystem own these. They are read fresh, every time, and **never
    stored**. A cached branch name is wrong the moment someone switches branches in their
    editor, silently, with no event that could correct it — and `git status` on a warm
    repository costs single-digit milliseconds, so the cache buys nothing and forfeits
    correctness. `ProjectObservation` is that read.
  * **Relational state** — what ORACLE attempted here, what it left unfinished, what it
    cost, when the owner last looked. Nothing else holds these, so they must be stored.
    `Project` is that row.

Stated once: **if git knows it, do not store it; if only ORACLE knows it, store it.**

Two boundaries worth naming, because both are load-bearing:

1. **Anything that spawns a process goes through `ToolExecutor`.** `observe()` reaches git
   via the `git.status` / `git.log` contracts, which cross the policy gate like every
   other call (ADR-0003). Direct filesystem *stat* does not — `detect_project` already
   reads marker files in-process and this module does the same for existence checks —
   because the gate exists for side effects and for spawning, not for `Path.is_dir()`.
2. **Registration grants nothing.** A row here is a label on work. Filesystem scopes live
   in `config/policy.yaml`, where a human edits them and git records the edit. If
   registering a project could widen a scope, "discover projects" would be privilege
   escalation with a friendly name — `tests/security/` asserts it cannot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from oracle.core.events import new_id, now_iso
from oracle.core.projects import ProjectInfo, detect_project
from oracle.logsink import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from oracle.tools.executor import ToolExecutor

log = get_logger(__name__)


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    ARCHIVED = "archived"
    MISSING = "missing"


class DescriptionSource(StrEnum):
    """Where `Project.description` came from.

    Kept because a description ORACLE derived from a README carries that README's taint,
    and a reader must be able to tell it from one a person wrote without guessing
    (docs/PROJECT_STATE.md §7).
    """

    USER = "user"
    DERIVED = "derived"


#: Task statuses that mean "finished". **Duplicated deliberately** from
#: `orchestration.models.TERMINAL` rather than imported: dependencies point downward
#: (ARCHITECTURE.md §4) and this module must not reach up into the supervisor — the same
#: reason `memory.attempts.from_task` takes an `Any`. `test_terminal_set_matches_orchestration`
#: asserts the two agree, so the duplication cannot drift silently.
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"succeeded", "failed", "timeout", "skipped", "cancelled"}
)
#: What "failed" means for a project counter. `TIMEOUT` is included here and is still not
#: the same thing as `FAILED` in the task table — a timed-out worker may well have done
#: the work — but from the briefing's point of view both are "this needs a person".
FAILED_STATUSES: Final[frozenset[str]] = frozenset({"failed", "timeout"})


class Project(BaseModel):
    """The durable half. Everything git can answer is deliberately absent."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    root: Path
    status: ProjectStatus = ProjectStatus.IDLE
    description: str = ""
    description_source: DescriptionSource = DescriptionSource.USER
    first_seen: str = Field(default_factory=now_iso)
    #: The last time ORACLE itself did something here. Not the last commit.
    last_touched: str | None = None
    #: Where the briefing resumes from (PROJECT_STATE.md §6).
    briefed_through_seq: int = 0

    #: A projection over `tasks`, never a source. See `ProjectStore.recount`.
    open_tasks: int = 0
    failed_tasks: int = 0
    tokens_spent: int = 0
    usd_spent: float = 0.0


@dataclass(frozen=True)
class ProjectObservation:
    """Read fresh, every time. Never persisted, never cached across a turn boundary.

    `error` is a **field, not an exception**: a project whose root was deleted, or which
    was never a git repository, has to render as a row that says so. `MISSING` in the
    sidebar is information; a crashed sidebar is not. This is the same lesson as the dead
    collection root that took the entire RAG watcher down with one absent path.
    """

    branch: str | None = None
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    #: staged + unstaged + untracked + conflicted, counted rather than listed. The
    #: sidebar needs a number; anything that needs the names calls `git.status` itself.
    dirty: int = 0
    clean: bool = True
    #: (sha, subject, iso-date) of HEAD, if the repository has one.
    last_commit: tuple[str, str, str] | None = None
    #: The existing marker-file classification: kinds, and argv for test/build/lint.
    detected: ProjectInfo | None = None
    #: `AGENTS.md` / `CLAUDE.md` and friends, if present. Names only — the contents are
    #: `local_foreign` and are read separately, by a caller that will taint the turn.
    agent_docs: tuple[str, ...] = ()
    error: str | None = None

    @property
    def is_repo(self) -> bool:
        return self.branch is not None


# --------------------------------------------------------------------------- store

_COLUMNS: Final[tuple[str, ...]] = (
    "id",
    "name",
    "root",
    "status",
    "description",
    "description_source",
    "first_seen",
    "last_touched",
    "briefed_through_seq",
    "open_tasks",
    "failed_tasks",
    "tokens_spent",
    "usd_spent",
)


def _present(root: Path) -> bool:
    """Does this project's directory exist right now?

    A **synchronous** stat, deliberately, and pulled out of the async methods that need it
    so the choice is visible rather than hidden behind a lint exemption. `oracled` runs a
    busy event loop, so blocking calls in it are a real hazard — but a single `is_dir()`
    on a local path costs microseconds, and `detect_project` already reads marker files
    the same way. Threading it would buy nothing and cost a thread hop per project per
    boot. If this ever grows to stat a tree, it moves to `asyncio.to_thread`.
    """
    return root.is_dir()


def _to_project(row: aiosqlite.Row) -> Project:
    return Project.model_validate(
        {
            "id": row["id"],
            "name": row["name"],
            "root": Path(row["root"]),
            "status": row["status"],
            "description": row["description"],
            "description_source": row["description_source"],
            "first_seen": row["first_seen"],
            "last_touched": row["last_touched"],
            "briefed_through_seq": row["briefed_through_seq"],
            "open_tasks": row["open_tasks"],
            "failed_tasks": row["failed_tasks"],
            "tokens_spent": row["tokens_spent"],
            "usd_spent": row["usd_spent"],
        }
    )


class ProjectNameTaken(Exception):
    """A different row already holds this name. Names are how the intent classifier
    resolves a project, so two rows answering to one name would make resolution
    ambiguous exactly where it must not be."""


class ProjectStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------ reads

    async def get(self, project_id: str) -> Project | None:
        async with self._conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)) as cur:
            row = await cur.fetchone()
        return _to_project(row) if row is not None else None

    async def by_name(self, name: str) -> Project | None:
        async with self._conn.execute("SELECT * FROM projects WHERE name = ?", (name,)) as cur:
            row = await cur.fetchone()
        return _to_project(row) if row is not None else None

    async def all(self, *, include_archived: bool = False) -> list[Project]:
        sql = "SELECT * FROM projects"
        params: tuple[Any, ...] = ()
        if not include_archived:
            sql += " WHERE status != ?"
            params = (str(ProjectStatus.ARCHIVED),)
        sql += " ORDER BY name"
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_to_project(r) for r in rows]

    # ------------------------------------------------------------------ writes

    async def register(
        self,
        name: str,
        root: Path,
        *,
        description: str = "",
        description_source: DescriptionSource = DescriptionSource.USER,
    ) -> Project:
        """Add a project. **Explicit, because discovery is only a suggestion.**

        `discover_projects()` lists directories; the projects root on this machine holds
        `New folder`, `docs.zip` and `Kaggle` alongside the real ones. Auto-registering
        every directory would fill the briefing with things nobody considers a project,
        and the briefing's entire value is that it is short.

        Idempotent by name: registering an existing name returns the existing row rather
        than raising, so a caller that registers on first use does not have to check
        first.
        """
        existing = await self.by_name(name)
        if existing is not None:
            return existing

        project = Project(
            id=new_id("pj"),
            name=name,
            root=root,
            status=ProjectStatus.IDLE if _present(root) else ProjectStatus.MISSING,
            description=description,
            description_source=description_source,
        )
        await self._insert(project)
        log.info("project.registered", project=name, id=project.id, root=str(root))
        return project

    async def _insert(self, project: Project) -> None:
        placeholders = ", ".join("?" for _ in _COLUMNS)
        await self._conn.execute(
            # S608: the interpolated parts are `_COLUMNS`, a module constant, and a run
            # of `?`. Every value is bound.
            f"INSERT INTO projects ({', '.join(_COLUMNS)}) VALUES ({placeholders})",  # noqa: S608
            (
                project.id,
                project.name,
                str(project.root),
                str(project.status),
                project.description,
                str(project.description_source),
                project.first_seen,
                project.last_touched,
                project.briefed_through_seq,
                project.open_tasks,
                project.failed_tasks,
                project.tokens_spent,
                project.usd_spent,
            ),
        )
        await self._conn.commit()

    async def rename(self, project_id: str, new_name: str) -> Project:
        """Re-point the label. **The id does not move.**

        Identity is the row, not the directory name — renaming `Asterim/` on disk must not
        orphan every fact and attempt recorded against it. That is the whole reason `id`
        exists as a separate column from `name`.
        """
        project = await self._require(project_id)
        clash = await self.by_name(new_name)
        if clash is not None and clash.id != project_id:
            raise ProjectNameTaken(f"{new_name!r} is already held by {clash.id}")
        await self._conn.execute(
            "UPDATE projects SET name = ? WHERE id = ?", (new_name, project_id)
        )
        await self._conn.commit()
        log.info("project.renamed", id=project_id, was=project.name, now=new_name)
        return project.model_copy(update={"name": new_name})

    async def relocate(self, project_id: str, root: Path) -> Project:
        """Point an existing project at a different directory, keeping its history.

        The counterpart to `rename`: a project that moved on disk is the same project.
        Re-registering under the new path would silently start a second history.
        """
        await self._require(project_id)
        status = ProjectStatus.IDLE if _present(root) else ProjectStatus.MISSING
        await self._conn.execute(
            "UPDATE projects SET root = ?, status = ? WHERE id = ?",
            (str(root), str(status), project_id),
        )
        await self._conn.commit()
        return await self._require(project_id)

    async def archive(self, project_id: str) -> Project:
        """Set aside, never delete. Hidden by default; its facts and attempts survive,
        because "what did we decide about this?" outlives caring about it day to day."""
        await self._require(project_id)
        await self._conn.execute(
            "UPDATE projects SET status = ? WHERE id = ?",
            (str(ProjectStatus.ARCHIVED), project_id),
        )
        await self._conn.commit()
        return await self._require(project_id)

    async def set_status(self, project_id: str, status: ProjectStatus) -> Project:
        await self._require(project_id)
        await self._conn.execute(
            "UPDATE projects SET status = ? WHERE id = ?", (str(status), project_id)
        )
        await self._conn.commit()
        return await self._require(project_id)

    async def touch(self, project_id: str, *, when: str | None = None) -> None:
        """Record that ORACLE did something here. Moves an `IDLE` project to `ACTIVE`;
        never resurrects an `ARCHIVED` one, because archiving is a human's decision and
        a background task should not undo it."""
        stamp = when or now_iso()
        await self._conn.execute(
            "UPDATE projects SET last_touched = ?,"
            " status = CASE WHEN status = ? THEN ? ELSE status END"
            " WHERE id = ?",
            (stamp, str(ProjectStatus.IDLE), str(ProjectStatus.ACTIVE), project_id),
        )
        await self._conn.commit()

    async def acknowledge_briefing(self, project_id: str, through_seq: int) -> None:
        """Advance the briefing pointer. **Called on acknowledgement, never on render.**

        A briefing that clears itself on sight is a notification, and notifications are
        how people miss things (PROJECT_STATE.md §6). Monotonic: a late acknowledgement
        carrying a lower sequence must not rewind a pointer a later one already advanced.
        """
        await self._conn.execute(
            "UPDATE projects SET briefed_through_seq = MAX(briefed_through_seq, ?) WHERE id = ?",
            (through_seq, project_id),
        )
        await self._conn.commit()

    async def refresh_presence(self) -> list[Project]:
        """Reconcile `MISSING` against the filesystem, both directions.

        A plain `Path.is_dir()` per row: read-only, no spawn, no gate — the same thing
        `detect_project` already does. Archived rows are left alone; an archived project
        whose directory is gone is not news.
        """
        changed: list[Project] = []
        for project in await self.all(include_archived=False):
            present = _present(project.root)
            if present and project.status is ProjectStatus.MISSING:
                changed.append(await self.set_status(project.id, ProjectStatus.IDLE))
            elif not present and project.status is not ProjectStatus.MISSING:
                log.warning("project.root_missing", project=project.name, root=str(project.root))
                changed.append(await self.set_status(project.id, ProjectStatus.MISSING))
        return changed

    # ------------------------------------------------------------------ counters

    async def recount(self, project_id: str) -> Project:
        """Rebuild the counters from `tasks`. **Recompute is always right.**

        The stored numbers are a projection, exactly as `tasks` is itself a projection the
        event log can rebuild (ADR-0010). A counter that disagrees with the task table is
        a bug in the projection and the repair is to run this — never to trust the
        counter.

        Reads through the `project` generated column, which *is* `spec` rather than a
        second copy of it, so it cannot drift from the task row.
        """
        project = await self._require(project_id)
        open_tasks = failed = tokens = 0
        usd = 0.0

        async with self._conn.execute(
            "SELECT status, result FROM tasks WHERE project = ?", (project.name,)
        ) as cur:
            rows = await cur.fetchall()

        for row in rows:
            status = str(row["status"])
            if status not in TERMINAL_STATUSES:
                open_tasks += 1
            if status in FAILED_STATUSES:
                failed += 1
            tokens_, usd_ = _cost_of(row["result"])
            tokens += tokens_
            usd += usd_

        await self._conn.execute(
            "UPDATE projects SET open_tasks = ?, failed_tasks = ?, tokens_spent = ?,"
            " usd_spent = ? WHERE id = ?",
            (open_tasks, failed, tokens, usd, project_id),
        )
        await self._conn.commit()
        return await self._require(project_id)

    async def recount_all(self) -> list[Project]:
        """Every project's counters, rebuilt. Cheap enough to run at boot: it is one
        indexed scan per project over a table that is a few thousand rows at most."""
        return [await self.recount(p.id) for p in await self.all(include_archived=True)]

    async def _require(self, project_id: str) -> Project:
        project = await self.get(project_id)
        if project is None:
            raise KeyError(f"no such project: {project_id!r}")
        return project


def _cost_of(raw: str | None) -> tuple[int, float]:
    """Tokens and dollars out of a persisted `TaskResult`, tolerantly.

    A result whose JSON is unreadable must not take a counter rebuild down with it: this
    runs over every task a project ever had, and one malformed row from an old schema
    would otherwise make the whole briefing unavailable.
    """
    if not raw:
        return 0, 0.0
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return 0, 0.0
    cost = parsed.get("cost") if isinstance(parsed, dict) else None
    if not isinstance(cost, dict):
        return 0, 0.0
    tokens = cost.get("tokens")
    usd = cost.get("usd")
    return (
        int(tokens) if isinstance(tokens, int) else 0,
        float(usd) if isinstance(usd, (int, float)) else 0.0,
    )


def effective_status(project: Project) -> ProjectStatus:
    """The stored status, corrected by a fresh existence check.

    Existence is **observed state** (PROJECT_STATE.md §2), so it is read rather than
    trusted. `refresh_presence()` reconciles the row at boot, but a directory deleted
    while the daemon is running would otherwise leave the sidebar saying `idle` about
    something that is gone — precisely the stale-cache failure this design exists to
    avoid, just with a coarser field than a branch name.

    `ARCHIVED` is never overridden: a project deliberately set aside is archived whether
    or not its directory survives, and reporting it as missing would invite someone to
    "fix" it.
    """
    if project.status is ProjectStatus.ARCHIVED:
        return project.status
    if not _present(project.root):
        return ProjectStatus.MISSING
    return ProjectStatus.IDLE if project.status is ProjectStatus.MISSING else project.status


# --------------------------------------------------------------------- observation


async def observe(
    executor: ToolExecutor,
    project: Project,
    *,
    detect: bool = True,
) -> ProjectObservation:
    """Everything git owns, read now.

    Two `git` calls (`git.status`, `git.log --limit 1`) through `ToolExecutor`, so they
    cross the policy gate like every other invocation. Both are **T0** — no side effect,
    in scope — so the cost is a process hop, not an approval card.

    Never raises. A missing root, a directory that is not a repository, and a denied path
    all come back as `error` on an otherwise-empty observation, because every caller of
    this is a surface that has to render *something*.
    """
    if not _present(project.root):
        return ProjectObservation(error="root does not exist")

    detected = detect_project(project.root, project.name) if detect else None
    docs = detected.agent_docs if detected else ()

    path = str(project.root)
    status = await executor.execute("git.status", {"path": path})
    if not status.ok or status.result is None:
        # Not a repository is the common case and is not a failure of ORACLE's.
        detail = status.error.message if status.error else "git.status failed"
        return ProjectObservation(detected=detected, agent_docs=docs, error=detail)

    s: Any = status.result
    dirty = len(s.staged) + len(s.unstaged) + len(s.untracked) + len(s.conflicted)

    last: tuple[str, str, str] | None = None
    log_outcome = await executor.execute("git.log", {"path": path, "limit": 1})
    if log_outcome.ok and log_outcome.result is not None:
        commits = getattr(log_outcome.result, "commits", [])
        if commits:
            head = commits[0]
            last = (head.short, head.subject, head.date)

    return ProjectObservation(
        branch=s.branch,
        upstream=s.upstream,
        ahead=s.ahead,
        behind=s.behind,
        dirty=dirty,
        clean=s.clean,
        last_commit=last,
        detected=detected,
        agent_docs=docs,
    )


__all__ = [
    "FAILED_STATUSES",
    "TERMINAL_STATUSES",
    "DescriptionSource",
    "Project",
    "ProjectNameTaken",
    "ProjectObservation",
    "ProjectStatus",
    "ProjectStore",
    "effective_status",
    "observe",
]
