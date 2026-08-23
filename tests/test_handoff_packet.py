"""The packet renderer's two teeth: redaction that reaches every file, and a budget
that is a ceiling rather than a hope.

The planted-secret test is a ROADMAP Phase 6 acceptance criterion — *"a planted secret
in a candidate context file is redacted before the preview renders"* — asserted here at
the layer that renders, so no future preview can show a byte the scanner never saw.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oracle.handoff.packet import (
    FILENAMES,
    Attempt,
    ContextExcerpt,
    FileEntry,
    GitState,
    PacketOverBudget,
    write_packet,
)
from oracle.integrations.types import HandoffPacket

PLANTED_KEY = "sk-ant-api03-forged0000000000000000000"
PLANTED_ASSIGNMENT = "password = hunter2secret99"


def packet() -> HandoffPacket:
    return HandoffPacket(
        task_id="t-0042",
        task="Fix authentication token refresh in Asterim.",
        acceptance=("pnpm test auth passes",),
        constraints=("Do not modify migrations",),
    )


def inputs() -> dict:
    return {
        "excerpts": (
            ContextExcerpt(
                source="src/auth/token.ts",
                text=f"const key = '{PLANTED_KEY}';\nrefresh() {{ ... }}",
                reason="top retrieval hit",
                priority=5,
            ),
        ),
        "files": (FileEntry(path="src/auth/token.ts", reason="named by the failing test"),),
        "attempts": (
            Attempt(date="2026-08-19", agent="claude", summary="null check; still failed"),
        ),
        "state": GitState(
            branch="fix/auth",
            status=f"M src/auth/token.ts\n# {PLANTED_ASSIGNMENT}",
            recent_commits=("abc123 fix: retry refresh",),
            failing_tests="auth.spec.ts: 2 failed",
        ),
    }


def test_all_six_files_land_on_disk(tmp_path: Path) -> None:
    written = write_packet(packet(), tmp_path, **inputs())
    assert written.directory == tmp_path / "t-0042"
    assert sorted(written.files) == sorted(FILENAMES)
    for name in FILENAMES:
        assert (written.directory / name).is_file()


def test_planted_secrets_are_redacted_in_every_rendered_file(tmp_path: Path) -> None:
    written = write_packet(packet(), tmp_path, **inputs())
    for name in written.files:
        body = (written.directory / name).read_text(encoding="utf-8")
        assert PLANTED_KEY not in body, f"raw secret leaked into {name}"
        assert "hunter2secret99" not in body, f"assigned secret leaked into {name}"
    context = (written.directory / "CONTEXT.md").read_text(encoding="utf-8")
    assert "[REDACTED:anthropic_key]" in context
    state = (written.directory / "STATE.md").read_text(encoding="utf-8")
    assert "[REDACTED:assigned_secret]" in state
    assert any("anthropic_key" in label for label in written.redactions)


def test_budget_is_a_ceiling_and_the_cut_is_recorded(tmp_path: Path) -> None:
    many = tuple(
        ContextExcerpt(source=f"doc/{i}.md", text="word " * 400, priority=i) for i in range(10)
    )
    written = write_packet(packet(), tmp_path, excerpts=many, budget_tokens=2000)
    assert written.tokens <= 2000
    assert written.dropped_excerpts > 0
    context = (written.directory / "CONTEXT.md").read_text(encoding="utf-8")
    assert "dropped to fit the token budget" in context
    # Highest priority survives; the eviction order is the contract, not an accident.
    assert "doc/9.md" in context
    assert "doc/0.md" not in context


def test_over_budget_with_zero_excerpts_refuses_rather_than_truncates(tmp_path: Path) -> None:
    huge = packet().model_copy(update={"task": "word " * 5000})
    with pytest.raises(PacketOverBudget):
        write_packet(huge, tmp_path, budget_tokens=1000)


def test_rerender_is_idempotent(tmp_path: Path) -> None:
    first = write_packet(packet(), tmp_path, **inputs())
    second = write_packet(packet(), tmp_path, **inputs())
    assert first.directory == second.directory
    assert first.tokens == second.tokens
    assert (second.directory / "TASK.md").read_text(encoding="utf-8").startswith("# TASK")


def test_gather_git_state_reads_the_repo_and_never_raises(tmp_path: Path) -> None:
    from oracle.handoff.gather import gather_git_state
    from oracle.integrations.workspace import _git

    assert gather_git_state(tmp_path) == GitState(), "a non-repo must yield an empty state"

    _git(tmp_path, "init", "-b", "work")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "seed")
    (tmp_path / "a.py").write_text("A = 2\n", encoding="utf-8")

    state = gather_git_state(tmp_path, failing_tests="1 failed")
    assert state.branch == "work"
    assert "a.py" in state.status
    assert len(state.recent_commits) == 1 and "seed" in state.recent_commits[0]
    assert state.failing_tests == "1 failed"
