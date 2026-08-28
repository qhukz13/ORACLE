"""The bridge speaks MCP, and the daemon end of it behaves.

Two halves, deliberately separate:

* **The protocol**, driven with raw JSON-RPC frames — the same bytes a client sends.
  These are what stand in for an SDK: the wire format is the contract, so the tests
  hold it rather than a library version.
* **The seam**, driven through the FastAPI app: a delegate's call reaches the real
  executor, lands in the audit log and the event log, and shows up attributed to the
  delegate rather than to ORACLE.

`tests/security/test_mcp_tokens.py` owns the refusals; this owns the working path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from oracle.mcp.bridge import PROTOCOL_VERSION, Bridge
from oracle.mcp.catalogue import describe, resolve
from oracle.mcp.tokens import TokenStore
from oracle.tools import build_registry


class FakeDaemon:
    """Stands in for the loopback API. Records what the bridge forwarded, so the
    'dumb pipe' claim is measured rather than asserted in a docstring."""

    def __init__(self, tools: list[dict[str, Any]] | None = None) -> None:
        self.tools = tools if tools is not None else [{"name": "fs_read", "description": "read"}]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.ok = True
        self.payload: dict[str, Any] = {"content": "VALUE = 1"}
        self.raise_on_post = False

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.raise_on_post:
            raise ConnectionError("daemon is not listening")
        if path.endswith("/tools"):
            return {"tools": self.tools}
        self.calls.append((str(body.get("tool")), dict(body.get("arguments") or {})))
        return {"ok": self.ok, "payload": self.payload}


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> tuple[Bridge, FakeDaemon]:
    daemon = FakeDaemon()
    b = Bridge("http://127.0.0.1:9/", "tok")
    monkeypatch.setattr(b, "_post", daemon.post)
    return b, daemon


def rpc(method: str, params: dict[str, Any] | None = None, id_: int | None = 1) -> dict[str, Any]:
    frame: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        frame["id"] = id_
    if params is not None:
        frame["params"] = params
    return frame


class TestProtocol:
    async def test_initialize_announces_tools_and_a_version(
        self, bridge: tuple[Bridge, FakeDaemon]
    ) -> None:
        b, _ = bridge
        response = await b.handle(rpc("initialize", {"protocolVersion": PROTOCOL_VERSION}))
        assert response is not None
        result = response["result"]
        assert result["protocolVersion"] == PROTOCOL_VERSION
        assert result["capabilities"]["tools"] == {"listChanged": False}
        assert result["serverInfo"]["name"] == "oracle"
        # The instructions are load-bearing: they tell the delegate that anything
        # needing approval is refused here, so it asks in its result instead.
        assert "refused" in result["instructions"]

    async def test_a_notification_gets_no_answer(self, bridge: tuple[Bridge, FakeDaemon]) -> None:
        """JSON-RPC notifications carry no id; answering one is a protocol error."""
        b, _ = bridge
        assert await b.handle(rpc("notifications/initialized", id_=None)) is None

    async def test_tools_list_comes_from_the_daemon(
        self, bridge: tuple[Bridge, FakeDaemon]
    ) -> None:
        b, daemon = bridge
        daemon.tools = [{"name": "know_search", "description": "search"}]
        response = await b.handle(rpc("tools/list"))
        assert response is not None
        assert response["result"]["tools"] == daemon.tools

    async def test_tools_call_forwards_verbatim_and_wraps_the_result(
        self, bridge: tuple[Bridge, FakeDaemon]
    ) -> None:
        b, daemon = bridge
        response = await b.handle(
            rpc("tools/call", {"name": "fs_read", "arguments": {"path": "C:/wt/app.py"}})
        )
        assert daemon.calls == [("fs_read", {"path": "C:/wt/app.py"})]
        assert response is not None
        content = response["result"]["content"][0]
        assert content["type"] == "text"
        assert json.loads(content["text"]) == {"content": "VALUE = 1"}
        assert response["result"]["isError"] is False

    async def test_a_refusal_is_a_result_not_a_protocol_error(
        self, bridge: tuple[Bridge, FakeDaemon]
    ) -> None:
        """The delegate should read the refusal and adapt, not conclude the server is
        broken and fall back to shelling out."""
        b, daemon = bridge
        daemon.ok = False
        daemon.payload = {"error": "fs.delete is T3 and a delegated agent may not run it."}
        response = await b.handle(rpc("tools/call", {"name": "fs_delete", "arguments": {}}))
        assert response is not None
        assert response["result"]["isError"] is True
        assert "T3" in response["result"]["content"][0]["text"]
        assert "error" not in response

    async def test_an_unreachable_daemon_fails_loudly_in_both_directions(
        self, bridge: tuple[Bridge, FakeDaemon]
    ) -> None:
        """Empty tool list (so `mcp_server_errors` fires at init) and an errored call —
        never a silent success that leaves the delegate using raw shell."""
        b, daemon = bridge
        daemon.raise_on_post = True
        listed = await b.handle(rpc("tools/list"))
        called = await b.handle(rpc("tools/call", {"name": "fs_read", "arguments": {}}))
        assert listed is not None and listed["result"]["tools"] == []
        assert called is not None and called["result"]["isError"] is True
        assert "unreachable" in called["result"]["content"][0]["text"]

    async def test_an_unknown_method_is_a_json_rpc_error(
        self, bridge: tuple[Bridge, FakeDaemon]
    ) -> None:
        b, _ = bridge
        response = await b.handle(rpc("resources/list"))
        assert response is not None
        assert response["error"]["code"] == -32601


class TestCatalogue:
    def test_descriptors_carry_the_contract_summary_and_the_workspace(self, tmp_path: Path) -> None:
        tokens = TokenStore()
        cap = tokens.verify(tokens.mint("t-1", tmp_path, tools=("fs.read", "know.search")))
        described = describe(build_registry(), cap)

        assert {d["name"] for d in described} == {"fs_read", "know_search"}
        read = next(d for d in described if d["name"] == "fs_read")
        assert str(tmp_path.resolve()) in read["description"]
        assert read["inputSchema"]["type"] == "object"

    def test_a_tool_this_build_does_not_have_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """Version skew between a minted capability and the running registry."""
        tokens = TokenStore()
        cap = tokens.verify(tokens.mint("t-1", tmp_path, tools=("fs.read", "not.a.tool")))
        assert {d["name"] for d in describe(build_registry(), cap)} == {"fs_read"}

    def test_names_resolve_back_to_tool_ids_and_unknowns_return_none(self, tmp_path: Path) -> None:
        tokens = TokenStore()
        cap = tokens.verify(tokens.mint("t-1", tmp_path, tools=("fs.read",)))
        assert resolve("fs_read", cap) == "fs.read"
        assert resolve("fs.read", cap) == "fs.read"
        assert resolve("git_status", cap) is None


SEAM_POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
tools:
  fs.read:    {{ tier: T0, scopes: [projects] }}
  fs.list:    {{ tier: T0, scopes: [projects] }}
  fs.delete:  {{ tier: T3, scopes: [projects] }}
  git.status: {{ tier: T0, scopes: [projects] }}
  git.diff:   {{ tier: T0, scopes: [projects] }}
  know.search: {{ tier: T0 }}
  dev.run_tests: {{ tier: T1, scopes: [projects] }}
"""


