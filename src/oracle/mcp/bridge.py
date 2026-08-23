"""The stdio bridge: a dumb pipe between a delegated agent and ORACLE's daemon.

Run as `python -m oracle.mcp` by the delegate's CLI (`--mcp-config`), one process per
delegation. It speaks MCP's newline-delimited JSON-RPC 2.0 on stdin/stdout and forwards
every `tools/call` to the daemon over loopback.

**It holds no policy.** No registry, no engine, no scopes — the tool list comes from the
daemon and every call is decided there. That is deliberate and it is the design: a
bridge that evaluated anything would be the second permission system INTEGRATIONS.md §4
exists to delete. Everything it knows arrives in environment variables:

    ORACLE_MCP_URL     the daemon's loopback base URL
    ORACLE_MCP_TOKEN   the delegation capability (scoped, expiring, revocable)

Failure is loud, not silent: if the daemon is unreachable the tool list is empty and
every call errors, so `mcp_server_errors` shows up in the client's `system/init` and the
delegation fails rather than degrading into raw shell use.

Written against the wire format rather than the SDK — `mcp==2.0.0` works but brings 24
packages, including `cryptography`, `pywin32` and `opentelemetry-api`, into the daemon's
dependency tree for a protocol that is four methods of line-delimited JSON. The contract
is pinned the way P6-T1 pinned the vendor stream: by recording the real client's
exchange into `tests/fixtures/mcp/` and replaying it in CI.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

#: One frame off the client, or b"" at EOF.
ReadLine = Callable[[], Awaitable[bytes]]

#: The version this bridge implements. Echoed back at `initialize`; a client asking for
#: a different one is told what we speak rather than being guessed at.
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "oracle"

#: JSON-RPC's own codes. -32601 is "method not found", which is what an MCP client
#: expects for anything outside the four methods below.
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


class Bridge:
    """One MCP session. Owns no state beyond the daemon's address."""

    def __init__(self, base_url: str, token: str, *, timeout_s: float = 300.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------ transport

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(f"{self.base_url}{path}", json=body)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data

    # ------------------------------------------------------------------ methods

    async def list_tools(self) -> list[dict[str, Any]]:
        """The daemon decides what this delegation may see. An unreachable daemon
        yields an empty list, which the client surfaces as a server error."""
        try:
            data = await self._post("/api/v1/mcp/tools", {"token": self.token})
        except Exception as exc:
            print(f"oracle-mcp: cannot reach ORACLE: {exc}", file=sys.stderr, flush=True)
            return []
        tools: list[dict[str, Any]] = data.get("tools", [])
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Forward and translate. MCP wants content blocks; a refusal is a *result*
        with `isError`, not a protocol error — the delegate should read it and adapt,
        not treat it as a broken server."""
        try:
            data = await self._post(
                "/api/v1/mcp/call",
                {"token": self.token, "tool": name, "arguments": arguments},
            )
        except Exception as exc:
            return _content(f"ORACLE is unreachable: {exc}", is_error=True)
        if not data.get("ok", False):
            return _content(str(data.get("payload", {}).get("error", "refused")), is_error=True)
        return _content(json.dumps(data.get("payload", {}), ensure_ascii=False, indent=2))

    async def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """One JSON-RPC request → one response, or None for a notification."""
        method = str(request.get("method", ""))
        request_id = request.get("id")

        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"},
                "instructions": (
                    "ORACLE's guarded tools. Calls are policy-checked, audited, and scoped to "
                    "this delegation's workspace. Anything needing human approval is refused "
                    "here — put the request in your result instead."
                ),
            }
        elif method in ("notifications/initialized", "notifications/cancelled"):
            return None  # notifications carry no id and take no answer
        elif method == "tools/list":
            result = {"tools": await self.list_tools()}
        elif method == "tools/call":
            params = request.get("params") or {}
            result = await self.call_tool(
                str(params.get("name", "")), dict(params.get("arguments") or {})
            )
        elif method == "ping":
            result = {}
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": METHOD_NOT_FOUND, "message": f"unknown method {method!r}"},
            }

        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _content(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


async def serve(bridge: Bridge, read_line: ReadLine, writer: Any) -> None:
    """Read requests until stdin closes. One malformed line must not end the session:
    the client is entitled to keep talking after a bad frame.

    `read_line` is injected rather than taken from a `StreamReader`, because on Windows
    there is no way to get one for stdin — see `stdin_reader`.
    """
    while line := await read_line():
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            request = json.loads(text)
        except json.JSONDecodeError:
            print(f"oracle-mcp: unparseable frame: {text[:120]}", file=sys.stderr, flush=True)
            continue
        try:
            response = await bridge.handle(request)
        except Exception as exc:  # pragma: no cover - defensive; a dead session is worse
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": INTERNAL_ERROR, "message": str(exc)},
            }
        if response is not None:
            writer.write(json.dumps(response, ensure_ascii=False) + "\n")
            writer.flush()


def stdin_reader() -> ReadLine:
    """One line of stdin, off the event loop.

    **Measured, not assumed** (2026-08-24): `loop.connect_read_pipe(sys.stdin)` — the
    obvious asyncio spelling, and what this file did first — is broken on Windows.
    `ProactorEventLoop` raises `AttributeError: '_ProactorReadPipeTransport' object has
    no attribute '_empty_waiter'` on the first read, and the bridge hangs having read
    nothing. Since this bridge only ever runs on the delegate's machine, and the
    delegate's machine is this one, that is not a portability nicety: it is the
    difference between an MCP server and a process that looks alive and does nothing.
    Caught by spawning it as a real subprocess in `tests/test_mcp_server.py`.

    A worker thread per read is fine here: a delegate sends a handful of frames per
    minute, and the alternative costs a platform-specific transport for each OS.
    """
    return lambda: asyncio.to_thread(sys.stdin.buffer.readline)


async def main() -> int:
    url = os.environ.get("ORACLE_MCP_URL", "")
    token = os.environ.get("ORACLE_MCP_TOKEN", "")
    if not url or not token:
        print("oracle-mcp: ORACLE_MCP_URL and ORACLE_MCP_TOKEN are required", file=sys.stderr)
        return 2

    # JSON-RPC is UTF-8 on the wire, and on Windows a child process's stdout defaults to
    # the console codepage instead — so the first non-ASCII character in a tool result
    # (or in this server's own instructions) reaches the client as mojibake and the
    # frame fails to parse. Measured the same way as the stdin defect above: by
    # spawning the real process and reading its bytes.
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
    await serve(Bridge(url, token), stdin_reader(), sys.stdout)
    return 0
