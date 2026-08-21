"""Write tools, the undo journal, and the trash.

ADR-0005's bargain is that a write runs *without prompting* because it can be reversed.
These tests are what makes that bargain honest: if undo does not work, T1 is not a tier,
it is a hope.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oracle.config import Settings, set_settings
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.policy.model import Decision, Provenance
from oracle.tools import ToolErrorKind, ToolExecutor, build_registry
from oracle.tools.undo import UndoError, UndoJournal, UndoKind

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
  vendor:
    roots:
      - {{ path: "{vendor}", mode: ro }}
  deny_always:
    - "**/*.env"
    - "**/.git/hooks/**"
tools:
  fs.read:   {{ tier: T0, scopes: [projects, vendor] }}
  fs.write:  {{ tier: T1, scopes: [projects] }}
  fs.patch:  {{ tier: T1, scopes: [projects] }}
  fs.move:   {{ tier: T1, scopes: [projects] }}
  fs.delete: {{ tier: T3, scopes: [projects] }}
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "Projects"
    vendor = root / "vendor"
    vendor.mkdir(parents=True)
    (root / "a.txt").write_text("original", encoding="utf-8")
    (root / "code.py").write_text("x = 1\ny = 2\nz = 1\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=1", encoding="utf-8")
    (vendor / "v.txt").write_text("vendored", encoding="utf-8")

    # Tools read the trash location from settings, so point it at the tmp workspace.
    set_settings(
        Settings(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            port=0,
            llm_enabled=False,
            prewarm_toolhost=False,
        )
    )
    return root


@pytest.fixture
def ex(tmp_path: Path, workspace: Path) -> ToolExecutor:
    p = tmp_path / "policy.yaml"
    p.write_text(
        POLICY.format(root=workspace.as_posix(), vendor=(workspace / "vendor").as_posix()),
        encoding="utf-8",
    )
    return ToolExecutor(
        build_registry(),
        PolicyEngine(load_policy(p)),
        AuditLog(tmp_path / "audit.jsonl"),
        undo=UndoJournal(tmp_path / "undo.jsonl"),
    )


class TestWriteIsReversible:
    async def test_overwrite_then_undo_restores_the_original(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """The whole justification for T1 running without a prompt."""
        target = workspace / "a.txt"
        out = await ex.execute("fs.write", {"path": str(target), "content": "replaced"})
        assert out.ok
        assert target.read_text(encoding="utf-8") == "replaced"
        assert out.undo_id, "a reversible write was not journalled"

        await ex._undo.undo(out.undo_id)  # type: ignore[union-attr]
        assert target.read_text(encoding="utf-8") == "original"

    async def test_creating_a_file_then_undo_removes_it(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        target = workspace / "new.txt"
        out = await ex.execute("fs.write", {"path": str(target), "content": "hi"})
        assert out.ok and target.exists()
        assert out.result.created is True  # type: ignore[union-attr]

        await ex._undo.undo(out.undo_id)  # type: ignore[union-attr,arg-type]
        assert not target.exists()

    async def test_patch_then_undo(self, ex: ToolExecutor, workspace: Path) -> None:
        target = workspace / "code.py"
        out = await ex.execute(
            "fs.patch", {"path": str(target), "find": "x = 1", "replace": "x = 99"}
        )
        assert out.ok
        assert "x = 99" in target.read_text(encoding="utf-8")
        assert "@@" in out.result.diff  # type: ignore[union-attr]

        await ex._undo.undo(out.undo_id)  # type: ignore[union-attr,arg-type]
        assert target.read_text(encoding="utf-8") == "x = 1\ny = 2\nz = 1\n"

    async def test_move_then_undo(self, ex: ToolExecutor, workspace: Path) -> None:
        src, dst = workspace / "a.txt", workspace / "renamed.txt"
        out = await ex.execute("fs.move", {"path": str(src), "destination": str(dst)})
        assert out.ok and dst.exists() and not src.exists()

        await ex._undo.undo(out.undo_id)  # type: ignore[union-attr,arg-type]
        assert src.exists() and not dst.exists()

    async def test_undo_is_single_use(self, ex: ToolExecutor, workspace: Path) -> None:
        out = await ex.execute("fs.write", {"path": str(workspace / "a.txt"), "content": "x"})
        await ex._undo.undo(out.undo_id)  # type: ignore[union-attr,arg-type]
        with pytest.raises(UndoError, match="already been undone"):
            await ex._undo.undo(out.undo_id)  # type: ignore[union-attr,arg-type]

    async def test_undo_refuses_to_clobber_a_restored_move_target(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """Undo must never destroy work. If the origin exists again, refuse."""
        src, dst = workspace / "a.txt", workspace / "moved.txt"
        out = await ex.execute("fs.move", {"path": str(src), "destination": str(dst)})
        src.write_text("someone recreated this", encoding="utf-8")

        with pytest.raises(UndoError, match="refusing to overwrite"):
            await ex._undo.undo(out.undo_id)  # type: ignore[union-attr,arg-type]
        assert src.read_text(encoding="utf-8") == "someone recreated this"


def _approve(ex: ToolExecutor, tool: str, args: dict) -> str:
    """Issue an approval bound to exactly these arguments, the way the Confirmation
    Center will."""
    import time

    from oracle.tools.executor import Approval

    verdict, digest = ex.preview(tool, args)
    ex.grant(
        Approval(
            approval_id="ap_test",
            tool=tool,
            args_digest=digest,
            tier=verdict.tier,
            expires_at=time.time() + 300,
        )
    )
    return "ap_test"


class TestDeleteGoesToTrash:
    async def test_delete_needs_strong_approval(self, ex: ToolExecutor, workspace: Path) -> None:
        """T3. Without an approval it must refuse, even though it only trashes."""
        out = await ex.execute("fs.delete", {"path": str(workspace / "a.txt")})
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.APPROVAL_REQUIRED
        assert (workspace / "a.txt").exists()

    async def test_delete_moves_rather_than_unlinks(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """An unrecoverable delete is T4 and absent from the catalogue."""
        target = workspace / "a.txt"
        args = {"path": str(target)}
        out = await ex.execute("fs.delete", args, approval_id=_approve(ex, "fs.delete", args))
        assert out.ok, out.error.message if out.error else ""
        assert not target.exists()

        trashed = Path(out.result.trashed_to)  # type: ignore[union-attr]
        assert trashed.exists(), "the file was destroyed, not trashed"
        assert trashed.read_text(encoding="utf-8") == "original"

        await ex._undo.undo(out.undo_id)  # type: ignore[union-attr,arg-type]
        assert target.read_text(encoding="utf-8") == "original"

    async def test_directory_delete_requires_explicit_recursive(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        d = workspace / "adir"
        d.mkdir()
        (d / "f.txt").write_text("x", encoding="utf-8")
        args = {"path": str(d)}
        out = await ex.execute("fs.delete", args, approval_id=_approve(ex, "fs.delete", args))
        assert not out.ok
        assert "recursive" in (out.error.message if out.error else "")
        assert d.exists()


class TestTheGateStillApplies:
    async def test_write_outside_scope_is_denied(self, ex: ToolExecutor) -> None:
        out = await ex.execute(
            "fs.write", {"path": r"C:\Windows\System32\drivers\etc\hosts", "content": "x"}
        )
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED

    async def test_write_to_a_denied_path_is_refused(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        out = await ex.execute("fs.write", {"path": str(workspace / ".env"), "content": "x"})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED

    async def test_write_into_a_read_only_scope_is_denied(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        out = await ex.execute(
            "fs.write", {"path": str(workspace / "vendor" / "v.txt"), "content": "x"}
        )
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED

    async def test_move_destination_is_scope_checked_too(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """Resolving only the source would let a move write anywhere on disk."""
        out = await ex.execute(
            "fs.move",
            {"path": str(workspace / "a.txt"), "destination": r"C:\Windows\Temp\stolen.txt"},
        )
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED
        assert (workspace / "a.txt").exists(), "the source was moved despite a denied destination"

    async def test_delete_is_t3_and_needs_strong_approval(self, ex: ToolExecutor) -> None:
        verdict = ex._engine.evaluate("fs.delete", declared_tier=None)
        assert verdict.decision is Decision.CONFIRM_STRONG

    async def test_tainted_write_escalates_to_a_confirm(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """A plan built from a node_modules README does not get to auto-write."""
        out = await ex.execute(
            "fs.write",
            {"path": str(workspace / "a.txt"), "content": "x"},
            provenances=frozenset({Provenance.LOCAL_FOREIGN}),
        )
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.APPROVAL_REQUIRED
        assert (workspace / "a.txt").read_text(encoding="utf-8") == "original"


class TestPatchIsStrict:
    async def test_missing_text_fails_loudly(self, ex: ToolExecutor, workspace: Path) -> None:
        out = await ex.execute(
            "fs.patch",
            {"path": str(workspace / "code.py"), "find": "not present", "replace": "x"},
        )
        assert not out.ok
        assert "not what was expected" in (out.error.message if out.error else "")

    async def test_ambiguous_match_is_an_error_not_a_guess(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """`= 1` appears twice. Picking one silently is how an agent corrupts a file."""
        out = await ex.execute(
            "fs.patch", {"path": str(workspace / "code.py"), "find": "= 1", "replace": "= 7"}
        )
        assert not out.ok
        assert "occurrences" in (out.error.message if out.error else "")
        assert workspace.joinpath("code.py").read_text(encoding="utf-8") == "x = 1\ny = 2\nz = 1\n"


class TestJournalIntegrity:
    async def test_every_mutation_is_journalled(self, ex: ToolExecutor, workspace: Path) -> None:
        await ex.execute("fs.write", {"path": str(workspace / "a.txt"), "content": "1"})
        await ex.execute("fs.write", {"path": str(workspace / "b.txt"), "content": "2"})
        records = ex._undo.records()  # type: ignore[union-attr]
        assert len(records) == 2
        assert {r.tool for r in records} == {"fs.write"}
        assert records[0].plan.kind is UndoKind.RESTORE_FILE  # a.txt existed
        assert records[1].plan.kind is UndoKind.DELETE_CREATED  # b.txt was new

    async def test_reads_are_not_journalled(self, ex: ToolExecutor, workspace: Path) -> None:
        await ex.execute("fs.read", {"path": str(workspace / "a.txt")})
        assert ex._undo.records() == []  # type: ignore[union-attr]

    async def test_denied_writes_are_not_journalled(self, ex: ToolExecutor) -> None:
        await ex.execute("fs.write", {"path": r"C:\Windows\x.txt", "content": "x"})
        assert ex._undo.records() == []  # type: ignore[union-attr]

    async def test_undoing_an_unknown_record_fails(self, tmp_path: Path) -> None:
        j = UndoJournal(tmp_path / "u.jsonl")
        with pytest.raises(UndoError, match="no undo record"):
            await j.undo("u_nope")