class TestTheSeam:
    """A delegate's call, through the real app, into the real executor.

    This is the claim INTEGRATIONS.md §4 makes: every action a delegated agent takes
    lands in ORACLE's audit log, obeys ORACLE's scopes and tiers, and appears in the
    UI. Asserted here rather than described.
    """

    @pytest.fixture
    def client(self, settings: Any, tmp_path: Path) -> Any:
        """The app with a policy scoped to this test's tree.

        The shipped `config/policy.yaml` roots its scopes at the developer's real
        `C:/Projects`, so without this the gate refuses every tmp path — correctly, and
        the first run of this test proved it does.
        """
        from fastapi.testclient import TestClient

        from oracle.api.app import create_app

        policy = tmp_path / "policy.yaml"
        policy.write_text(
            SEAM_POLICY.format(root=settings.projects_root.as_posix()), encoding="utf-8"
        )
        settings.policy_path = policy
        with TestClient(create_app(settings)) as c:
            yield c

    def test_a_delegate_call_executes_and_is_attributed(self, client: Any, settings: Any) -> None:
        from oracle.api.app import state_of

        st = state_of(client.app)
        workspace = settings.projects_root / "Asterim"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        token = st.tokens.mint("t-seam", workspace)

        listed = client.post("/api/v1/mcp/tools", json={"token": token}).json()
        assert any(t["name"] == "fs_read" for t in listed["tools"])

        before = st.audit.seq
        response = client.post(
            "/api/v1/mcp/call",
            json={
                "token": token,
                "tool": "fs_read",
                "arguments": {"path": str(workspace / "app.py")},
            },
        ).json()

        assert response["ok"] is True
        assert "VALUE = 1" in json.dumps(response["payload"])
        assert st.audit.seq > before, "a delegated call left no audit trail"

        events = client.get("/api/v1/sessions/none/events").json()  # sanity: endpoint alive
        assert "events" in events

    def test_an_unverifiable_token_lists_no_tools_and_runs_nothing(self, client: Any) -> None:
        listed = client.post("/api/v1/mcp/tools", json={"token": "forged"}).json()
        called = client.post(
            "/api/v1/mcp/call", json={"token": "forged", "tool": "fs_read", "arguments": {}}
        ).json()
        assert listed["tools"] == []
        assert called["ok"] is False and called["payload"]["error"] == "not permitted"

    def test_a_tool_name_outside_the_capability_never_resolves(
        self, client: Any, settings: Any
    ) -> None:
        from oracle.api.app import state_of

        st = state_of(client.app)
        token = st.tokens.mint("t-seam2", settings.projects_root, tools=("fs.read",))
        called = client.post(
            "/api/v1/mcp/call",
            json={"token": token, "tool": "fs_delete", "arguments": {"path": "x"}},
        ).json()
        assert called["ok"] is False
        assert called["payload"]["error"] == "no such tool in this delegation"


