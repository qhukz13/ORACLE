"""ClaudeCodeAdapter against the recorded contract — no network, no vendor, no cost.

Every fixture under `tests/fixtures/claude_stream/` is real CLI output recorded on this
machine by `scripts/record_claude_stream.py`; the stub replays it byte for byte through
the *same* spawn path the real binary would take (argv vector, pipes, process group).
What these tests pin is therefore the adapter's half of the contract: normalisation,
semantic end, error surfacing, cancellation, and collection — against streams the
vendor actually produced, including the event kinds the docs never listed.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import pytest

from oracle.integrations.adapter import ExternalAgentAdapter
from oracle.integrations.claude import ClaudeCodeAdapter
from oracle.integrations.types import AgentEventKind, HandoffPacket, Workspace

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "tests" / "fixtures" / "claude_stream" / "smoke-v2.1.238.jsonl"
AUTH_FAILED = ROOT / "tests" / "fixtures" / "claude_stream" / "auth-failed-v2.1.238.jsonl"
STUB = ROOT / "tests" / "stubs" / "stub_claude.py"


def adapter(grace_s: float = 0.3) -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(argv=(sys.executable, str(STUB)), grace_s=grace_s)


def packet() -> HandoffPacket:
    return HandoffPacket(
        task_id="t-0001",
        task="Count the words in hello.txt.",
        acceptance=("count.txt contains the number",),
        constraints=("Do nothing else",),
        allowed_tools=("Read", "Write"),
        result_schema={"type": "object"},
    )


async def run_to_end(
    fixture: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str
) -> tuple[list, object]:
    monkeypatch.setenv("STUB_FIXTURE", str(fixture))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    a = adapter()
    handle = await a.submit(packet(), Workspace(path=tmp_path))
    events = [e async for e in a.events(handle)]
    return events, await a.collect(handle)


def test_protocol_conformance() -> None:
    assert isinstance(ClaudeCodeAdapter(), ExternalAgentAdapter)


def test_command_is_the_pinned_invocation(tmp_path: Path) -> None:
    cmd = adapter().command(packet(), Workspace(path=tmp_path))
    assert "--bare" not in cmd, "unusable with subscription auth — INTEGRATIONS.md §3"
    for flag in ("--setting-sources", "--strict-mcp-config", "--json-schema"):
        assert flag in cmd
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Write"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    prompt = cmd[cmd.index("-p") + 1]
    assert "Count the words" in prompt and "Do nothing else" in prompt


async def test_smoke_stream_normalises_to_oracle_vocabulary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    events, _ = await run_to_end(SMOKE, monkeypatch, tmp_path)
    # The exact sequence, because the fixture is pinned: hook noise, thinking-token
    # counters, rate-limit chatter and the trailing task_summary all vanish; empty
    # (signature-only) thinking blocks are not events.
    assert [e.kind for e in events] == [
        AgentEventKind.STARTED,
        AgentEventKind.TOOL_USE,
        AgentEventKind.TOOL_USE,
        AgentEventKind.TOOL_USE,
        AgentEventKind.FINISHED,
    ]
    assert [e.tool for e in events if e.kind is AgentEventKind.TOOL_USE] == [
        "Read",
        "Write",
        "StructuredOutput",
    ]
    assert not any(e.from_subagent for e in events)


async def test_smoke_result_is_collected_and_typed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, result = await run_to_end(SMOKE, monkeypatch, tmp_path)
    assert result.success and result.exit_code == 0
    assert result.structured == {"word_count": 9}
    assert result.cost_usd == pytest.approx(0.3213741)
    assert result.num_turns == 4
    assert result.session_id == "994441db-8ff6-4fac-86ed-be719c15463f"


async def test_collect_without_consuming_events_drains_the_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unread stdout pipe would block the child's exit forever; collect() must not
    depend on somebody else having iterated events() first."""
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    a = adapter()
    handle = await a.submit(packet(), Workspace(path=tmp_path))
    result = await asyncio.wait_for(a.collect(handle), timeout=30)
    assert result.success and result.structured == {"word_count": 9}


async def test_auth_failure_surfaces_as_error_not_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The recorded subscription-auth failure: exit 0, `is_error` true. Success is
    ORACLE's judgement of the stream, not the exit code alone."""
    events, result = await run_to_end(AUTH_FAILED, monkeypatch, tmp_path)
    errors = [e for e in events if e.kind is AgentEventKind.ERROR]
    assert errors and "Not logged in" in errors[0].text
    assert not any(e.kind is AgentEventKind.FINISHED for e in events)
    assert not result.success
    assert result.cost_usd == 0


async def test_preflight_ok_reports_version() -> None:
    pre = await adapter().preflight()
    assert pre.ok and pre.version == "2.1.238"


async def test_preflight_missing_binary_routes_to_fallback() -> None:
    pre = await ClaudeCodeAdapter(argv=("oracle-no-such-binary-xyz",)).preflight()
    assert not pre.ok
    assert pre.remedy is not None and "Handoff Packet" in pre.remedy


async def test_cancel_mid_stream_kills_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Truncated before `result`, then hanging: the shape of a wedged delegate. Cancel
    must end the process within the escalation ladder, and collect must report the
    truth — no result event, not a success."""
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    monkeypatch.setenv("STUB_TRUNCATE_AT", "7")
    monkeypatch.setenv("STUB_HANG", "1")
    a = adapter()
    handle = await a.submit(packet(), Workspace(path=tmp_path))

    seen: list[AgentEventKind] = []
    first = asyncio.Event()

    async def consume() -> None:
        async for event in a.events(handle):
            seen.append(event.kind)
            first.set()

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(first.wait(), timeout=10)
    await asyncio.wait_for(a.cancel(handle), timeout=15)
    assert handle.proc.returncode is not None, "cancel left the child running"

    with contextlib.suppress(Exception):
        await asyncio.wait_for(consumer, timeout=10)
    result = await asyncio.wait_for(a.collect(handle), timeout=15)
    assert not result.success
    assert result.structured is None


MCP_FAILED = ROOT / "tests" / "fixtures" / "claude_stream" / "mcp-failed-v2.1.238.jsonl"


async def test_a_run_whose_tool_server_failed_to_load_is_never_a_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """INTEGRATIONS.md §4: a silently unloaded MCP server leaves the delegate working
    outside the policy gate — the hole the server exists to close. The vendor stream
    still reports `is_error: false` and exit 0, and ORACLE still calls it a failure.

    The fixture is the recorded smoke stream with `mcp_server_errors` set on `init`,
    so everything else about it is real."""
    events, result = await run_to_end(MCP_FAILED, monkeypatch, tmp_path)

    kinds = [e.kind for e in events]
    assert kinds[0] is AgentEventKind.ERROR, "the run continued past a missing tool server"
    assert "outside the policy gate" in events[0].text
    assert not result.success
    assert "tool server failed to load" in result.result_text
