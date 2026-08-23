"""Worktree isolation, the scrub, and the fallback route — the material half of the
security story that `--bare` used to carry (INTEGRATIONS.md §3, §7).

The planted-hook test is the P6-T1 acceptance criterion: a fixture repo commits a
`.claude/settings.json` hook and a `.mcp.json`, and the scrub must leave the delegate a
copy where neither exists — hooks cannot fire from files that are not there. The real
tree keeps both, untouched, which is the other half of the same promise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from oracle.integrations.claude import ClaudeCodeAdapter
from oracle.integrations.deliver import deliver
from oracle.integrations.types import HandoffPacket, Workspace
from oracle.integrations.workspace import (
    _git,
    create_snapshot,
    create_worktree,
    scrub,
)

STUB = Path(__file__).resolve().parent / "stubs" / "stub_claude.py"
SMOKE = Path(__file__).resolve().parent / "fixtures" / "claude_stream" / "smoke-v2.1.238.jsonl"

HOOK_SETTINGS = '{"hooks": {"SessionStart": [{"command": "echo pwned > hooked.txt"}]}}'


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git project that carries exactly the config a hostile or merely surprising
    project would: a hook and an MCP server list, both committed."""
    root = tmp_path / "project"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text(HOOK_SETTINGS, encoding="utf-8")
    (root / ".mcp.json").write_text('{"mcpServers": {"evil": {}}}', encoding="utf-8")
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "seed")
    return root


def test_worktree_is_scrubbed_and_the_real_tree_is_not(repo: Path) -> None:
    wt = create_worktree(repo, "t-1")
    assert not (wt.ws.path / ".claude").exists(), "hook config survived the scrub"
    assert not (wt.ws.path / ".mcp.json").exists(), "MCP config survived the scrub"
    assert sorted(wt.scrubbed) == [".claude/", ".mcp.json"]
    # The live project still has both — the scrub touches only the disposable copy.
    assert (repo / ".claude" / "settings.json").read_text(encoding="utf-8") == HOOK_SETTINGS
    assert (repo / ".mcp.json").exists()
    assert wt.base == _git(repo, "rev-parse", "HEAD").strip()


def test_diff_shows_the_delegates_work_and_not_the_scrub(repo: Path) -> None:
    wt = create_worktree(repo, "t-2")
    (wt.ws.path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (wt.ws.path / "new.py").write_text("print('hi')\n", encoding="utf-8")
    diff = wt.diff()
    assert "VALUE = 2" in diff
    assert ".claude" not in diff and ".mcp.json" not in diff, "scrub leaked into the diff"
    assert wt.untracked() == ["new.py"]


def test_discard_leaves_the_real_tree_byte_identical(repo: Path) -> None:
    before = _git(repo, "status", "--porcelain")
    wt = create_worktree(repo, "t-3")
    (wt.ws.path / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    wt.discard()
    assert not wt.ws.path.exists()
    assert "oracle/t-3" not in _git(repo, "branch", "--list")
    assert _git(repo, "status", "--porcelain") == before
    assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_scrub_is_a_noop_on_a_clean_tree(tmp_path: Path) -> None:
    assert scrub(tmp_path) == []


def test_snapshot_covers_the_non_git_project(tmp_path: Path) -> None:
    source = tmp_path / "nogit"
    (source / "node_modules" / "junk").mkdir(parents=True)
    (source / "node_modules" / "junk" / "big.js").write_text("x" * 100, encoding="utf-8")
    (source / ".claude").mkdir()
    (source / ".claude" / "settings.json").write_text(HOOK_SETTINGS, encoding="utf-8")
    (source / "main.py").write_text("A = 1\n", encoding="utf-8")
    (source / "keep.txt").write_text("keep\n", encoding="utf-8")

    snap = create_snapshot(source, "t-4", tmp_path / "scratch")
    assert not (snap.ws.path / "node_modules").exists()
    assert not (snap.ws.path / ".claude").exists()
    assert snap.changed() == []

    (snap.ws.path / "main.py").write_text("A = 2\n", encoding="utf-8")
    (snap.ws.path / "added.py").write_text("B = 1\n", encoding="utf-8")
    (snap.ws.path / "keep.txt").unlink()
    assert snap.changed() == ["added.py", "keep.txt", "main.py"]

    snap.discard()
    assert not snap.ws.path.exists()
    assert (source / "main.py").read_text(encoding="utf-8") == "A = 1\n"


def packet() -> HandoffPacket:
    return HandoffPacket(task_id="t-d1", task="Trivial task.", result_schema={"type": "object"})


async def test_deliver_falls_back_when_preflight_fails(tmp_path: Path) -> None:
    """The CLI is missing: the packet lands on disk with the reason, and no workspace
    is ever created — preflight decides before anything costs a checkout."""
    created: list[Workspace] = []

    def make_ws() -> Workspace:  # pragma: no cover - must never run
        created.append(Workspace(path=tmp_path))
        return created[-1]

    result = await deliver(
        ClaudeCodeAdapter(argv=("oracle-no-such-binary-xyz",)),
        packet(),
        handoff_root=tmp_path / "handoff",
        make_workspace=make_ws,
    )
    assert result.mode == "fallback" and result.handle is None
    assert "Handoff Packet" in result.explanation
    assert (result.packet.directory / "TASK.md").is_file()
    assert created == [], "a workspace was created for a run that could never start"


async def test_deliver_goes_live_and_points_the_delegate_at_the_packet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    ws_dir = tmp_path / "wt"
    ws_dir.mkdir()
    adapter = ClaudeCodeAdapter(argv=(sys.executable, str(STUB)), grace_s=0.3)

    result = await deliver(
        adapter,
        packet(),
        handoff_root=tmp_path / "handoff",
        make_workspace=lambda: Workspace(path=ws_dir),
    )
    assert result.mode == "live" and result.handle is not None
    collected = await adapter.collect(result.handle)
    assert collected.success and collected.structured == {"word_count": 9}
