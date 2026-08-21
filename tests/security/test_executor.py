"""The gate, end to end.

These are the tests that matter most: they assert the *ordering* of the security model
— nothing executes before the gate allows it, an approval binds to exact arguments, and
a path swapped after approval is caught.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from oracle.policy.audit import AuditLog, digest_args
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.policy.model import Provenance, Tier
from oracle.tools import ToolErrorKind, ToolExecutor, build_registry
from oracle.tools.contract import ToolRegistry, ToolRegistryError, ToolResult, tool
from oracle.tools.executor import Approval
from oracle.tools.readonly import FsReadArgs

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
  deny_always:
    - "**/*.env"
tools:
  fs.read:  {{ tier: T0, scopes: [projects] }}
  fs.list:  {{ tier: T0, scopes: [projects] }}
  fs.stat:  {{ tier: T0, scopes: [projects] }}
  sys.info: {{ tier: T0 }}
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "Projects"
    (r / "sub").mkdir(parents=True)
    (r / "a.txt").write_text("hello", encoding="utf-8")
    (r / ".env").write_text("SECRET=1", encoding="utf-8")
    (r / "outside_target").mkdir()
    return r


@pytest.fixture
def executor(tmp_path: Path, root: Path) -> ToolExecutor:
    p = tmp_path / "policy.yaml"
    p.write_text(POLICY.format(root=root.as_posix()), encoding="utf-8")
    engine = PolicyEngine(load_policy(p))
    audit = AuditLog(tmp_path / "audit.jsonl")
    return ToolExecutor(build_registry(), engine, audit)


class TestHappyPath:
    async def test_reads_a_file_in_scope(self, executor: ToolExecutor, root: Path) -> None:
        out = await executor.execute("fs.read", {"path": str(root / "a.txt")})
        assert out.ok
        assert out.result is not None
        assert out.result.text == "hello"  # type: ignore[attr-defined]

    async def test_lists_a_directory(self, executor: ToolExecutor, root: Path) -> None:
        out = await executor.execute("fs.list", {"path": str(root)})
        assert out.ok
        names = {e.name for e in out.result.entries}  # type: ignore[attr-defined]
        assert "a.txt" in names


class TestGateCannotBeWalkedAround:
    async def test_unknown_tool_is_refused(self, executor: ToolExecutor) -> None:
        out = await executor.execute("sys.format_disk", {})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.NOT_FOUND

    async def test_path_outside_scope_never_reaches_the_handler(
        self, executor: ToolExecutor
    ) -> None:
        out = await executor.execute("fs.read", {"path": r"C:\Windows\win.ini"})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED

    async def test_deny_rule_blocks_a_file_inside_scope(
        self, executor: ToolExecutor, root: Path
    ) -> None:
        out = await executor.execute("fs.read", {"path": str(root / ".env")})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED

    async def test_junction_escape_is_denied_end_to_end(
        self, executor: ToolExecutor, root: Path, tmp_path: Path
    ) -> None:
        """The full stack: a real junction, through the executor, refused."""
        secret_dir = tmp_path / "elsewhere"
        secret_dir.mkdir()
        (secret_dir / "secret.txt").write_text("SECRET")
        link = root / "escape"
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(secret_dir)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            pytest.skip("could not create a junction")

        out = await executor.execute("fs.read", {"path": str(link / "secret.txt")})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED

    async def test_bad_arguments_are_refused_before_anything_runs(
        self, executor: ToolExecutor
    ) -> None:
        out = await executor.execute("fs.read", {"wrong_field": 1})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.INVALID_ARGS

    async def test_binary_file_is_refused_rather_than_decoded(
        self, executor: ToolExecutor, root: Path
    ) -> None:
        (root / "bin.dat").write_bytes(b"\x00\x01\x02binary")
        out = await executor.execute("fs.read", {"path": str(root / "bin.dat")})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.EXECUTION_FAILED

    async def test_halt_stops_even_a_read(self, executor: ToolExecutor, root: Path) -> None:
        executor._engine.halt("test")
        out = await executor.execute("fs.read", {"path": str(root / "a.txt")})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED
        assert "halted" in out.error.message.lower()


class TestApprovalBinding:
    def _digest(self, root: Path, name: str) -> str:
        from oracle.policy.paths import PathResolver, Scope

        resolver = PathResolver([Scope("projects", root, writable=True)])
        rp = resolver.resolve(str(root / name))
        return digest_args({"path": str(root / name), **{"path": str(rp)}})

    async def test_tier_requiring_approval_refuses_without_one(
        self, tmp_path: Path, root: Path
    ) -> None:
        p = tmp_path / "policy.yaml"
        p.write_text(
            POLICY.format(root=root.as_posix()).replace(
                "fs.read:  { tier: T0, scopes: [projects] }",
                "fs.read:  { tier: T2, scopes: [projects] }",
            ),
            encoding="utf-8",
        )
        ex = ToolExecutor(
            build_registry(), PolicyEngine(load_policy(p)), AuditLog(tmp_path / "a.jsonl")
        )
        out = await ex.execute("fs.read", {"path": str(root / "a.txt")})
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.APPROVAL_REQUIRED

    async def test_approval_for_one_path_does_not_execute_another(
        self, tmp_path: Path, root: Path
    ) -> None:
        """Approving a plan does not approve a mutated version of it."""
        p = tmp_path / "policy.yaml"
        p.write_text(
            POLICY.format(root=root.as_posix()).replace(
                "fs.read:  { tier: T0, scopes: [projects] }",
                "fs.read:  { tier: T2, scopes: [projects] }",
            ),
            encoding="utf-8",
        )
        ex = ToolExecutor(
            build_registry(), PolicyEngine(load_policy(p)), AuditLog(tmp_path / "a.jsonl")
        )
        (root / "b.txt").write_text("other")

        ex.grant(
            Approval(
                approval_id="ap_1",
                tool="fs.read",
                args_digest=self._digest(root, "a.txt"),
                tier=Tier.T2,
                expires_at=time.time() + 300,
            )
        )
        out = await ex.execute("fs.read", {"path": str(root / "b.txt")}, approval_id="ap_1")
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.APPROVAL_INVALID
        assert "arguments changed" in out.error.message

    async def test_approval_is_single_use(self, tmp_path: Path, root: Path) -> None:
        p = tmp_path / "policy.yaml"
        p.write_text(
            POLICY.format(root=root.as_posix()).replace(
                "fs.read:  { tier: T0, scopes: [projects] }",
                "fs.read:  { tier: T2, scopes: [projects] }",
            ),
            encoding="utf-8",
        )
        ex = ToolExecutor(
            build_registry(), PolicyEngine(load_policy(p)), AuditLog(tmp_path / "a.jsonl")
        )
        ex.grant(
            Approval(
                approval_id="ap_1",
                tool="fs.read",
                args_digest=self._digest(root, "a.txt"),
                tier=Tier.T2,
                expires_at=time.time() + 300,
            )
        )
        first = await ex.execute("fs.read", {"path": str(root / "a.txt")}, approval_id="ap_1")
        assert first.ok
        second = await ex.execute("fs.read", {"path": str(root / "a.txt")}, approval_id="ap_1")
        assert not second.ok
        assert second.error is not None and "already used" in second.error.message

    def test_expired_approval_is_invalid(self) -> None:
        a = Approval("ap", "fs.read", "sha256:x", Tier.T2, expires_at=time.time() - 1)
        ok, why = a.valid_for("fs.read", "sha256:x", time.time())
        assert not ok and "expired" in why

    def test_approval_for_a_different_tool_is_invalid(self) -> None:
        a = Approval("ap", "fs.read", "sha256:x", Tier.T2, expires_at=time.time() + 60)
        ok, why = a.valid_for("fs.delete", "sha256:x", time.time())
        assert not ok and "fs.read" in why


class TestTaintThroughTheExecutor:
    async def test_untrusted_provenance_escalates_and_blocks(
        self, tmp_path: Path, root: Path
    ) -> None:
        p = tmp_path / "policy.yaml"
        p.write_text(
            POLICY.format(root=root.as_posix()).replace(
                "fs.read:  { tier: T0, scopes: [projects] }",
                "fs.read:  { tier: T1, scopes: [projects] }",
            ),
            encoding="utf-8",
        )
        ex = ToolExecutor(
            build_registry(), PolicyEngine(load_policy(p)), AuditLog(tmp_path / "a.jsonl")
        )
        clean = await ex.execute("fs.read", {"path": str(root / "a.txt")})
        assert clean.ok

        tainted = await ex.execute(
            "fs.read",
            {"path": str(root / "a.txt")},
            provenances=frozenset({Provenance.LOCAL_FOREIGN}),
        )
        assert not tainted.ok
        assert tainted.error is not None
        assert tainted.error.kind == ToolErrorKind.APPROVAL_REQUIRED
        assert tainted.verdict.tainted is True


class TestAuditing:
    async def test_every_denial_is_audited_with_its_rule(
        self, executor: ToolExecutor, tmp_path: Path
    ) -> None:
        await executor.execute("fs.read", {"path": r"C:\Windows\win.ini"})
        records = executor._audit.records()
        assert records, "a denial went unaudited"
        assert records[-1]["decision"] == "deny"
        assert records[-1]["rule"]

    async def test_successful_calls_are_audited(self, executor: ToolExecutor, root: Path) -> None:
        await executor.execute("fs.read", {"path": str(root / "a.txt")})
        rec = executor._audit.records()[-1]
        assert rec["outcome"] == "ok"
        assert rec["tool"] == "fs.read"
        assert rec["args_digest"].startswith("sha256:")

    async def test_audit_chain_stays_intact_across_mixed_traffic(
        self, executor: ToolExecutor, root: Path
    ) -> None:
        await executor.execute("fs.read", {"path": str(root / "a.txt")})
        await executor.execute("fs.read", {"path": r"C:\Windows\win.ini"})
        await executor.execute("sys.info", {})
        assert executor._audit.verify() == []

    async def test_denials_are_not_retryable(self, executor: ToolExecutor) -> None:
        """Retrying a policy denial is how an agent nags a user into approving."""
        out = await executor.execute("fs.read", {"path": r"C:\Windows\win.ini"})
        assert out.error is not None and out.error.retryable is False


class TestRegistryValidation:
    """Contract errors must be boot failures, never runtime surprises."""

    def test_writing_tool_cannot_claim_t0(self) -> None:
        from oracle.policy.model import Capability

        class A(FsReadArgs):
            pass

        @tool(
            id="bad.write",
            summary="x",
            args=A,
            result=ToolResult,
            capabilities={Capability.FS_WRITE},
            risk=Tier.T0,
        )
        async def bad(*, resolved: dict, args: A) -> ToolResult:  # pragma: no cover
            return ToolResult()

        with pytest.raises(ToolRegistryError, match="T0 means no side effect"):
            ToolRegistry().register(bad)

    def test_t3_tool_must_support_dry_run(self) -> None:
        from oracle.policy.model import Capability

        class A(FsReadArgs):
            pass

        @tool(
            id="bad.delete",
            summary="x",
            args=A,
            result=ToolResult,
            capabilities={Capability.FS_DELETE},
            risk=Tier.T3,
            dry_run=False,
        )
        async def bad(*, resolved: dict, args: A) -> ToolResult:  # pragma: no cover
            return ToolResult()

        with pytest.raises(ToolRegistryError, match="dry_run"):
            ToolRegistry().register(bad)

    def test_path_fields_must_exist_on_the_args_model(self) -> None:
        from oracle.policy.model import Capability

        class A(FsReadArgs):
            pass

        @tool(
            id="bad.paths",
            summary="x",
            args=A,
            result=ToolResult,
            capabilities={Capability.FS_READ},
            risk=Tier.T0,
            path_fields={"nonexistent"},
        )
        async def bad(*, resolved: dict, args: A) -> ToolResult:  # pragma: no cover
            return ToolResult()

        with pytest.raises(ToolRegistryError, match="path_fields"):
            ToolRegistry().register(bad)

    def test_phase2_registry_is_read_only(self) -> None:
        """The phase's central claim, asserted rather than trusted."""
        from oracle.policy.model import Capability

        writing = {
            Capability.FS_WRITE,
            Capability.FS_DELETE,
            Capability.PROC_SPAWN,
            Capability.NET_EGRESS,
            Capability.GIT_WRITE,
            Capability.INPUT_SYNTH,
            Capability.SYS_SETTINGS,
        }
        for contract in build_registry().all():
            assert not (contract.capabilities & writing), f"{contract.id} can write in Phase 2"
            assert contract.risk <= Tier.T1, f"{contract.id} is above T1 in Phase 2"

    def test_every_registered_tool_has_a_policy_rule(self) -> None:
        """A tool with no rule is denied by default — correct, but silent. Catch the
        mismatch here instead of at 3am."""
        policy = load_policy(Path("config/policy.yaml"))
        if policy.read_only:
            pytest.skip("real policy.yaml not loadable from this working directory")
        for contract in build_registry().all():
            assert contract.id in policy.tools, f"{contract.id} has no rule in config/policy.yaml"


