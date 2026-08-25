"""Workspace isolation for delegated agents (INTEGRATIONS.md §7).

**Delegated agents never work in the live project directory.** A git project gets a
disposable worktree on its own branch; a non-git project gets a snapshot copy, recorded
as a limitation rather than silently ignored. Two properties this buys, both essential:
verification is independent of the agent's own report (the diff and the tests are read
from the workspace, not from prose), and a bad run is free to discard.

**The scrub is the isolation.** ORACLE runs `claude -p` without `--bare` (auth,
INTEGRATIONS.md §3), so a `-p` session would load hooks from the target project's
`.claude/settings.json` and MCP servers from its `.mcp.json` — even in a folder never
trusted. Deleting both from the disposable copy before invocation closes that
materially: hooks cannot load from files that do not exist. `create_worktree` refuses
to hand back an unscrubbed workspace; there is no opt-out parameter, deliberately.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from oracle.integrations.types import Workspace
from oracle.logsink import get_logger

log = get_logger(__name__)

#: What the scrub removes from the disposable copy. The vendor reads both on startup.
SCRUB_ENTRIES = (".claude", ".mcp.json")

#: Junk that a snapshot copy skips: nothing a delegate needs, everything that makes a
#: copy slow. Mirrors the indexer's exclusions in spirit, not by import — a snapshot of
#: a non-git project must not depend on the RAG layer being configured.
SNAPSHOT_IGNORE = ("node_modules", ".git", "__pycache__", "target", "dist", ".venv", "venv")


class WorkspaceError(RuntimeError):
    """A git plumbing step failed. The message carries the command and its stderr,
    because 'worktree failed' without the why is a support ticket to yourself."""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(  # noqa: S603 - argv list, never a shell (AGENTS.md)
        ["git", "-C", str(repo), *args],  # noqa: S607 - resolved from PATH like everywhere else
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)}: exit {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def scrub(path: Path) -> list[str]:
    """Delete the vendor-config entry points from a disposable copy. Returns what was
    removed, for the log and the egress preview."""
    removed: list[str] = []
    for name in SCRUB_ENTRIES:
        target = path / name
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(name + "/")
        elif target.exists():
            target.unlink()
            removed.append(name)
    return removed


@dataclass
class Worktree:
    """A scrubbed, disposable working copy of a git project."""

    ws: Workspace
    repo: Path
    branch: str
    #: The commit the delegate started from — what `diff()` is measured against, so the
    #: result reflects what the *delegate* changed, never what was already in flight.
    base: str
    scrubbed: tuple[str, ...]

    def diff(self) -> str:
        """Tracked changes against base — committed or not, the same diff. The scrub is
        excluded: its deletions are ORACLE's doing, not the delegate's."""
        return _git(
            self.ws.path, "diff", self.base, "--", ".", *(f":(exclude){e}" for e in SCRUB_ENTRIES)
        )

    def untracked(self) -> list[str]:
        out = _git(self.ws.path, "ls-files", "--others", "--exclude-standard")
        return [line for line in out.splitlines() if line]

    def harvest(self, message: str) -> str | None:
        """Commit whatever the worker produced onto this worktree's own branch, and
        return the commit sha — or `None` if there was nothing to keep.

        **ORACLE commits; the delegate still may not.** The ban stands (a delegate that
        commits has hidden its own diff), and this runs *after* `diff()` has been read as
        evidence, so what is committed is exactly what was judged.

        Added in P7-T1 because a delegation's result used to exist only as long as its
        worktree did: delegates cannot commit, `discard()` deletes the checkout, and the
        change is gone. Harmless when a result is only evidence for one delegation; fatal
        for a graph, where task C's output is task D's input. Learned the hard way — the
        P6-T5 run lost its own artifact to exactly this
        (`logs/development/2026-08-24-p6t5-antigravity-planning.md`, finding 8).

        The scrub is *not* re-added: its deletions are ORACLE's doing, so committing them
        would put a vendor-config deletion in the worker's change set.
        """
        _git(self.ws.path, "add", "--all", "--", ".", *(f":(exclude){e}" for e in SCRUB_ENTRIES))
        staged = _git(self.ws.path, "diff", "--cached", "--name-only").strip()
        if not staged:
            log.info("workspace.harvest_empty", branch=self.branch)
            return None
        # `--no-verify`: hooks belong to the developer's own checkout, and this commit is
        # a record of what a worker produced, not a change anyone is proposing yet.
        _git(self.ws.path, "commit", "--no-verify", "-m", message)
        sha = _git(self.ws.path, "rev-parse", "HEAD").strip()
        log.info(
            "workspace.harvested",
            branch=self.branch,
            commit=sha[:12],
            files=len(staged.splitlines()),
        )
        return sha

    def discard(self, *, keep_branch: bool = False) -> None:
        """Remove the worktree and its branch. The real tree was never touched, so a
        bad run costs nothing — the property the whole design leans on.

        `keep_branch` is what makes `harvest()` worth anything: the checkout goes, the
        commit stays reachable. Deleting a branch that holds a harvested result would
        throw away the only copy."""
        _git(self.repo, "worktree", "remove", "--force", str(self.ws.path))
        if not keep_branch:
            _git(self.repo, "branch", "-D", self.branch)
        log.info("workspace.discarded", branch=self.branch, kept=keep_branch)


def create_worktree(repo: Path, task_id: str) -> Worktree:
    """`git worktree add .oracle/wt/<task-id> -b oracle/<task-id>`, base recorded,
    scrub applied — in that order, and the scrub is not optional."""
    path = repo / ".oracle" / "wt" / task_id
    branch = f"oracle/{task_id}"
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", str(path), "-b", branch)
    base = _git(path, "rev-parse", "HEAD").strip()
    removed = scrub(path)
    log.info(
        "workspace.worktree_created",
        task_id=task_id,
        branch=branch,
        base=base[:12],
        scrubbed=removed,
    )
    return Worktree(
        ws=Workspace(path=path), repo=repo, branch=branch, base=base, scrubbed=tuple(removed)
    )


@dataclass
class Snapshot:
    """The non-git fallback: a copy with a manifest, not a worktree. A limitation by
    design — change detection is by content hash, and there is no branch to keep. The
    recommendation to `git init` belongs in the UI, not silently here."""

    ws: Workspace
    source: Path
    #: rel path → blake2 digest at creation time.
    manifest: dict[str, str]
    scrubbed: tuple[str, ...]

    def changed(self) -> list[str]:
        """Relative paths added, modified or deleted since the snapshot was taken."""
        current = _digests(self.ws.path)
        return sorted(
            rel
            for rel in current.keys() | self.manifest.keys()
            if current.get(rel) != self.manifest.get(rel)
        )

    def discard(self) -> None:
        shutil.rmtree(self.ws.path)
        log.info("workspace.snapshot_discarded", source=str(self.source))


def create_snapshot(source: Path, task_id: str, scratch_root: Path) -> Snapshot:
    """Copy the project into scratch, scrub it, and record a manifest for the
    before/after comparison a worktree would have given us for free."""
    path = scratch_root / task_id
    if path.exists():
        shutil.rmtree(path)
    shutil.copytree(source, path, ignore=shutil.ignore_patterns(*SNAPSHOT_IGNORE))
    removed = scrub(path)
    manifest = _digests(path)
    log.info(
        "workspace.snapshot_created",
        task_id=task_id,
        source=str(source),
        files=len(manifest),
        scrubbed=removed,
    )
    return Snapshot(
        ws=Workspace(path=path), source=source, manifest=manifest, scrubbed=tuple(removed)
    )


def _digests(root: Path) -> dict[str, str]:
    from hashlib import blake2b

    out: dict[str, str] = {}
    for file in root.rglob("*"):
        if file.is_file():
            digest = blake2b(file.read_bytes(), digest_size=16).hexdigest()
            out[file.relative_to(root).as_posix()] = digest
    return out
