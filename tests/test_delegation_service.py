"""The delegation lifecycle under the daemon: approve → run → verify → report.

Everything is real except the vendor (stub CLI replaying recorded output — see
`helpers_delegation`). What is being checked is the *shape of a delegation*: the state
sequence on the event log, the feed a client can replay from `since_seq`, verification
read from the worktree rather than from prose, and HALT reaching the child process —
the P6-T2 acceptance criteria, not implementation details.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from oracle.core.eventlog import EventLog
from oracle.delegation.service import DelegationState, Outcome
from oracle.integrations.claude import ClaudeCodeAdapter
from tests.helpers_delegation import (
    SMOKE,
    SpyAdapter,
    events_of,
    make_repo,
    make_service,
    packet,
    stub_adapter,
    wait_for,
    wait_state,
)


async def test_happy_path_reaches_finished_with_verified_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, eventlog: EventLog
) -> None:
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    adapter = stub_adapter()
    service, approvals, _ = make_service(tmp_path, eventlog, adapter)

    run = asyncio.ensure_future(service.run(packet(), repo))
    requested = await wait_for(eventlog, "approval.requested")
    assert requested.payload["tool"] == "ai.delegate"
    await approvals.resolve(str(requested.payload["approval_id"]), True)
    active = await asyncio.wait_for(run, 30)

    assert active.outcome == Outcome.SUCCESS
    assert adapter.submits == 1

    states = [e.payload["state"] for e in await events_of(eventlog, "task.updated")]
    assert states == [
        DelegationState.RENDERING,
        DelegationState.AWAITING_EGRESS,
        DelegationState.RUNNING,
        DelegationState.VERIFYING,
    ]
    finished = (await events_of(eventlog, "task.finished"))[-1]
    assert finished.payload["outcome"] == Outcome.SUCCESS
    assert finished.payload["structured"] == {"word_count": 9}
    assert finished.payload["cost_usd"] == pytest.approx(0.3213741)
    assert finished.payload["tests"] == {"ran": False, "reason": "no verifier wired"}
    assert Path(finished.payload["workspace"]).exists()
    # The scrub ran before the child did.
    assert not (Path(finished.payload["workspace"]) / ".claude").exists()


async def test_the_feed_replays_from_since_seq(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, eventlog: EventLog
) -> None:
    """A client connecting after the fact reconstructs the whole run from the log —
    the same `read_range` the WS resume path uses."""
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    service, approvals, _ = make_service(tmp_path, eventlog, stub_adapter())

    run = asyncio.ensure_future(service.run(packet(), repo))
    requested = await wait_for(eventlog, "approval.requested")
    await approvals.resolve(str(requested.payload["approval_id"]), True)
    await asyncio.wait_for(run, 30)

    feed = await events_of(eventlog, "delegate.event")
    kinds = [e.payload["kind"] for e in feed]
    assert kinds[0] == "started"
    assert kinds[-1] == "finished"
    assert kinds.count("tool_use") == 3
    assert all(e.task_id == "t-del-1" for e in feed)


async def test_preflight_failure_falls_back_without_asking(
    tmp_path: Path, eventlog: EventLog
) -> None:
    """No vendor → the packet stays on disk and *no approval is requested*: there is
    nothing to approve, because nothing can egress."""
    adapter = SpyAdapter(ClaudeCodeAdapter(argv=("oracle-no-such-binary-xyz",)))
    repo = make_repo(tmp_path)
    service, _, _ = make_service(tmp_path, eventlog, adapter)

    active = await asyncio.wait_for(service.run(packet(), repo), 30)

    assert active.outcome == Outcome.FALLBACK
    assert adapter.submits == 0
    assert await events_of(eventlog, "approval.requested") == []
    finished = (await events_of(eventlog, "task.finished"))[-1]
    assert "Handoff Packet" in finished.payload["explanation"]
    assert (Path(finished.payload["packet_dir"]) / "TASK.md").is_file()


async def test_halt_mid_run_kills_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, eventlog: EventLog
) -> None:
    """Cancelling the service task (what HALT does to every tracked task) must reach
    the child process — measured by its exit, not assumed from the cancel."""
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    monkeypatch.setenv("STUB_TRUNCATE_AT", "7")
    monkeypatch.setenv("STUB_HANG", "1")
    repo = make_repo(tmp_path)
    service, approvals, _ = make_service(tmp_path, eventlog, stub_adapter())

    run = asyncio.ensure_future(service.run(packet(), repo))
    requested = await wait_for(eventlog, "approval.requested")
    await approvals.resolve(str(requested.payload["approval_id"]), True)
    await wait_state(eventlog, DelegationState.RUNNING)

    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run, 30)

    active = service.get("t-del-1")
    assert active is not None and active.outcome == Outcome.HALTED
    assert active.handle is not None
    assert active.handle.proc.returncode is not None, "the child outlived HALT"
    finished = await events_of(eventlog, "task.finished")
    assert finished and finished[-1].payload["outcome"] == Outcome.HALTED


async def test_discard_removes_the_worktree_and_keeps_the_packet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, eventlog: EventLog
) -> None:
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    service, approvals, _ = make_service(tmp_path, eventlog, stub_adapter())

    run = asyncio.ensure_future(service.run(packet(), repo))
    requested = await wait_for(eventlog, "approval.requested")
    await approvals.resolve(str(requested.payload["approval_id"]), True)
    active = await asyncio.wait_for(run, 30)

    workspace = Path(active.result["workspace"])
    assert workspace.exists()
    assert await service.discard("t-del-1") is True
    assert not workspace.exists()
    assert active.written is not None and active.written.directory.exists(), (
        "the packet is the record of what was sent; discard must not eat it"
    )
    assert await service.discard("t-del-1") is False


async def test_verifier_result_lands_in_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, eventlog: EventLog
) -> None:
    """When a test-runner is wired, its verdict — ORACLE's evidence — rides
    `task.finished` beside the agent's own claim."""
    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)

    seen: list[Path] = []

    async def verifier(path: Path) -> dict[str, object]:
        seen.append(path)
        return {"ran": True, "passed": 3, "failed": 0}

    service, approvals, _ = make_service(tmp_path, eventlog, stub_adapter(), run_tests=verifier)

    run = asyncio.ensure_future(service.run(packet(), repo))
    requested = await wait_for(eventlog, "approval.requested")
    await approvals.resolve(str(requested.payload["approval_id"]), True)
    active = await asyncio.wait_for(run, 30)

    assert active.result["tests"] == {"ran": True, "passed": 3, "failed": 0}
    assert seen and seen[0] == Path(active.result["workspace"])


