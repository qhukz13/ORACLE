"""`git.*` through the gate, across the process boundary.

These tests use a **real** git repository and the **real** toolhost. Both matter:

  * a faked `git` would encode the porcelain format we assumed rather than the one git
    emits, which is precisely the class of bug the parser can have;
  * a git tool must never run in-process. Without the Job Object, killing the runtime
    would not kill what git started, and HALT would be a lie (ADR-0003).

The acceptance criterion this file exists to prove: *"commit my changes with message X
works end to end and is undoable"*, plus the two refusals that keep it honest — a push
must ask, and an undo must not fire at a moved HEAD.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from oracle.config import Settings, set_settings
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.policy.model import Decision, Tier
from oracle.toolhost import ToolHost
from oracle.tools import ToolErrorKind, ToolExecutor, build_registry, git_undo_runner
from oracle.tools.undo import UndoJournal, UndoKind

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed on this machine")

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
  notes:
    roots:
      - {{ path: "{notes}", mode: rw }}
  deny_always:
    - "**/*.env"
programs:
  git:
    subcommands:
      allow:   [status, log]
      confirm: [commit, push]
      deny: ["push --force"]
tools:
  fs.read:    {{ tier: T0, scopes: [projects, notes] }}
  git.status: {{ tier: T0, scopes: [projects] }}
  git.diff:   {{ tier: T0, scopes: [projects] }}
  git.log:    {{ tier: T0, scopes: [projects] }}
  git.add:    {{ tier: T1, scopes: [projects] }}
  git.commit: {{ tier: T1, scopes: [projects] }}
  git.branch: {{ tier: T1, scopes: [projects] }}
  git.stash:  {{ tier: T1, scopes: [projects] }}
  git.push:   {{ tier: T2, scopes: [projects] }}
  git.undo:   {{ tier: T1, scopes: [projects] }}
"""


def _git(repo: Path, *args: str) -> str:
    assert GIT is not None
    r = subprocess.run([GIT, *args], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "Projects" / "Demo"
    root.mkdir(parents=True)
    _git(root, "init", "-b", "main")
    # Identity is set on the repo, not globally: the suite must not touch the machine's
    # git configuration, and a commit without one fails.
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "ORACLE Test")
    (root / "README.md").write_text("first\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial commit")

    set_settings(
        Settings(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            port=0,
            llm_enabled=False,
            prewarm_toolhost=False,
            watch_knowledge=False,
        )
    )
    return root


@pytest_asyncio.fixture
async def ex(tmp_path: Path, repo: Path) -> AsyncIterator[ToolExecutor]:
    notes = tmp_path / "Notes"
    notes.mkdir()
    p = tmp_path / "policy.yaml"
    p.write_text(
        POLICY.format(root=(tmp_path / "Projects").as_posix(), notes=notes.as_posix()),
        encoding="utf-8",
    )
    host = ToolHost()
    journal = UndoJournal(tmp_path / "undo.jsonl")
    executor = ToolExecutor(
        build_registry(),
        PolicyEngine(load_policy(p)),
        AuditLog(tmp_path / "audit.jsonl"),
        host=host,
        undo=journal,
    )
    journal.set_git_runner(git_undo_runner(executor))
    try:
        yield executor
    finally:
        await host.stop()


