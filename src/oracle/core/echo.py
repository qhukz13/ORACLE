"""The echo "agent" — a stand-in that carries no intelligence.

P0 deliberately ships zero model integration (docs/current_task.md constraints). This
exists to produce a *realistic* event sequence so the transport, persistence, fan-out
and resume paths are exercised end to end before there is anything to be clever with.

It emits the same shape the real runtime will:
    turn.started -> agent.state -> message.delta* -> message.completed -> turn.finished

Phase 1 replaces the body of `run()` with the real turn pipeline. Nothing else about
the event contract changes — that is the point of building this seam first.
"""

from __future__ import annotations

import asyncio

from oracle.core.eventlog import EventLog
from oracle.core.events import Event, new_id
from oracle.logsink import bind_trace, get_logger

log = get_logger(__name__)

_DELTA_DELAY_S = 0.02


class EchoAgent:
    def __init__(self, eventlog: EventLog) -> None:
        self._log = eventlog

    async def run(self, session_id: str, text: str, trace_id: str | None = None) -> None:
        tid = bind_trace(trace_id)
        turn_id = new_id("t")

        async def emit(etype: str, payload: dict[str, object], actor: str = "agent") -> None:
            await self._log.append(
                Event(
                    type=etype,
                    session_id=session_id,
                    turn_id=turn_id,
                    trace_id=tid,
                    actor=actor,
                    payload=payload,
                )
            )

        await emit("turn.started", {"text": text}, actor="user")
        try:
            for state in ("understanding", "executing"):
                await emit("agent.state", {"state": state})
                await asyncio.sleep(_DELTA_DELAY_S)

            reply = f"echo: {text}"
            for token in _tokenize(reply):
                await emit("message.delta", {"text": token})
                await asyncio.sleep(_DELTA_DELAY_S)

            await emit("message.completed", {"text": reply})
            await emit("agent.state", {"state": "idle"})
            await emit("turn.finished", {"outcome": "completed"})
        except asyncio.CancelledError:
            await emit("turn.finished", {"outcome": "cancelled"})
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("echo.failed")
            await emit("error", {"kind": "execution_failed", "message": str(exc)})
            await emit("turn.finished", {"outcome": "error"})


def _tokenize(text: str) -> list[str]:
    """Word-ish chunks, so the stream looks like real token streaming in the UI."""
    parts = text.split(" ")
    return [p if i == len(parts) - 1 else p + " " for i, p in enumerate(parts)]