async def test_the_delegate_is_lent_oracles_tools_and_loses_them_at_the_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, eventlog: EventLog
) -> None:
    """The MCP lending path (INTEGRATIONS.md §4): a config with a live token exists
    while the delegate runs, and both the file and the capability are gone when it
    ends. A token on disk after the run is a key left in the door."""
    import json

    from oracle.mcp.tokens import TokenError, TokenStore

    monkeypatch.setenv("STUB_FIXTURE", str(SMOKE))
    repo = make_repo(tmp_path)
    tokens = TokenStore()
    adapter = stub_adapter()
    service, approvals, _ = make_service(
        tmp_path, eventlog, adapter, tokens=tokens, mcp_url="http://127.0.0.1:7777"
    )

    seen: dict[str, str] = {}
    original = adapter.submit

    async def capture(packet, ws):  # type: ignore[no-untyped-def]
        # Read the config at the moment of submission: afterwards it is gone by design.
        assert packet.mcp_config is not None
        config = json.loads(Path(packet.mcp_config).read_text(encoding="utf-8"))
        seen.update(config["mcpServers"]["oracle"]["env"])
        return await original(packet, ws)

    adapter.submit = capture  # type: ignore[method-assign]

    run = asyncio.ensure_future(service.run(packet(), repo))
    requested = await wait_for(eventlog, "approval.requested")
    await approvals.resolve(str(requested.payload["approval_id"]), True)
    active = await asyncio.wait_for(run, 30)

    assert active.outcome == Outcome.SUCCESS
    assert seen["ORACLE_MCP_URL"] == "http://127.0.0.1:7777"
    # The token was real while the run was live, and is refused now that it is not.
    with pytest.raises(TokenError):
        tokens.verify(seen["ORACLE_MCP_TOKEN"])
    assert active.mcp_config is None