class TestStructuredNotScraped:
    async def test_status_reports_fields_not_prose(self, ex: ToolExecutor, repo: Path) -> None:
        (repo / "new.txt").write_text("x", encoding="utf-8")
        (repo / "README.md").write_text("changed\n", encoding="utf-8")

        out = await ex.execute("git.status", {"path": str(repo)})
        assert out.ok, out.error and out.error.message
        r = out.result
        assert r is not None
        assert r.branch == "main"  # type: ignore[attr-defined]
        assert "README.md" in r.unstaged  # type: ignore[attr-defined]
        assert "new.txt" in r.untracked  # type: ignore[attr-defined]
        assert r.clean is False  # type: ignore[attr-defined]

    async def test_log_returns_commits(self, ex: ToolExecutor, repo: Path) -> None:
        out = await ex.execute("git.log", {"path": str(repo), "limit": 5})
        assert out.ok
        commits = out.result.commits  # type: ignore[union-attr]
        assert len(commits) == 1
        assert commits[0].subject == "initial commit"

    async def test_diff_counts_lines(self, ex: ToolExecutor, repo: Path) -> None:
        (repo / "README.md").write_text("first\nsecond\n", encoding="utf-8")
        out = await ex.execute("git.diff", {"path": str(repo)})
        assert out.ok
        assert out.result.files_changed == 1  # type: ignore[union-attr]
        assert out.result.insertions == 1  # type: ignore[union-attr]


