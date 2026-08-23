"""Selection helpers for packet inputs (INTEGRATIONS.md §6, steps 1-6).

Only the cheap, always-available steps live here so far. The retrieval-fed steps (top
hybrid hits for the goal, symbol neighbours) belong to the task that wires the
reference scenario end to end — they need a live knowledge store, and pretending
otherwise here would mean a mock dressed up as curation.
"""

from __future__ import annotations

from pathlib import Path

from oracle.handoff.packet import GitState
from oracle.integrations.workspace import WorkspaceError, _git


def gather_git_state(repo: Path, *, commits: int = 5, failing_tests: str = "") -> GitState:
    """Step 5: branch, uncommitted status, the last few commits. Never raises — a
    project without git history still deserves a packet, just a thinner STATE.md."""
    try:
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        status = _git(repo, "status", "--porcelain").rstrip()
        log = _git(repo, "log", f"-{commits}", "--format=%h %s").strip()
    except WorkspaceError:
        return GitState(failing_tests=failing_tests)
    return GitState(
        branch=branch,
        status=status,
        recent_commits=tuple(line for line in log.splitlines() if line),
        failing_tests=failing_tests,
    )