class TestHaltThroughTheApi:
    """HALT must work from the API without involving the LLM, and must not clear
    itself (docs/SECURITY.md#emergency-stop-halt)."""

    def test_halt_then_resume_over_the_websocket(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from oracle.api.app import create_app
        from oracle.config import Settings

        settings = Settings(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            port=0,
            llm_enabled=False,
            projects_root=tmp_path / "projects",
            policy_path=Path("config/policy.yaml"),
        )
        with TestClient(create_app(settings)) as client:
            assert client.get("/api/v1/status").json()["policy"]["halted"] is False

            with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
                ws.send_json({"type": "halt", "payload": {"reason": "unit test"}})
                for _ in range(20):
                    ev = ws.receive_json()
                    if ev["type"] == "agent.state" and ev["payload"].get("state") == "halted":
                        break
                else:  # pragma: no cover
                    raise AssertionError("halt produced no state change")

            body = client.get("/api/v1/status").json()["policy"]
            assert body["halted"] is True
            assert body["halt_reason"] == "unit test"

            with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
                ws.send_json({"type": "resume", "payload": {}})
                for _ in range(20):
                    ev = ws.receive_json()
                    if ev["type"] == "agent.state" and ev["payload"].get("state") == "idle":
                        break
            assert client.get("/api/v1/status").json()["policy"]["halted"] is False


class TestExecutionCrossesTheProcessBoundary:
    """ADR-0003: with a host configured, tools run in the low-privilege child, and the
    gate still decides everything before the invocation crosses the pipe."""

    async def test_allowed_tool_runs_in_the_child(self, tmp_path: Path, root: Path) -> None:
        from oracle.toolhost import ToolHost

        p = tmp_path / "policy.yaml"
        p.write_text(POLICY.format(root=root.as_posix()), encoding="utf-8")
        host = ToolHost()
        ex = ToolExecutor(
            build_registry(),
            PolicyEngine(load_policy(p)),
            AuditLog(tmp_path / "a.jsonl"),
            host=host,
        )
        try:
            out = await ex.execute("fs.read", {"path": str(root / "a.txt")})
            assert out.ok
            assert out.result is not None
            assert out.result.text == "hello"  # type: ignore[attr-defined]
            assert host.running, "the tool should have run in a child process"
        finally:
            await host.stop()

    async def test_denied_tool_never_reaches_the_child(self, tmp_path: Path, root: Path) -> None:
        """The gate runs on the parent side. A denied call must not even start the
        host, let alone send it a frame."""
        from oracle.toolhost import ToolHost

        p = tmp_path / "policy.yaml"
        p.write_text(POLICY.format(root=root.as_posix()), encoding="utf-8")
        host = ToolHost()
        ex = ToolExecutor(
            build_registry(),
            PolicyEngine(load_policy(p)),
            AuditLog(tmp_path / "a.jsonl"),
            host=host,
        )
        try:
            out = await ex.execute("fs.read", {"path": r"C:\Windows\win.ini"})
            assert not out.ok
            assert out.error is not None and out.error.kind == ToolErrorKind.DENIED
            assert not host.running, "a denied call started the toolhost"
            assert host.stats.calls == 0
        finally:
            await host.stop()
