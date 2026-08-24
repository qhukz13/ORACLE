"""AntigravityAdapter against the recorded contract — no network, no vendor, no quota.

Every fixture under `tests/fixtures/agents/antigravity/` is real `agy` output recorded on
this machine by `scripts/record_agy_stream.py`; the stub replays it byte for byte through
the *same* spawn path the real binary would take (argv vector, pipes, process group).

Three of these tests exist because the recording contradicted an expectation:

* the write in `smoke` was **soft-denied** — this CLI is read-only without
  `--dangerously-skip-permissions`, which ORACLE will not pass;
* a soft denial ends the run at `status: ERROR` with **exit code 1**, so neither signal
  alone is enough to judge a run;
* an interrupted run reports `ERROR` / "timeout waiting for response" — never the
  documented `CANCELED`.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

import pytest

from oracle.integrations.adapter import ExternalAgentAdapter
from oracle.integrations.antigravity import AntigravityAdapter
from oracle.integrations.types import AgentEventKind, HandoffPacket, Workspace

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "agents" / "antigravity"
#: A read task that succeeded, with `--json-schema`: the shape the planner role depends on.
SCHEMA_OK = FIXTURES / "schema-v1.1.19.jsonl"
#: The same trivial task with a write in it: soft-denied, twice, then ERROR.
DENIED = FIXTURES / "smoke-v1.1.19.jsonl"
#: Interrupted at 12.00s; `result` arrived at 12.11s. See the sibling .timing.txt.
CANCELLED = FIXTURES / "cancel-v1.1.19.jsonl"
STUB = ROOT / "tests" / "stubs" / "stub_agy.py"


def adapter(grace_s: float = 0.3) -> AntigravityAdapter:
    return AntigravityAdapter(argv=(sys.executable, str(STUB)), grace_s=grace_s)


def packet(**overrides: object) -> HandoffPacket:
    fields: dict = {
        "task_id": "t-0002",
        "task": "Count the words in hello.txt.",
        "acceptance": ("the count is reported",),
        "constraints": ("Do nothing else",),
        "allowed_tools": ("Read",),
        "result_schema": {"type": "object"},
    }
    fields.update(overrides)
    return HandoffPacket(**fields)


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
    assert isinstance(AntigravityAdapter(), ExternalAgentAdapter)


def test_command_is_the_pinned_invocation(tmp_path: Path) -> None:
    cmd = adapter().command(packet(), Workspace(path=tmp_path))
    # The prompt is the VALUE of -p, and -p is LAST. The opposite of Claude's stdin, and
    # the one detail Asterim's working integration was consulted for before any code.
    assert cmd[-2] == "-p"
    assert "Count the words" in cmd[-1] and "Do nothing else" in cmd[-1]
    # Never omitted: default text mode loses stdout when it is not a TTY (OQ-05).
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--dangerously-skip-permissions" not in cmd, "INTEGRATIONS.md §5 forbids it"
    assert cmd[cmd.index("--add-dir") + 1] == str(tmp_path)
    assert "--json-schema" in cmd and "--print-timeout" in cmd


def test_model_and_effort_ride_only_when_the_registry_sets_them(tmp_path: Path) -> None:
    bare = AntigravityAdapter().command(packet(), Workspace(path=tmp_path))
    assert "--model" not in bare and "--effort" not in bare
    tuned = AntigravityAdapter(model="gemini-3.1-pro-high", effort="high").command(
        packet(), Workspace(path=tmp_path)
    )
    assert tuned[tuned.index("--model") + 1] == "gemini-3.1-pro-high"
    assert tuned[tuned.index("--effort") + 1] == "high"


def test_a_packet_asking_for_oracles_tools_fails_closed(tmp_path: Path) -> None:
    """`agy` has no `--mcp-config`: its MCP servers are global config. Honouring the
    packet would mutate machine state for one delegation; ignoring it would run a
    delegate that believes it holds ORACLE's guarded tools and does not. So: neither."""
    with pytest.raises(ValueError, match="cannot be lent ORACLE's tool server"):
        adapter().command(packet(mcp_config=str(tmp_path / "mcp.json")), Workspace(path=tmp_path))


async def test_structured_result_is_collected_from_the_vendors_own_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI filters the model's answer down to the schema itself: the raw `response`
    carried two extra keys (`toolAction`, `toolSummary`) that `structured_output` does
    not. ORACLE reads the filtered field — never the prose."""
    events, result = await run_to_end(SCHEMA_OK, monkeypatch, tmp_path)
    assert [e.kind for e in events] == [
        AgentEventKind.STARTED,
        AgentEventKind.TOOL_USE,
        AgentEventKind.TOOL_USE,
        AgentEventKind.TEXT,
        AgentEventKind.FINISHED,
    ]
    assert [e.tool for e in events if e.kind is AgentEventKind.TOOL_USE] == [
        "find_by_name",
        "view_file",
    ]
    assert result.success and result.exit_code == 0
    assert result.structured == {"first_word": "the", "word_count": 9}
    assert "toolSummary" not in str(result.structured)
    assert result.session_id == "68b6131f-5b56-49de-9bf9-d56d6f92b268"
    assert result.num_turns == 1
    # Quota-metered, so there is no dollar figure to report and none is invented.
    assert result.cost_usd is None


async def test_each_tool_step_counts_once_despite_arriving_twice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every tool step appears as ACTIVE and then again as DONE/ERROR under the same
    `step_index`. Counting both would double every tool call in the inspector."""
    events, _ = await run_to_end(SCHEMA_OK, monkeypatch, tmp_path)
    assert sum(1 for e in events if e.kind is AgentEventKind.TOOL_USE) == 2


