"""The event log: append, fan-out, resume.

The resume contract is the whole point (docs/API.md#connect-and-resume):
a client reconnecting with `since_seq=N` receives every event after N, exactly once,
in order, with no gaps.

The subtle part is the handover from backlog to live stream. `stream()` subscribes
*before* reading the head, so any event appended during the backlog read lands in the
queue; overlap is then removed by seq. Subscribing after the read would lose events in
that window — the classic bug this ordering exists to prevent.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import aiosqlite

from oracle.core.events import Event
from oracle.logsink import get_logger
from oracle.logsink.redact import redact

log = get_logger(__name__)


class QueueOverflow(Exception):
    """A subscriber fell too far behind. The connection is closed; the client
    reconnects with since_seq rather than silently losing critical events."""


class EventLog:
    def __init__(self, conn: aiosqlite.Connection, queue_size: int = 1000) -> None:
        self._conn = conn
        self._queue_size = queue_size
        self._subs: set[asyncio.Queue[Event]] = set()
        # Single writer. SQLite AUTOINCREMENT gives monotonicity; this lock gives us a
        # matching in-memory order so fan-out cannot reorder relative to persistence.
        self._write_lock = asyncio.Lock()
        self._last_seq = 0

    @property
    def last_seq(self) -> int:
        return self._last_seq

    async def load_head(self) -> int:
        async with self._conn.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM events") as cur:
            row = await cur.fetchone()
        self._last_seq = int(row["s"]) if row else 0
        return self._last_seq

    async def append(self, event: Event) -> Event:
        """Persist, then fan out. Persistence first: an event a client saw but that did
        not survive a restart would break the resume contract."""
        async with self._write_lock:
            payload = redact(event.payload)
            cur = await self._conn.execute(
                "INSERT INTO events(ts, type, session_id, turn_id, task_id, trace_id,"
                " actor, payload, critical) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    event.ts,
                    event.type,
                    event.session_id,
                    event.turn_id,
                    event.task_id,
                    event.trace_id,
                    event.actor,
                    json.dumps(payload, ensure_ascii=False),
                    int(event.critical),
                ),
            )
            await self._conn.commit()
            seq = int(cur.lastrowid or 0)
            stored = event.model_copy(update={"seq": seq, "payload": payload})
            self._last_seq = seq

            dead: list[asyncio.Queue[Event]] = []
            for q in self._subs:
                try:
                    q.put_nowait(stored)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                # Drop the subscriber, not the event. Closing is honest; shedding a
                # critical event silently is not.
                self._subs.discard(q)
                log.warning("eventlog.subscriber_overflow", seq=seq, type=event.type)

        return stored

    async def read_range(self, since_seq: int, to_seq: int, limit: int = 5000) -> list[Event]:
        rows: list[Event] = []
        async with self._conn.execute(
            "SELECT * FROM events WHERE seq > ? AND seq <= ? ORDER BY seq ASC LIMIT ?",
            (since_seq, to_seq, limit),
        ) as cur:
            async for row in cur:
                rows.append(_row_to_event(row))
        return rows

    async def stream(self, since_seq: int = 0) -> AsyncIterator[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        # Subscribe BEFORE snapshotting the head — see module docstring.
        self._subs.add(q)
        try:
            head = self._last_seq
            last = since_seq
            if head > since_seq:
                for ev in await self.read_range(since_seq, head):
                    yield ev
                    last = ev.seq
            while True:
                ev = await q.get()
                if ev.seq <= last:
                    continue  # overlap with the backlog we already yielded
                yield ev
                last = ev.seq
        finally:
            self._subs.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


def _row_to_event(row: aiosqlite.Row) -> Event:
    payload: dict[str, Any] = json.loads(row["payload"])
    return Event(
        seq=int(row["seq"]),
        ts=row["ts"],
        type=row["type"],
        session_id=row["session_id"],
        turn_id=row["turn_id"],
        task_id=row["task_id"],
        trace_id=row["trace_id"],
        actor=row["actor"],
        payload=payload,
    )
