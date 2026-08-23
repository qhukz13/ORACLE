# 2026-08-24 — P6-T3: the delegation hole, closed — and two Windows defects the tests caught

Requirements 2–6 of [P6-T3](../../docs/current_task.md) built; requirement 1 settled for the
transport and pending one supervised live run against the real CLI. 33 new tests, gate green.

## The headline: writing the test found the bug twice

The bridge was written, unit-tested, type-checked and green — **and it could not have worked on this
machine.** Both defects surfaced only when the test spawned it as a real subprocess, which is the
whole argument for that test existing:

1. **`loop.connect_read_pipe(sys.stdin)` is broken on Windows.** The obvious asyncio spelling, and
   what this file did first. `ProactorEventLoop` raises `AttributeError: '_ProactorReadPipeTransport'
   object has no attribute '_empty_waiter'` on the first read; the bridge then hangs having read
   nothing. Not a portability nicety — the delegate's machine *is* this machine, so this is the
   difference between an MCP server and a process that looks alive and does nothing. Fixed by
   reading stdin on a worker thread (`asyncio.to_thread(sys.stdin.buffer.readline)`); a delegate
   sends a handful of frames a minute, so a thread per read costs nothing.
2. **A child process's stdout is the console codepage, not UTF-8.** JSON-RPC is UTF-8 on the wire.
   The first non-ASCII character — an em dash in this server's own `instructions` string — reached
   the client as mojibake and the frame failed to parse. Fixed with an explicit
   `sys.stdout.reconfigure(encoding="utf-8")`.

Same lesson as the `fnmatch`/`LCMapStringEx` finding in P5-T2 and `pywinpty` in OQ-09: on this
platform, "it type-checks" and "it runs" are unrelated claims.

## The design, in three sentences

**The bridge is a dumb pipe.** `python -m oracle.mcp` holds no registry, no scopes and no engine;
every call is forwarded to the daemon and executed by the *same* `ToolExecutor` as everything else.
**The token is a capability, not a bearer credential** — HMAC-signed, naming its tool allowlist, its
worktree and its expiry, with a process-lifetime key that is never written to disk and revocation on
every exit path including HALT. **T2+ is refused, never prompted**, because an unattended delegate
that could raise confirmation dialogs would be prompt fatigue as a service.

## The dependency question, measured

`mcp==2.0.0` installs and `MCPServer.run_stdio_async` works here — and it costs **24 packages**
(`cryptography`, `pywin32`, `opentelemetry-api`, `jsonschema`, `sse-starlette`…) in the daemon's
trusted base, for a protocol that is four methods of newline-delimited JSON-RPC. ORACLE implements
those four and pins them with tests that drive raw frames. **No ledger line, because no dependency**
— and the measurement is written into TECH_STACK.md as the justification, so taking the SDK later is
a decision with evidence rather than a reversal.

## What is asserted, not described

| | |
|---|---|
| `tests/security/test_mcp_tokens.py` | Forged, expired, revoked, path-escape, tool-not-lent, T3 — six refusals, each asserting the call **never reached the executor** (a `SpyExecutor` counts, because "errored" and "did not run" are different properties). |
| `tests/test_mcp_server.py` | The protocol in raw frames; the catalogue; the seam through the real FastAPI app into the real executor and audit log; and the bridge **as a spawned process**. |
| `tests/test_integrations_claude.py` | A run whose MCP server failed to load is never a success — the vendor reports exit 0 and `is_error: false`, and ORACLE overrides it, because that run worked outside the gate. |
| `tests/test_delegation_service.py` | The token is live during the run and refused the moment it ends; the config file carrying it is deleted. |

The seam test also earned its keep on the first run: it failed because the gate refused a tmp path
that no scope covered. That was the gate working, and it is now a fixture with its own policy.

## Open

Requirement 1's **live recording against the real CLI** — one supervised run, payload previewed
first, exactly as P6-T1 did it. Everything else in the transport decision is settled offline.
