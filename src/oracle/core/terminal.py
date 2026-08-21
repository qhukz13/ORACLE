"""The terminal bridge: PTY output onto the event stream.

The PTY itself lives in the toolhost child, where it belongs — a runaway `npm install`
must die with HALT. This is the parent-side half that makes it visible: it polls each
open session through the ordinary tool path and republishes what comes back as
`term.output` events, so every attached client (and later the phone) sees the same
stream from the same event log.

**Polling, not a push channel.** The child answers invocations; it does not speak
unprompted, and giving it a second way to talk would mean the parent's frame reader had
to tell a reply from an announcement — a protocol seam right where correctness matters.
Polling costs one ~28 ms round trip per open session per interval and reuses the gate,
the audit log and the timeout handling exactly as they are. A terminal is a human-speed
surface; 120 ms of latency is not the thing anyone will notice about it.

**Who is typing is tracked, because it is a trust feature** (docs/UI.md#5): output
carries the session it came from, and input arrives either as `term.input` (the person)
or `term.write` (the agent, confirmed every time). Those are different tools on purpose.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

from oracle.core.eventlog import EventLog
from oracle.core.events import Event
from oracle.logsink import bind_trace, get_logger
from oracle.tools.executor import ToolExecutor

log = get_logger(__name__)

#: How often an open session is drained. Slow enough to be cheap, fast enough that a
#: build log does not feel like it is arriving in slabs.
POLL_INTERVAL_S = 0.12
#: Give up on a session that has produced nothing and is no longer alive.
MAX_SESSIONS = 8


@dataclass
class Attached:
    session_id: str
    cwd: str
    task: asyncio.Task[None] | None = None
    alive: bool = True
    produced: int = 0
    dropped: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


class TerminalBridge:
    """Owns the poll loops. One per open session, cancelled when it closes."""

    def __init__(self, eventlog: EventLog, executor: ToolExecutor) -> None:
        self._log = eventlog
        self._executor = executor
        self._sessions: dict[str, Attached] = {}

    # ------------------------------------------------------------------ lifecycle

    async def open(self, path: str, *, session_id: str | None = None) -> dict[str, Any]:
        if len([a for a in self._sessions.values() if a.alive]) >= MAX_SESSIONS:
            return {"error": f"{MAX_SESSIONS} terminals are already open"}

        outcome = await self._executor.execute("term.open", {"path": path})
        if not outcome.ok or outcome.result is None:
            message = outcome.error.message if outcome.error else "could not open a terminal"
            log.warning("term.open_failed", path=path, error=message)
            return {"error": message}

        result = outcome.result.model_dump(mode="json")
        term_id = str(result["session_id"])
        attached = Attached(session_id=term_id, cwd=str(result.get("cwd", path)), meta=result)
        self._sessions[term_id] = attached
        attached.task = asyncio.create_task(self._pump(attached, session_id))

        await self._emit(
            "term.opened",
            session_id,
            {
                "pty_id": term_id,
                "cwd": attached.cwd,
                "shell": result.get("shell"),
                "banner": result.get("banner", ""),
            },
        )
        return result

    async def close(self, term_id: str, *, session_id: str | None = None) -> dict[str, Any]:
        attached = self._sessions.pop(term_id, None)
        if attached is None:
            return {"error": f"no terminal {term_id}"}
        attached.alive = False
        if attached.task is not None:
            attached.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await attached.task
        outcome = await self._executor.execute("term.close", {"session_id": term_id})
        await self._emit("term.closed", session_id, {"pty_id": term_id})
        return outcome.result.model_dump(mode="json") if outcome.result else {}

    async def stop(self) -> None:
        """Shutdown. The PTYs die with the toolhost regardless; this just stops polling."""
        for attached in list(self._sessions.values()):
            attached.alive = False
            if attached.task is not None:
                attached.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await attached.task
        self._sessions.clear()

    # --------------------------------------------------------------------- input

    async def input(self, term_id: str, text: str) -> dict[str, Any]:
        """The human's keystrokes. Not `term.write` — see `tools/terminal.py`."""
        if term_id not in self._sessions:
            return {"error": f"no terminal {term_id}"}
        outcome = await self._executor.execute("term.input", {"session_id": term_id, "text": text})
        if not outcome.ok:
            return {"error": outcome.error.message if outcome.error else "input failed"}
        return outcome.result.model_dump(mode="json") if outcome.result else {}

    async def resize(self, term_id: str, cols: int, rows: int) -> dict[str, Any]:
        if term_id not in self._sessions:
            return {"error": f"no terminal {term_id}"}
        outcome = await self._executor.execute(
            "term.resize", {"session_id": term_id, "cols": cols, "rows": rows}
        )
        return outcome.result.model_dump(mode="json") if outcome.result else {}

    # ---------------------------------------------------------------------- poll

    async def _pump(self, attached: Attached, session_id: str | None) -> None:
        """Drain one session until it dies or is closed."""
        trace = bind_trace()
        try:
            while attached.alive:
                await asyncio.sleep(POLL_INTERVAL_S)
                outcome = await self._executor.execute(
                    "term.read", {"session_id": attached.session_id, "raw": True}
                )
                if not outcome.ok or outcome.result is None:
                    # The session is gone, or the toolhost restarted and took it with
                    # it. Either way there is nothing left to poll.
                    attached.alive = False
                    await self._emit(
                        "term.closed",
                        session_id,
                        {
                            "pty_id": attached.session_id,
                            "reason": outcome.error.message if outcome.error else "gone",
                        },
                        trace,
                    )
                    break

                result = outcome.result.model_dump(mode="json")
                text = str(result.get("text", ""))
                attached.produced = int(result.get("produced", attached.produced))
                dropped = int(result.get("dropped", 0))

                if text:
                    await self._emit(
                        "term.output",
                        session_id,
                        {
                            "pty_id": attached.session_id,
                            "stream": "stdout",
                            "data": text,
                            # Surfaced, never swallowed: if scrollback was trimmed the
                            # UI has to say so rather than let the reader believe they
                            # saw everything (see tools/terminal.py).
                            "dropped": dropped,
                            "more": bool(result.get("truncated")),
                        },
                        trace,
                    )
                attached.dropped = dropped

                if not result.get("alive", True):
                    attached.alive = False
                    await self._emit(
                        "term.closed",
                        session_id,
                        {"pty_id": attached.session_id, "reason": "the shell exited"},
                        trace,
                    )
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - a poll loop must not take the app down
            log.exception("term.pump_failed", session=attached.session_id, error=str(exc))
            attached.alive = False

    async def _emit(
        self,
        etype: str,
        session_id: str | None,
        payload: dict[str, Any],
        trace: str | None = None,
    ) -> None:
        await self._log.append(
            Event(
                type=etype,
                session_id=session_id,
                trace_id=trace or bind_trace(),
                actor="system",
                payload=payload,
            )
        )

    # --------------------------------------------------------------------- views

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "pty_id": a.session_id,
                "cwd": a.cwd,
                "alive": a.alive,
                "produced": a.produced,
                "dropped": a.dropped,
            }
            for a in self._sessions.values()
        ]


__all__ = ["MAX_SESSIONS", "POLL_INTERVAL_S", "Attached", "TerminalBridge"]