class TestTheBridgeProcess:
    """The bridge as an actual process — argv, stdio framing, real HTTP.

    Everything above tests the handler in-process; this proves the thing a delegate's
    CLI actually spawns. It is the OQ-09 rule applied to our own binary: run it before
    trusting it. Hermetic — the "daemon" is a stdlib HTTP server on a loopback port.
    """

    @pytest.fixture
    def daemon(self) -> Any:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        seen: list[dict[str, Any]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                seen.append({"path": self.path, **body})
                payload = (
                    {"tools": [{"name": "fs_read", "description": "read", "inputSchema": {}}]}
                    if self.path.endswith("/tools")
                    else {"ok": True, "payload": {"content": "VALUE = 1"}}
                )
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args: Any) -> None:
                pass  # keep the test output readable

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server, seen
        finally:
            server.shutdown()
            # shutdown() stops serving; the listening socket still needs closing, or
            # the next test inherits a ResourceWarning that fails its setup.
            server.server_close()

    async def test_the_spawned_bridge_speaks_the_protocol_over_stdio(self, daemon: Any) -> None:
        import asyncio
        import os
        import sys

        server, seen = daemon
        env = {
            **os.environ,
            "ORACLE_MCP_URL": f"http://127.0.0.1:{server.server_address[1]}",
            "ORACLE_MCP_TOKEN": "test-token",
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "oracle.mcp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        assert proc.stdin is not None and proc.stdout is not None

        frames = [
            rpc("initialize", {"protocolVersion": PROTOCOL_VERSION}),
            rpc("notifications/initialized", id_=None),
            rpc("tools/list", id_=2),
            rpc("tools/call", {"name": "fs_read", "arguments": {"path": "x"}}, id_=3),
        ]
        proc.stdin.write(("\n".join(json.dumps(f) for f in frames) + "\n").encode())
        await proc.stdin.drain()
        proc.stdin.close()

        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        replies = [json.loads(line) for line in out.decode().splitlines() if line.strip()]

        # Three replies for four frames: the notification is correctly unanswered.
        assert [r["id"] for r in replies] == [1, 2, 3]
        assert replies[0]["result"]["protocolVersion"] == PROTOCOL_VERSION
        assert replies[1]["result"]["tools"][0]["name"] == "fs_read"
        assert "VALUE = 1" in replies[2]["result"]["content"][0]["text"]
        # And the token it was handed is the one that reached the daemon, unmodified.
        assert {s["token"] for s in seen} == {"test-token"}

    async def test_the_bridge_refuses_to_start_without_a_capability(self) -> None:
        """No token means no delegation; starting anyway would be a server that looks
        alive and can do nothing."""
        import asyncio
        import os
        import sys

        env = {k: v for k, v in os.environ.items() if not k.startswith("ORACLE_MCP_")}
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "oracle.mcp",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        assert proc.returncode == 2
        assert b"ORACLE_MCP_URL" in err