async def test_a_soft_denied_write_is_surfaced_and_is_never_a_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The recorded consequence of refusing `--dangerously-skip-permissions`: `view_file`
    runs, `write_to_file` is denied twice, and the run ends ERROR / exit 1. The denial is
    the single most useful line in a headless run that appeared to do nothing, so it must
    reach the caller rather than being swallowed as vendor noise."""
    events, result = await run_to_end(DENIED, monkeypatch, tmp_path, STUB_EXIT="1")
    denials = [e for e in events if e.kind is AgentEventKind.ERROR and e.tool == "write_to_file"]
    assert len(denials) == 2
    assert "permission" in denials[0].text.lower()
    assert not any(e.kind is AgentEventKind.FINISHED for e in events)
    assert not result.success and result.exit_code == 1
    assert result.structured is None
    assert "permission check failed" in result.result_text


async def test_success_needs_both_the_exit_code_and_the_vendors_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exit 0 with `status: ERROR` is reachable — the denial fixture with its exit code
    forced to 0. ORACLE's judgement is the conjunction, not either half."""
    _, result = await run_to_end(DENIED, monkeypatch, tmp_path, STUB_EXIT="0")
    assert result.exit_code == 0 and not result.success


async def test_an_interrupted_run_reports_error_not_the_documented_cancelled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Measured, not assumed: the terminal event of a real interrupted run says
    `status: ERROR` / "timeout waiting for response" — never `CANCELED`. A cancelled run
    is therefore indistinguishable from a vendor timeout in the stream alone."""
    events, result = await run_to_end(CANCELLED, monkeypatch, tmp_path, STUB_EXIT="1")
    assert [e.kind for e in events] == [AgentEventKind.STARTED, AgentEventKind.ERROR]
    assert "timeout waiting for response" in events[-1].text
    assert not result.success and result.structured is None


async def test_collect_without_consuming_events_drains_the_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unread stdout pipe would block the child's exit forever; collect() must not
    depend on somebody else having iterated events() first."""
    monkeypatch.setenv("STUB_FIXTURE", str(SCHEMA_OK))
    a = adapter()
    handle = await a.submit(packet(), Workspace(path=tmp_path))
    result = await asyncio.wait_for(a.collect(handle), timeout=30)
    assert result.success and result.structured == {"first_word": "the", "word_count": 9}


async def test_unparseable_lines_are_skipped_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The vocabulary grew between two minor CLI versions during this task's own
    recording session (1.1.17 → 1.1.19). It will grow again."""
    noisy = tmp_path / "noisy.jsonl"
    lines = SCHEMA_OK.read_text(encoding="utf-8").splitlines(keepends=True)
    noisy.write_text(
        "not json at all\n"
        + '{"event": "future_kind", "future_kind": {"x": 1}}\n'
        + "".join(lines),
        encoding="utf-8",
    )
    events, result = await run_to_end(noisy, monkeypatch, tmp_path)
    assert result.success
    assert events[0].kind is AgentEventKind.STARTED


async def test_preflight_ready_reports_the_version() -> None:
    pre = await adapter().preflight()
    assert pre.ok and pre.version == "1.1.19"


async def test_preflight_missing_binary_routes_to_fallback() -> None:
    pre = await AntigravityAdapter(argv=("oracle-no-such-binary-xyz",)).preflight()
    assert not pre.ok
    assert pre.remedy is not None and "Handoff Packet" in pre.remedy


async def test_preflight_tells_unauthenticated_apart_from_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third state, and the one that costs something to get wrong: the binary is
    there, so the fallback message must say 'sign in', not 'install'. The probe is
    `agy models` — a real round trip that spends no model tokens, because every `-p`
    call costs ~14k input tokens before the model reads a word (OQ-05)."""
    monkeypatch.setenv("STUB_MODELS_EXIT", "1")
    monkeypatch.setenv("STUB_MODELS_STDERR", "Error: not authenticated. Please sign in.\n")
    pre = await adapter().preflight()
    assert not pre.ok
    assert pre.reason is not None and "not authenticated" in pre.reason
    assert pre.remedy is not None and "Sign in" in pre.remedy


async def test_cancel_mid_stream_kills_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Truncated before `result`, then hanging: the shape of a wedged delegate. Cancel
    must end the process within the escalation ladder, and collect must report the truth
    — no result event, not a success."""
    monkeypatch.setenv("STUB_FIXTURE", str(SCHEMA_OK))
    monkeypatch.setenv("STUB_TRUNCATE_AT", "5")
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
