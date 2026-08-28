"""`AntigravityAdapter`: drive `agy -p` and normalise its stream (INTEGRATIONS.md §5).

The same seam as `ClaudeCodeAdapter`, deliberately rhyming with it, but the vendor
differs in four ways that are not cosmetic, and each one is recorded in
`tests/fixtures/agents/antigravity/` rather than remembered:

* **The prompt is the value of `-p`, last** — the opposite of Claude's stdin. Cross-checked
  against Asterim's working integration before a line of this was written.
* **The envelope names its own payload**: `{"event": "step_update", "step_update": {...}}`.
  So `body = obj[obj["event"]]`, never `obj["payload"]`. `conversation_id` appears both
  beside `event` and inside the body; both are read.
* **Without `--dangerously-skip-permissions`, this CLI is read-only.** Measured
  2026-08-24: `view_file` runs unprompted, `write_to_file` and `run_command` are
  soft-denied and the run ends `status: ERROR`, exit 1. ORACLE will not pass that flag
  (INTEGRATIONS.md §5), so Antigravity can hold read-only roles — planner, reviewer,
  researcher — and never `coder`. The denial is surfaced, not swallowed.
* **There is no per-run MCP flag and no allow-list flag.** `agy mcp` edits global config;
  `--add-dir` scopes the filesystem and nothing scopes the toolset. A packet that asks
  for ORACLE's tool server therefore cannot be honoured here, and `command()` fails
  closed rather than running a delegation that silently lost its gate.

`--output-format` is never omitted: default text mode is where issue #76 eats stdout when
it is not a TTY (OQ-05). One rule, zero cost, since ORACLE wants structured output anyway.
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

#: Seconds between escalation steps of `cancel()`: interrupt → terminate → kill.
GRACE_S = 2.0
#: Kept from a failed run's stderr for the report, as with Claude.
STDERR_TAIL_BYTES = 2000
#: `--print-timeout`. The vendor default is 5m; ORACLE asks for longer because a planning
#: call over a real objective is not a chat turn. It is not the cancellation path — that
#: is `cancel()` — it is the backstop for a child that stopped talking entirely.
PRINT_TIMEOUT = "10m"
#: The vendor's terminal status vocabulary (INTEGRATIONS.md §5). Exactly one of them is
#: success. `CANCELED`/`INTERRUPTED` are what a cancelled run *should* report and, measured
#: 2026-08-24, never do: an interrupted run emits `status: ERROR` with the actively
#: misleading message "timeout waiting for response", ~110 ms after the signal. They stay
#: in the vocabulary because the vendor documents them and may start sending them.
SUCCESS_STATUS = "SUCCESS"
CANCELLED_STATUSES = frozenset({"CANCELED", "INTERRUPTED"})
FALLBACK_REMEDY = (
    "Delegation falls back to the on-disk Handoff Packet (INTEGRATIONS.md §6); "
    "install or authenticate the Antigravity CLI to delegate directly."
)
#: `agy models` round-trips to the vendor and costs no model tokens, which makes it the
#: only honest authentication probe available: every `-p` call costs ~15k input tokens
#: before the model reads a word (OQ-05), so preflight must not use one.
AUTH_PROBE = "models"
#: ASSUMPTION: that an unauthenticated `agy models` exits non-zero and says one of these.
#: The unauthenticated state could NOT be observed on this machine (2026-08-24): hiding
#: HOME/USERPROFILE/APPDATA/LOCALAPPDATA/XDG_CONFIG_HOME did not deauthenticate the CLI,
#: so its credentials live somewhere else entirely (INTEGRATIONS.md §5, "What could not
#: be observed"). This list is written from the vendor's documented behaviour. If the
#: match fails, preflight still returns `ok=False` with the raw text and the fallback
#: remedy — wrong wording, right decision — which is the failure mode to prefer.
AUTH_WORDS = ("auth", "log in", "login", "sign in", "signin", "credential", "not logged")


class AntigravityAdapter:
    """One `agy -p` run per submission, scoped to a worktree by `--add-dir`."""

    id = "antigravity"

    def __init__(
        self,
        argv: Sequence[str] = ("agy",),
        *,
        grace_s: float = GRACE_S,
        print_timeout: str = PRINT_TIMEOUT,
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        #: The executable as a vector, not a string, so tests can inject
        #: `(sys.executable, stub.py)` and exercise the identical spawn path.
        self.argv = list(argv)
        self.grace_s = grace_s
        self.print_timeout = print_timeout
        #: Both are vendor knobs the capability registry sets per role (PLANNER.md §5);
        #: absent means the CLI's own default, which is what a spike should measure first.
        self.model = model
        self.effort = effort

    def capabilities(self) -> AgentCaps:
        return AgentCaps(
            streaming=True,
            resume=True,  # --continue / --conversation <id>
            structured_output=True,  # --json-schema, filtered into result.structured_output
            workspace_scoped=True,  # --add-dir; the toolset itself is NOT scopeable
            # `usage` reports tokens, never money: this is quota-metered, and reporting a
            # fabricated dollar figure would be worse than reporting none.
            cost_reporting=False,
        )

    # -- preflight -----------------------------------------------------------

    async def preflight(self) -> Preflight:
        """Three states, distinguished: binary missing · present but unauthenticated ·
        ready. The middle one costs a `models` round trip rather than a `-p` call, so
        preflight stays free — the point of preflight is to decide *before* spending."""
        if shutil.which(self.argv[0]) is None:
            return Preflight(
                ok=False,
                reason=f"{self.argv[0]!r} is not on PATH",
                remedy=FALLBACK_REMEDY,
            )
        version, failure = await self._probe("--version", timeout_s=15)
        if failure is not None:
            return failure
        _, failure = await self._probe(AUTH_PROBE, timeout_s=30)
        if failure is not None:
            return failure
        return Preflight(ok=True, version=version)

    async def _probe(self, *args: str, timeout_s: float) -> tuple[str | None, Preflight | None]:
        """Run a free subcommand. Returns its first word, or the Preflight that explains
        why there isn't one."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.argv,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except (OSError, TimeoutError) as exc:
            return None, Preflight(
                ok=False, reason=f"{args[0]} failed: {exc}", remedy=FALLBACK_REMEDY
            )
        text = (out + err).decode("utf-8", errors="replace")
        if proc.returncode != 0:
            lowered = text.lower()
            auth = any(word in lowered for word in AUTH_WORDS)
            return None, Preflight(
                ok=False,
                reason=(
                    "the CLI is installed but not authenticated"
                    if auth
                    else f"{args[0]} exited {proc.returncode}: {text.strip()[:200]}"
                ),
                remedy=(
                    "Sign in to Antigravity, then retry; until then, "
                    + FALLBACK_REMEDY[0].lower()
                    + FALLBACK_REMEDY[1:]
                    if auth
                    else FALLBACK_REMEDY
                ),
            )
        words = out.decode("utf-8", errors="replace").split()
        return (words[0] if words else None), None

    # -- submission ----------------------------------------------------------

    def command(self, packet: HandoffPacket, ws: Workspace) -> list[str]:
        """The pinned invocation, flag for flag (INTEGRATIONS.md §5). Public so the egress
        preview can show the exact command without submitting anything.

        `-p` goes last, carrying the prompt, because that is what this CLI parses.
        """
        if packet.mcp_config is not None:
            # Fail closed. `agy` has no `--mcp-config`: its MCP servers are global config
            # edited by `agy mcp`, so honouring this would mean mutating machine state on
            # behalf of one delegation — and ignoring it would mean running a delegate
            # that believes it has ORACLE's guarded tools and silently does not.
            raise ValueError(
                "the Antigravity CLI cannot be lent ORACLE's tool server per run "
                "(no --mcp-config); route packets needing it to the Claude adapter"
            )
        cmd = [
            *self.argv,
            "--output-format",
            "stream-json",
            "--print-timeout",
            self.print_timeout,
            "--add-dir",
            str(ws.path),
        ]
        if packet.context_dir is not None:
            # The rendered packet lives OUTSIDE the worktree so it never pollutes the diff
            # the result is judged by; the delegate still needs read access to it.
            cmd += ["--add-dir", packet.context_dir]
        if self.model is not None:
            cmd += ["--model", self.model]
        if self.effort is not None:
            cmd += ["--effort", self.effort]
        if packet.result_schema is not None:
            cmd += ["--json-schema", json.dumps(packet.result_schema)]
        # Last, and the prompt is its value: the opposite of Claude's stdin.
        cmd += ["-p", packet.render_prompt()]
        return cmd

    async def submit(self, packet: HandoffPacket, ws: Workspace) -> AgentHandle:
        if packet.allowed_tools and packet.allowed_tools != ("Read",):
            # There is no allow-list flag. Writes are soft-denied by the vendor, which is
            # the posture ORACLE wants, but a packet that *asked* for write tools would be
            # silently downgraded — so say it out loud rather than let a task fail later
            # looking like a model failure.
            log.warning(
                "delegate.tools_not_expressible",
                adapter=self.id,
                task_id=packet.task_id,
                requested=list(packet.allowed_tools),
                effect="the CLI runs read-only; writes are soft-denied by the vendor",
            )
        # CREATE_NEW_PROCESS_GROUP is what makes CTRL_BREAK deliverable to the child on
        # Windows; without it, cancel() would start at terminate().
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
        # would deadlock against it mid-run.
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
            if not isinstance(event, dict):
                continue
            for normalised in self._normalise(event, h):
                yield normalised
            if h.result is not None:
                break
        # `result` is the semantic end, and the process may outlive it — the recorded
        # cancellation run emitted `result` and then lingered. Drain, do not parse.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(h.proc.stdout.read(), timeout=10)
        h.drained = True

    @staticmethod
    def _body(event: dict[str, Any]) -> dict[str, Any]:
        """The undocumented envelope: the payload sits under a key named after the event."""
        kind = event.get("event")
        value = event.get(kind) if isinstance(kind, str) else None
        return value if isinstance(value, dict) else {}

    def _normalise(self, event: dict[str, Any], h: AgentHandle) -> list[AgentEvent]:
        kind = event.get("event")
        body = self._body(event)
        # `conversation_id` rides beside `event` on `init` and inside the body elsewhere.
        conversation = event.get("conversation_id") or body.get("conversation_id")
        if isinstance(conversation, str) and h.session_id is None:
            h.session_id = conversation

        if kind == "init":
            return [AgentEvent(kind=AgentEventKind.STARTED, text=str(body.get("cwd") or ""))]
        if kind == "step_update":
            return self._normalise_step(body)
        if kind == "result":
            h.result = body
            status = str(body.get("status") or "")
            if status == SUCCESS_STATUS:
                return [
                    AgentEvent(kind=AgentEventKind.FINISHED, text=str(body.get("response") or ""))
                ]
            if status in CANCELLED_STATUSES:
                log.info("delegate.cancelled_by_vendor", task_id=h.task_id, status=status)
            return [
                AgentEvent(
                    kind=AgentEventKind.ERROR,
                    text=str(
                        body.get("error") or body.get("response") or status or "unknown error"
                    ),
                )
            ]
        log.debug("delegate.event_skipped", task_id=h.task_id, vendor_event=kind)
        return []

    @staticmethod
    def _normalise_step(body: dict[str, Any]) -> list[AgentEvent]:
        """One vendor step becomes at most one ORACLE event.

        A tool step appears twice — `ACTIVE` then `DONE` — so only the first is a
        `tool_use`, or the inspector would double-count every call. `ERROR` on a tool
        step is where a soft-denied approval surfaces, and it is the single most useful
        line in a headless run that appeared to do nothing.
        """
        state = str(body.get("state") or "")
        step_type = str(body.get("step_type") or "")
        info = body.get("tool_info")
        info = info if isinstance(info, dict) else {}

        if step_type == "tool":
            tool = str(body.get("tool_name") or info.get("name") or "?")
            if state == "ACTIVE":
                return [AgentEvent(kind=AgentEventKind.TOOL_USE, tool=tool)]
            if state == "ERROR":
                error = info.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                return [
                    AgentEvent(
                        kind=AgentEventKind.ERROR,
                        tool=tool,
                        text=str(message or f"{tool} failed"),
                    )
                ]
            return []
        delta = body.get("text_delta")
        if step_type == "agent_response" and isinstance(delta, str) and delta:
            return [AgentEvent(kind=AgentEventKind.TEXT, text=delta)]
        if state == "ERROR":
            return [AgentEvent(kind=AgentEventKind.ERROR, text=f"{step_type or 'step'} failed")]
        return []

    # -- cancellation --------------------------------------------------------

    async def cancel(self, h: AgentHandle) -> None:
        """Interrupt, then terminate, then kill — each step only if the previous one did
        not land within the grace period.

        Measured 2026-08-24 with a timestamped stream
        (`tests/fixtures/agents/antigravity/cancel-v1.1.19.timing.txt`): the interrupt
        alone suffices. ~110 ms later the vendor emits a terminal `result` —
        `status: ERROR`, `error: "timeout waiting for response"`, zero usage — and the
        child exits 1, leaving nothing in the workspace. So a cancelled run and a genuine
        vendor timeout are *indistinguishable from the stream alone*; only ORACLE's own
        knowledge that it sent the signal tells them apart, which is why cancellation is
        recorded by the caller and never inferred here. The enclosing Job Object remains
        the backstop (ARCHITECTURE.md §3); this is the polite path, not the guarantee."""
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
        """ORACLE's reading of the run: exit code *and* the vendor's own status, because
        this CLI exits 1 on a soft-denied tool call with a perfectly well-formed stream.
        Callable without iterating `events()` first — an unread stdout pipe would block
        the child's exit forever."""
        if not h.drained:
            async for _ in self.events(h):
                pass
        exit_code = await h.proc.wait()
        if h.pump is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(h.pump, timeout=5)
        result = h.result or {}
        status = str(result.get("status") or "")
        structured = result.get("structured_output")
        usage = result.get("usage")
        if isinstance(usage, dict):
            # Tokens are the only cost signal this vendor gives, and AgentResult carries
            # no token fields (they belong to the Phase 8 budget work, not here), so the
            # log is where a spent quota is accounted for today.
            log.info("delegate.usage", adapter=self.id, task_id=h.task_id, **usage)
        duration = result.get("duration_seconds")
        return AgentResult(
            success=exit_code == 0 and status == SUCCESS_STATUS,
            exit_code=exit_code,
            result_text=str(result.get("response") or result.get("error") or ""),
            structured=structured if isinstance(structured, dict) else None,
            # Quota-metered: no dollar figure exists to report, and inventing one would be
            # worse than reporting none.
            cost_usd=None,
            duration_ms=int(duration * 1000) if isinstance(duration, int | float) else None,
            num_turns=result.get("num_turns"),
            session_id=h.session_id,
            stderr_tail=bytes(h.stderr[-STDERR_TAIL_BYTES:]).decode("utf-8", errors="replace"),
        )
