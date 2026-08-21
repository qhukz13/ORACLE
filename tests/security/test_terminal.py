"""`term.*` against a real ConPTY, through the real toolhost.

The claim that matters is not "a terminal works". It is: **the agent may watch a shell
all day, and may not type into one without being asked each time.** A shell has every
privilege the user has and no scope an allowlist can inspect, so `term.write` is the
one place where the tool-contract argument does not reach.

The rest of these tests exist because of what the OQ-09 spike found — writing before
the shell is ready is swallowed with no error, which is the sort of failure that looks
like "the agent ignored me" rather than like a bug.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.policy.model import Capability, Decision, Tier
from oracle.toolhost import ToolHost
from oracle.tools import Approval, ToolErrorKind, ToolExecutor, build_registry
from oracle.tools.terminal import strip_ansi

winpty = pytest.importorskip("winpty", reason="pywinpty is not installed")

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
programs:
  cmd:
    path: "C:/Windows/System32/cmd.exe"
tools:
  fs.read:    {{ tier: T0, scopes: [projects] }}
  term.open:  {{ tier: T1, scopes: [projects] }}
  term.read:  {{ tier: T0, scopes: [projects] }}
  term.write: {{ tier: T2, scopes: [projects] }}
  term.close: {{ tier: T1, scopes: [projects] }}
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "Projects"
    root.mkdir()
    return root


@pytest_asyncio.fixture
async def ex(tmp_path: Path, workspace: Path) -> AsyncIterator[ToolExecutor]:
    p = tmp_path / "policy.yaml"
    p.write_text(POLICY.format(root=workspace.as_posix()), encoding="utf-8")
    host = ToolHost()
    executor = ToolExecutor(
        build_registry(),
        PolicyEngine(load_policy(p)),
        AuditLog(tmp_path / "audit.jsonl"),
        host=host,
    )
    try:
        yield executor
    finally:
        await host.stop()


async def _approved_write(ex: ToolExecutor, session_id: str, text: str, tag: str) -> object:
    """Write one line the way the UI would: preview, approve, execute."""
    args = {"session_id": session_id, "text": text}
    _, digest = ex.preview("term.write", args)
    ex.grant(
        Approval(
            approval_id=tag,
            tool="term.write",
            args_digest=digest,
            tier=Tier.T2,
            expires_at=time.time() + 60,
        )
    )
    return await ex.execute("term.write", args, approval_id=tag)


async def _read_until(
    ex: ToolExecutor, session_id: str, needle: str, give_up_after: float = 8.0
) -> str:
    """Poll the session until the marker appears, or give up.

    A fixed sleep would make this test flaky in exactly the way the spike showed the
    PTY itself is timing-sensitive — so the test waits for a condition, like the code
    it is testing does.
    """
    import asyncio

    seen: list[str] = []
    deadline = time.time() + give_up_after
    while time.time() < deadline:
        out = await ex.execute("term.read", {"session_id": session_id})
        if out.ok and out.result is not None:
            seen.append(out.result.text)  # type: ignore[attr-defined]
            if needle in "".join(seen):
                break
        await asyncio.sleep(0.15)
    return "".join(seen)


class TestWritingAlwaysAsks:
    async def test_write_is_refused_without_an_approval(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        opened = await ex.execute("term.open", {"path": str(workspace)})
        assert opened.ok, opened.error and opened.error.message
        session_id = opened.result.session_id  # type: ignore[union-attr]

        out = await ex.execute("term.write", {"session_id": session_id, "text": "echo hi"})
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.APPROVAL_REQUIRED
        assert out.verdict.tier is Tier.T2

        await ex.execute("term.close", {"session_id": session_id})

    async def test_an_approval_does_not_carry_to_the_next_command(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """The whole point of confirming *every* write: approving `echo hi` must not
        approve `del important.txt` a moment later."""
        opened = await ex.execute("term.open", {"path": str(workspace)})
        session_id = opened.result.session_id  # type: ignore[union-attr]

        first = await _approved_write(ex, session_id, "echo first-command", "ap_a")
        assert first.ok  # type: ignore[attr-defined]

        reused = await ex.execute(
            "term.write",
            {"session_id": session_id, "text": "echo second-command"},
            approval_id="ap_a",
        )
        assert not reused.ok
        assert reused.error is not None
        assert reused.error.kind == ToolErrorKind.APPROVAL_INVALID

        await ex.execute("term.close", {"session_id": session_id})

    async def test_write_is_not_proc_spawn(self) -> None:
        """docs/SECURITY.md#4b: the PTY is a separate capability on purpose. A spawn is
        an argv the allowlist can inspect; this is not."""
        contract = build_registry().get("term.write")
        assert Capability.TERM_WRITE in contract.capabilities
        assert Capability.PROC_SPAWN not in contract.capabilities
        assert contract.risk is Tier.T2

    async def test_multiple_lines_in_one_write_are_refused(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """One approval must not cover a script. The confirmation card would show the
        first line of something much longer."""
        opened = await ex.execute("term.open", {"path": str(workspace)})
        session_id = opened.result.session_id  # type: ignore[union-attr]

        out = await _approved_write(ex, session_id, "echo one\necho two", "ap_multi")
        assert not out.ok  # type: ignore[attr-defined]
        assert "more than one line" in out.error.message  # type: ignore[attr-defined]

        await ex.execute("term.close", {"session_id": session_id})


class TestTheShellActuallyWorks:
    async def test_a_written_command_runs_and_its_output_comes_back(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """The spike's trap, guarded: input sent before the shell is ready is swallowed
        silently, so `term.open` waits for a measured readiness condition."""
        opened = await ex.execute("term.open", {"path": str(workspace)})
        assert opened.ok, opened.error and opened.error.message
        session_id = opened.result.session_id  # type: ignore[union-attr]

        wrote = await _approved_write(ex, session_id, "echo oracle-marker-42", "ap_run")
        assert wrote.ok, wrote.error and wrote.error.message  # type: ignore[attr-defined]

        text = await _read_until(ex, session_id, "oracle-marker-42")
        assert "oracle-marker-42" in text

        await ex.execute("term.close", {"session_id": session_id})

    async def test_cyrillic_survives_without_a_code_page_dance(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """OQ-09's actual worry. ConPTY normalises to UTF-8, so no `chcp` is needed —
        this test is what stops that finding from silently regressing."""
        opened = await ex.execute("term.open", {"path": str(workspace)})
        session_id = opened.result.session_id  # type: ignore[union-attr]

        await _approved_write(ex, session_id, "echo Привет-мир-проверка", "ap_ru")
        text = await _read_until(ex, session_id, "Привет-мир-проверка")
        assert "Привет-мир-проверка" in text

        await ex.execute("term.close", {"session_id": session_id})

    async def test_reads_are_free_and_consume_the_buffer(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        opened = await ex.execute("term.open", {"path": str(workspace)})
        session_id = opened.result.session_id  # type: ignore[union-attr]

        first = await ex.execute("term.read", {"session_id": session_id})
        assert first.ok
        # T0: watching a shell needs no permission, only typing into one does.
        assert first.verdict.decision is Decision.ALLOW
        assert first.verdict.tier is Tier.T0

        again = await ex.execute("term.read", {"session_id": session_id})
        assert again.result.text == ""  # type: ignore[union-attr]

        await ex.execute("term.close", {"session_id": session_id})

    async def test_sessions_are_isolated(self, ex: ToolExecutor, workspace: Path) -> None:
        a = await ex.execute("term.open", {"path": str(workspace)})
        b = await ex.execute("term.open", {"path": str(workspace)})
        a_id = a.result.session_id  # type: ignore[union-attr]
        b_id = b.result.session_id  # type: ignore[union-attr]
        assert a_id != b_id

        await _approved_write(ex, a_id, "echo only-in-session-a", "ap_iso")
        seen_a = await _read_until(ex, a_id, "only-in-session-a")
        assert "only-in-session-a" in seen_a

        other = await ex.execute("term.read", {"session_id": b_id})
        assert "only-in-session-a" not in other.result.text  # type: ignore[union-attr]

        await ex.execute("term.close", {"session_id": a_id})
        await ex.execute("term.close", {"session_id": b_id})


class TestRefusals:
    async def test_opening_outside_a_scope_is_denied(self, ex: ToolExecutor) -> None:
        out = await ex.execute("term.open", {"path": r"C:\Windows"})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED

    async def test_an_unknown_session_is_refused(self, ex: ToolExecutor) -> None:
        out = await ex.execute("term.read", {"session_id": "term_nope"})
        assert not out.ok
        assert out.error is not None
        assert "no terminal session" in out.error.message

    async def test_a_dry_run_types_nothing(self, ex: ToolExecutor, workspace: Path) -> None:
        opened = await ex.execute("term.open", {"path": str(workspace)})
        session_id = opened.result.session_id  # type: ignore[union-attr]

        args = {"session_id": session_id, "text": "echo never-typed-marker"}
        _, digest = ex.preview("term.write", args)
        ex.grant(
            Approval(
                approval_id="ap_dry",
                tool="term.write",
                args_digest=digest,
                tier=Tier.T2,
                expires_at=time.time() + 60,
            )
        )
        out = await ex.execute("term.write", args, approval_id="ap_dry", dry_run=True)
        assert out.ok
        assert out.result.submitted is False  # type: ignore[union-attr]

        text = await _read_until(ex, session_id, "never-typed-marker", give_up_after=2.0)
        assert "never-typed-marker" not in text

        await ex.execute("term.close", {"session_id": session_id})


class TestNothingIsLostOnTheWayOut:
    """The bug this class exists for: `term.read` used to empty the whole buffer and
    then return only its LAST 16 KB. A long build lost its oldest output silently, and
    the scrollback counter reported zero drops — because nothing had been dropped on the
    way *in*. It was destroyed on the way out."""

    def test_a_bounded_read_leaves_the_remainder(self) -> None:
        from oracle.tools.terminal import Session

        session = Session(id="t", cwd=Path("."), shell_path="cmd.exe", pty=None)
        session.append("A" * 100)
        session.append("B" * 100)

        head, more = session.take(120)
        assert more is True
        assert head == "A" * 100 + "B" * 20

        rest, more = session.take(120)
        assert more is False
        assert rest == "B" * 80
        # And nothing was counted as lost, because nothing was.
        assert session.dropped == 0

    def test_an_unbounded_read_takes_everything(self) -> None:
        from oracle.tools.terminal import Session

        session = Session(id="t", cwd=Path("."), shell_path="cmd.exe", pty=None)
        session.append("hello")
        assert session.take() == ("hello", False)
        assert session.take() == ("", False)

    def test_the_ring_reports_what_it_trims(self) -> None:
        """A bounded scrollback is correct. Trimming *silently* is not — a reader would
        conclude that something near the start never appeared."""
        from oracle.tools import terminal

        session = terminal.Session(id="t", cwd=Path("."), shell_path="cmd.exe", pty=None)
        chunk = "x" * 10_000
        for _ in range((terminal.MAX_BUFFER_CHARS // len(chunk)) + 3):
            session.append(chunk)
        assert session.dropped > 0
        assert session.buffered <= terminal.MAX_BUFFER_CHARS

    async def test_a_long_burst_arrives_complete(self, ex: ToolExecutor, workspace: Path) -> None:
        """The Phase 3 acceptance criterion, scaled down to keep the suite quick: a
        flood is delivered in full across successive bounded reads."""
        import re

        opened = await ex.execute("term.open", {"path": str(workspace)})
        session_id = opened.result.session_id  # type: ignore[union-attr]

        lines = 300
        await _approved_write(
            ex, session_id, f"for /L %i in (1,1,{lines}) do @echo N-%i-Z", "ap_burst"
        )
        blob = await _read_until(ex, session_id, f"N-{lines}-Z", give_up_after=20.0)

        found = {int(n) for n in re.findall(r"N-(\d+)-Z", blob)}
        missing = sorted(set(range(1, lines + 1)) - found)
        assert not missing, f"{len(missing)} lines lost, first {missing[:5]}"

        await ex.execute("term.close", {"session_id": session_id})


class TestAnsiStripping:
    def test_escape_sequences_never_reach_the_model(self) -> None:
        """cmd.exe emits cursor moves and an OSC title on every prompt. Feeding those
        to a small model is noise it will try to interpret."""
        raw = (
            "\x1b[?7l\x1b[?7hMicrosoft Windows\r\n"
            "\x1b]0;C:\\Windows\\System32\\cmd.exe\x1b\\"
            "C:\\Projects>\x1b[4;20Hecho hi\x1b[4;30H\r\nhi\r\n"
        )
        clean = strip_ansi(raw)
        assert "\x1b" not in clean
        assert "Microsoft Windows" in clean and "echo hi" in clean and "hi" in clean

    def test_bel_terminated_osc_is_also_stripped(self) -> None:
        assert strip_ansi("\x1b]0;title\x07done") == "done"