class TestCommitEndToEnd:
    """The acceptance criterion, in full."""

    async def test_add_commit_then_undo(self, ex: ToolExecutor, repo: Path) -> None:
        (repo / "feature.py").write_text("print('hi')\n", encoding="utf-8")

        add = await ex.execute("git.add", {"path": str(repo)})
        assert add.ok, add.error and add.error.message
        assert "feature.py" in add.result.staged  # type: ignore[union-attr]

        commit = await ex.execute("git.commit", {"path": str(repo), "message": "add the feature"})
        assert commit.ok, commit.error and commit.error.message
        sha = commit.result.sha  # type: ignore[union-attr]
        assert commit.result.files_changed == 1  # type: ignore[union-attr]

        # T1: it ran without an approval, because it is journalled and reversible.
        assert commit.verdict.decision is Decision.ALLOW
        assert commit.verdict.tier is Tier.T1
        assert commit.undo_id is not None

        journal = ex._undo
        assert journal is not None
        result = await journal.undo(commit.undo_id)
        assert result["kind"] == str(UndoKind.GIT_UNCOMMIT)

        # The commit is gone and the work is not: still staged, nothing lost.
        assert sha not in _git(repo, "log", "--format=%H")
        assert "feature.py" in _git(repo, "diff", "--cached", "--name-only")

    async def test_undo_refuses_when_head_has_moved(self, ex: ToolExecutor, repo: Path) -> None:
        """The dangerous case. "Undo the last commit" when the last commit is no longer
        ours would destroy work nobody asked us to touch, so the sha is checked."""
        (repo / "a.py").write_text("a\n", encoding="utf-8")
        await ex.execute("git.add", {"path": str(repo)})
        commit = await ex.execute("git.commit", {"path": str(repo), "message": "ours"})
        assert commit.ok

        (repo / "b.py").write_text("b\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "somebody else's commit")
        head_before = _git(repo, "rev-parse", "HEAD").strip()

        journal = ex._undo
        assert journal is not None
        with pytest.raises(Exception, match="HEAD has moved"):
            await journal.undo(commit.undo_id or "")

        assert _git(repo, "rev-parse", "HEAD").strip() == head_before

    async def test_commit_with_nothing_staged_says_so(self, ex: ToolExecutor, repo: Path) -> None:
        out = await ex.execute("git.commit", {"path": str(repo), "message": "empty"})
        assert not out.ok
        assert out.error is not None
        assert "nothing is staged" in out.error.message

    async def test_add_is_undoable(self, ex: ToolExecutor, repo: Path) -> None:
        (repo / "c.py").write_text("c\n", encoding="utf-8")
        add = await ex.execute("git.add", {"path": str(repo)})
        assert add.ok and add.undo_id is not None

        journal = ex._undo
        assert journal is not None
        await journal.undo(add.undo_id)
        assert _git(repo, "diff", "--cached", "--name-only").strip() == ""


class TestPushAsks:
    async def test_push_requires_approval_and_previews_the_exact_argv(
        self, ex: ToolExecutor, repo: Path
    ) -> None:
        """A push is T2 because it cannot be unpublished. The preview must bind to the
        same arguments that later execute, or approving a plan would approve a
        different act."""
        verdict, digest = ex.preview("git.push", {"path": str(repo), "remote": "origin"})
        assert verdict.decision is Decision.CONFIRM
        assert verdict.tier is Tier.T2

        out = await ex.execute("git.push", {"path": str(repo), "remote": "origin"})
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.APPROVAL_REQUIRED

        # And the digest is a function of the arguments: a different remote is a
        # different decision, so the approval cannot carry over.
        _, other = ex.preview("git.push", {"path": str(repo), "remote": "upstream"})
        assert digest != other

    async def test_push_is_not_declared_reversible(self) -> None:
        """If a push claimed reversibility it would slide to T1 and stop asking."""
        contract = build_registry().get("git.push")
        assert contract.reversible is False
        assert contract.risk is Tier.T2
        assert contract.dry_run is True


class TestScopeAndProgramRefusals:
    async def test_git_outside_the_projects_scope_is_refused(
        self, ex: ToolExecutor, tmp_path: Path
    ) -> None:
        notes = tmp_path / "Notes"
        out = await ex.execute("git.status", {"path": str(notes)})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED
        assert "git.status" in out.verdict.rule

    async def test_a_directory_that_is_not_a_repository_is_refused(
        self, ex: ToolExecutor, tmp_path: Path
    ) -> None:
        plain = tmp_path / "Projects" / "NotARepo"
        plain.mkdir()
        out = await ex.execute("git.status", {"path": str(plain)})
        assert not out.ok
        assert out.error is not None
        assert "not inside a git repository" in out.error.message

    async def test_git_tools_refuse_to_run_without_the_tool_host(
        self, tmp_path: Path, repo: Path
    ) -> None:
        """The in-process path is for tools that cannot spawn. Taking it here would
        mean a `git` ORACLE could not guarantee to kill."""
        p = tmp_path / "policy.yaml"
        p.write_text(
            POLICY.format(
                root=(tmp_path / "Projects").as_posix(), notes=(tmp_path / "Notes").as_posix()
            ),
            encoding="utf-8",
        )
        hostless = ToolExecutor(
            build_registry(),
            PolicyEngine(load_policy(p)),
            AuditLog(tmp_path / "audit2.jsonl"),
        )
        out = await hostless.execute("git.status", {"path": str(repo)})
        assert not out.ok
        assert out.error is not None
        assert "requires the tool host" in out.error.message


class TestBranchAndStash:
    async def test_create_branch_then_undo(self, ex: ToolExecutor, repo: Path) -> None:
        out = await ex.execute(
            "git.branch", {"path": str(repo), "action": "create", "name": "feature/x"}
        )
        assert out.ok, out.error and out.error.message
        assert out.result.current == "feature/x"  # type: ignore[union-attr]

        journal = ex._undo
        assert journal is not None
        assert out.undo_id is not None
        await journal.undo(out.undo_id)

        assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
        assert "feature/x" not in _git(repo, "branch", "--format=%(refname:short)")

    async def test_an_invalid_branch_name_is_refused(self, ex: ToolExecutor, repo: Path) -> None:
        out = await ex.execute(
            "git.branch", {"path": str(repo), "action": "create", "name": "bad name..~"}
        )
        assert not out.ok
        assert out.error is not None and "not a valid branch name" in out.error.message

    async def test_stash_save_then_undo_restores_the_changes(
        self, ex: ToolExecutor, repo: Path
    ) -> None:
        (repo / "README.md").write_text("work in progress\n", encoding="utf-8")
        out = await ex.execute("git.stash", {"path": str(repo), "action": "save"})
        assert out.ok, out.error and out.error.message
        assert (repo / "README.md").read_text(encoding="utf-8") == "first\n"

        journal = ex._undo
        assert journal is not None
        assert out.undo_id is not None
        await journal.undo(out.undo_id)
        assert (repo / "README.md").read_text(encoding="utf-8") == "work in progress\n"
