"""A delegated agent must be able to reach less than its owner can, and nothing else.

The six refusals P6-T3 promises, each asserting the call **never reached the executor**
— a `SpyExecutor` counts executions, because "it returned an error" and "it did not
run" are different security properties and only the second one is the claim.

The threat model here is not a malicious human; it is a delegated agent that has read a
prompt-injected file in the corpus it was given, and is now trying to do what that file
said (SECURITY.md §6). Everything it can reach is therefore a decision, not a default.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from oracle.core.eventlog import EventLog
from oracle.mcp.calls import McpCallHandler
from oracle.mcp.tokens import DEFAULT_TOOLS, DEFAULT_TTL_S, TokenError, TokenStore
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.policy.model import Tier
from oracle.tools import ToolExecutor, build_registry
from oracle.tools.executor import ToolOutcome

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
tools:
  fs.read:      {{ tier: T0, scopes: [projects] }}
  fs.list:      {{ tier: T0, scopes: [projects] }}
  fs.delete:    {{ tier: T3, scopes: [projects] }}
  git.status:   {{ tier: T0, scopes: [projects] }}
  dev.run_tests: {{ tier: T1, scopes: [projects] }}
"""


class SpyExecutor(ToolExecutor):
    """Counts what actually executed. The assertion is 'never reached', not 'errored'."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.executed: list[str] = []

    async def execute(self, tool_id: str, raw_args: dict[str, Any], **kwargs: Any) -> ToolOutcome:
        self.executed.append(tool_id)
        return await super().execute(tool_id, raw_args, **kwargs)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "Projects" / "wt" / "t-1"
    ws.mkdir(parents=True)
    (ws / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "Projects" / "secrets.txt").write_text("not yours\n", encoding="utf-8")
    return ws


@pytest.fixture
def harness(
    tmp_path: Path, workspace: Path, eventlog: EventLog
) -> tuple[McpCallHandler, TokenStore, SpyExecutor]:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(POLICY.format(root=(tmp_path / "Projects").as_posix()), encoding="utf-8")
    executor = SpyExecutor(
        build_registry(), PolicyEngine(load_policy(policy_path)), AuditLog(tmp_path / "audit.jsonl")
    )
    tokens = TokenStore()
    return McpCallHandler(tokens, executor, eventlog), tokens, executor


async def test_a_forged_token_never_reaches_the_executor(
    harness: tuple[McpCallHandler, TokenStore, SpyExecutor], workspace: Path
) -> None:
    handler, tokens, executor = harness
    real = tokens.mint("t-1", workspace)
    body, _ = real.split(".", 1)
    forged = f"{body}.{'A' * 43}"  # valid shape, wrong signature

    result = await handler.call(forged, "fs.read", {"path": str(workspace / "app.py")})

    assert not result.ok and result.payload["error"] == "not permitted"
    assert executor.executed == []


async def test_an_expired_token_is_refused(
    harness: tuple[McpCallHandler, TokenStore, SpyExecutor], workspace: Path
) -> None:
    handler, tokens, executor = harness
    # Minted as if the delegation started long enough ago that its TTL has run out.
    token = tokens.mint("t-1", workspace, now=time.time() - DEFAULT_TTL_S - 10)

    result = await handler.call(token, "fs.read", {"path": str(workspace / "app.py")})

    assert not result.ok
    assert executor.executed == []


async def test_a_token_stops_working_when_its_delegation_ends(
    harness: tuple[McpCallHandler, TokenStore, SpyExecutor], workspace: Path
) -> None:
    """Use-after-end. The run is over, nobody is watching the event stream, and the
    worktree may already be discarded — the key must not still turn."""
    handler, tokens, executor = harness
    token = tokens.mint("t-1", workspace)
    ok = await handler.call(token, "fs.read", {"path": str(workspace / "app.py")})
    assert ok.ok and executor.executed == ["fs.read"]

    tokens.revoke("t-1")
    after = await handler.call(token, "fs.read", {"path": str(workspace / "app.py")})

    assert not after.ok and after.payload["error"] == "not permitted"
    assert executor.executed == ["fs.read"], "a revoked capability still executed"


async def test_a_path_outside_the_worktree_is_refused(
    harness: tuple[McpCallHandler, TokenStore, SpyExecutor], workspace: Path, tmp_path: Path
) -> None:
    """The gate would refuse an out-of-scope path anyway; this refuses an IN-scope path
    that simply is not this delegation's. `..` is resolved before comparing."""
    handler, tokens, executor = harness
    token = tokens.mint("t-1", workspace)

    escape = workspace / ".." / ".." / "secrets.txt"
    result = await handler.call(token, "fs.read", {"path": str(escape)})

    assert not result.ok
    assert "outside this delegation's workspace" in result.payload["error"]
    assert executor.executed == []


async def test_a_tool_outside_the_allowlist_is_refused(
    harness: tuple[McpCallHandler, TokenStore, SpyExecutor], workspace: Path
) -> None:
    handler, tokens, executor = harness
    token = tokens.mint("t-1", workspace, tools=("fs.read",))

    result = await handler.call(token, "git.status", {"path": str(workspace)})

    assert not result.ok and "was not lent" in result.payload["error"]
    assert executor.executed == []


async def test_a_t3_tool_is_refused_rather_than_prompting_the_owner(
    harness: tuple[McpCallHandler, TokenStore, SpyExecutor], workspace: Path
) -> None:
    """An unattended delegate must not be able to raise a confirmation dialog: that is
    prompt fatigue as a service. Note the tool IS in the allowlist here — the refusal
    comes from the gate's tier, not from the capability."""
    handler, tokens, executor = harness
    token = tokens.mint("t-1", workspace, tools=("fs.delete",))

    result = await handler.call(token, "fs.delete", {"path": str(workspace / "app.py")})

    assert not result.ok
    assert "T3" in result.payload["error"] and "a human decides it" in result.payload["error"]
    assert executor.executed == []
    assert (workspace / "app.py").exists()


async def test_the_default_allowlist_reads_and_verifies_but_never_writes() -> None:
    """The lent surface is a decision, and this is where it is reviewable."""
    from oracle.tools import build_registry as registry_of

    registry = registry_of()
    for tool_id in DEFAULT_TOOLS:
        if not registry.has(tool_id):
            continue
        contract = registry.get(tool_id)
        assert contract.risk <= Tier.T1, f"{tool_id} is lent to delegates at {contract.risk.label}"


async def test_verify_reports_why_without_telling_the_caller(
    harness: tuple[McpCallHandler, TokenStore, SpyExecutor], workspace: Path
) -> None:
    """The store says which check failed (for the log); the handler says 'not
    permitted' (for the delegate). Both halves matter and they are different."""
    _, tokens, _ = harness
    with pytest.raises(TokenError, match="malformed token"):
        tokens.verify("nonsense")
    with pytest.raises(TokenError, match="no such live delegation"):
        token = tokens.mint("t-9", workspace)
        tokens.revoke("t-9")
        tokens.verify(token)
