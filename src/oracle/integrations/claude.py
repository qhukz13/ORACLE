"""`ClaudeCodeAdapter`: drive `claude -p` and normalise its stream (INTEGRATIONS.md §3).

The contract this file is written against is *recorded*, not remembered:
`tests/fixtures/claude_stream/*.jsonl` is real CLI output from this machine, and the
contract tests replay it through a stub. Three rules the v2.1.238 recording forced:

* **`result` is the terminal semantic event, not the last line** — `system/task_summary`
  trails it. The stream ends at `result`; the remainder is drained, not parsed.
* **`system/init` is not necessarily first** — user-level hooks emit events before it.
* **Unknown event kinds are logged and skipped, never fatal** — the vocabulary grew
  between two minor CLI versions and will grow again.

No `--bare`, by measurement, not preference (2026-08-23): it authenticates only via API
key, and this machine runs on a subscription login. The isolation `--bare` provided is
rebuilt materially — the worktree scrub, owned by the workspace layer, is a precondition
of every invocation — plus `--setting-sources user` and `--strict-mcp-config` here.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import signal
import subprocess
import sys
from collections.abc import AsyncIterator, Sequence
from typing import Any

from oracle.integrations.types import (
    AgentCaps,
    AgentEvent,
    AgentEventKind,
    AgentHandle,
    AgentResult,
    HandoffPacket,
    Preflight,
    Workspace,
)
from oracle.logsink import get_logger

log = get_logger(__name__)

#: Seconds between escalation steps of `cancel()`: graceful → terminate → kill.
GRACE_S = 2.0
#: Kept from a failed run's stderr for the report. Full stderr goes nowhere — it is the
#: vendor's diagnostics, not ORACLE's log.
STDERR_TAIL_BYTES = 2000
FALLBACK_REMEDY = (
    "Delegation falls back to the on-disk Handoff Packet (INTEGRATIONS.md §6); "
    "install or authenticate the Claude Code CLI to delegate directly."
)


class ClaudeCodeAdapter:
    """One `claude -p` run per submission, scoped to a scrubbed worktree."""

    id = "claude-code"

    def __init__(self, argv: Sequence[str] = ("claude",), *, grace_s: float = GRACE_S) -> None:
        #: The executable as a vector, not a string, so tests can inject
        #: `(sys.executable, stub.py)` and exercise the identical spawn path.
        self.argv = list(argv)
        self.grace_s = grace_s

    def capabilities(self) -> AgentCaps:
        return AgentCaps(
            streaming=True,
            resume=True,
            structured_output=True,
            workspace_scoped=True,
            cost_reporting=True,
        )

    # -- preflight -----------------------------------------------------------

    async def preflight(self) -> Preflight:
        if shutil.which(self.argv[0]) is None:
            return Preflight(
                ok=False,
                reason=f"{self.argv[0]!r} is not on PATH",
                remedy=FALLBACK_REMEDY,
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.argv,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except (OSError, TimeoutError) as exc:
            return Preflight(ok=False, reason=f"--version failed: {exc}", remedy=FALLBACK_REMEDY)
        if proc.returncode != 0:
            return Preflight(
                ok=False,
                reason=f"--version exited {proc.returncode}",
                remedy=FALLBACK_REMEDY,
            )
        words = out.decode("utf-8", errors="replace").split()
        return Preflight(ok=True, version=words[0] if words else None)

    # -- submission ----------------------------------------------------------

    def command(self, packet: HandoffPacket, ws: Workspace) -> list[str]:
        """The pinned invocation, flag for flag (INTEGRATIONS.md §3). Public so the
        egress preview can show the exact command without submitting anything."""
        cmd = [
            *self.argv,
            "-p",
            packet.render_prompt(),
            "--setting-sources",
            "user",
            "--strict-mcp-config",
            "--output-format",
            "stream-json",
            "--verbose",
            "--allowedTools",
            ",".join(packet.allowed_tools),
            "--permission-mode",
            "dontAsk",
            "--add-dir",
            str(ws.path),
        ]
        if packet.context_dir is not None:
            # The rendered packet lives OUTSIDE the worktree so it never pollutes the
            # diff the result is judged by; the delegate still needs read access to it.
            cmd += ["--add-dir", packet.context_dir]
        if packet.result_schema is not None:
            cmd += ["--json-schema", json.dumps(packet.result_schema)]
        return cmd

    async def submit(self, packet: HandoffPacket, ws: Workspace) -> AgentHandle:
        # CREATE_NEW_PROCESS_GROUP is what makes CTRL_BREAK deliverable to the child on
        # Windows; without it, cancel() would start at terminate() — the vendor's clean
        # SIGINT path (finish the turn, run SessionEnd) would be unreachable.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        proc = await asyncio.create_subprocess_exec(
            *self.command(packet, ws),
            cwd=str(ws.path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            creationflags=flags,
        )
        handle = AgentHandle(task_id=packet.task_id, proc=proc)
        # Pump stderr from the start: an unread pipe fills at 64 KiB and a chatty child
        # would deadlock against it mid-run, which no test of ours would ever see.
        handle.pump = asyncio.create_task(self._pump_stderr(handle))
        log.info("delegate.submitted", adapter=self.id, task_id=packet.task_id, pid=proc.pid)
        return handle

    @staticmethod
    async def _pump_stderr(h: AgentHandle) -> None:
        assert h.proc.stderr is not None
        while chunk := await h.proc.stderr.read(4096):
            h.stderr += chunk

    # -- the stream ----------------------------------------------------------

    async def events(self, h: AgentHandle) -> AsyncIterator[AgentEvent]:
        assert h.proc.stdout is not None
        async for raw in h.proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                log.warning("delegate.unparseable_line", task_id=h.task_id, head=line[:120])
                continue
            for normalised in self._normalise(event, h):
                yield normalised
            if h.result is not None:
                break
        # Trailing housekeeping (`system/task_summary` et al.) is read so the child can
        # exit, and deliberately not parsed: the contract ends at `result`.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(h.proc.stdout.read(), timeout=10)
        h.drained = True

    def _normalise(self, event: dict[str, Any], h: AgentHandle) -> list[AgentEvent]:
        kind, subtype = event.get("type"), event.get("subtype")
        if kind == "system" and subtype == "init":
            h.session_id = event.get("session_id")
            problems = (event.get("mcp_server_errors") or []) + (event.get("plugin_errors") or [])
            if problems:
                # With --strict-mcp-config a silently unloaded server must fail the run,
                # not degrade into raw shell use — surfaced here, decided above the seam.
                log.warning("delegate.init_errors", task_id=h.task_id, errors=problems)
            return [AgentEvent(kind=AgentEventKind.STARTED, text=str(event.get("model") or ""))]
        if kind == "system" and subtype == "api_retry":
            return [AgentEvent(kind=AgentEventKind.RETRYING, text=str(event.get("error") or ""))]
        if kind == "assistant":
            return self._normalise_assistant(event)
        if kind == "result":
            h.result = event
            failed = bool(event.get("is_error"))
            return [
                AgentEvent(
                    kind=AgentEventKind.ERROR if failed else AgentEventKind.FINISHED,
                    text=str(event.get("result") or ""),
                )
            ]
        if kind == "user":
            # Tool results flowing back to the delegate: its business, not ours. The
            # verified outcome comes from the diff and the tests, never from here.
            return []
        log.debug("delegate.event_skipped", task_id=h.task_id, type=kind, subtype=subtype)
        return []

    @staticmethod
    def _normalise_assistant(event: dict[str, Any]) -> list[AgentEvent]:
        message = event.get("message") or {}
        if event.get("is_api_error_message"):
            # E.g. "Not logged in": the vendor reports auth failure as an assistant
            # message wearing an error flag, at zero cost and zero egress.
            text = " ".join(
                c.get("text", "") for c in message.get("content") or [] if c.get("type") == "text"
            )
            return [
                AgentEvent(
                    kind=AgentEventKind.ERROR,
                    text=text or str(event.get("error") or "unknown error"),
                )
            ]
        from_subagent = event.get("parent_tool_use_id") is not None
        out: list[AgentEvent] = []
        for content in message.get("content") or []:
            ctype = content.get("type")
            if ctype == "text" and content.get("text"):
                out.append(
                    AgentEvent(
                        kind=AgentEventKind.TEXT,
                        text=content["text"],
                        from_subagent=from_subagent,
                    )
                )
            elif ctype == "thinking" and content.get("thinking"):
                out.append(
                    AgentEvent(
                        kind=AgentEventKind.THINKING,
                        text=content["thinking"],
                        from_subagent=from_subagent,
                    )
                )
            elif ctype == "tool_use":
                out.append(
                    AgentEvent(
                        kind=AgentEventKind.TOOL_USE,
                        tool=str(content.get("name") or "?"),
                        from_subagent=from_subagent,
                    )
                )
        return out

    # -- cancellation --------------------------------------------------------

    async def cancel(self, h: AgentHandle) -> None:
        """SIGINT-equivalent first (the vendor finishes its turn and runs SessionEnd),
        then terminate, then kill — each step only if the previous one did not land
        within the grace period. The enclosing Job Object remains the final backstop
        (ARCHITECTURE.md §3); this is the polite path, not the guarantee."""
        proc = h.proc
        if proc.returncode is not None:
            return
        log.info("delegate.cancel", task_id=h.task_id, pid=proc.pid)
        interrupt = signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT
        for step in ("interrupt", "terminate", "kill"):
            with contextlib.suppress(ProcessLookupError, OSError, ValueError):
                if step == "interrupt":
                    proc.send_signal(interrupt)
                elif step == "terminate":
                    proc.terminate()
                else:
                    proc.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=self.grace_s)
                return

    # -- collection ----------------------------------------------------------

    async def collect(self, h: AgentHandle) -> AgentResult:
        """ORACLE's reading of the run. Callable without iterating `events()` first —
        the stream is drained here if nobody consumed it, because an unread stdout pipe
        would otherwise block the child's exit forever."""
        if not h.drained:
            async for _ in self.events(h):
                pass
        exit_code = await h.proc.wait()
        if h.pump is not None:
            # Ends at stderr EOF, which the exit above guarantees is coming.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(h.pump, timeout=5)
        result = h.result or {}
        structured = result.get("structured_output")
        return AgentResult(
            success=exit_code == 0 and h.result is not None and not result.get("is_error", False),
            exit_code=exit_code,
            result_text=str(result.get("result") or ""),
            structured=structured if isinstance(structured, dict) else None,
            cost_usd=result.get("total_cost_usd"),
            duration_ms=result.get("duration_ms"),
            num_turns=result.get("num_turns"),
            session_id=result.get("session_id") or h.session_id,
            stderr_tail=bytes(h.stderr[-STDERR_TAIL_BYTES:]).decode("utf-8", errors="replace"),
        )
