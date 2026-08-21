"""Event log: sequencing, persistence, and the resume contract.

The resume tests are the load-bearing ones. `since_seq` reconnection is what makes the
mobile client implementable at all (docs/API.md#connect-and-resume), and the failure
mode it guards against — a gap opened during the backlog/live handover — is silent.
"""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from oracle.core.eventlog import EventLog
from oracle.core.events import Event
from oracle.storage.db import connect, migrate


def _ev(n: int) -> Event:
    return Event(type="message.delta", trace_id="tr_test", payload={"n": n})


async def test_seq_is_monotonic_and_gap_free(eventlog: EventLog) -> None:
    seqs = [(await eventlog.append(_ev(i))).seq for i in range(50)]
    assert seqs == list(range(1, 51))
    assert eventlog.last_seq == 50


async def test_seq_never_reused_after_delete(
    eventlog: EventLog, conn: aiosqlite.Connection
) -> None:
    """AUTOINCREMENT, not rowid reuse — the resume protocol depends on this."""
    for i in range(5):
        await eventlog.append(_ev(i))
    await conn.execute("DELETE FROM events WHERE seq >= 3")
    await conn.commit()
    nxt = await eventlog.append(_ev(99))
    assert nxt.seq == 6


async def test_survives_reopen(settings, eventlog: EventLog) -> None:
    for i in range(3):
        await eventlog.append(_ev(i))
    await eventlog._conn.commit()

    conn2 = await connect(settings.db_path)
    await migrate(conn2)
    el2 = EventLog(conn2)
    assert await el2.load_head() == 3
    nxt = await el2.append(_ev(4))
    assert nxt.seq == 4
    await conn2.close()


async def test_stream_replays_backlog_then_live(eventlog: EventLog) -> None:
    for i in range(5):
        await eventlog.append(_ev(i))

    received: list[int] = []

    async def consume() -> None:
        async for ev in eventlog.stream(since_seq=2):
            received.append(ev.seq)
            if len(received) == 5:
                break

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # let the backlog drain
    for i in range(2):
        await eventlog.append(_ev(100 + i))
    await asyncio.wait_for(task, timeout=2)

    # 3,4,5 from backlog then 6,7 live — in order, no gaps, no repeats.
    assert received == [3, 4, 5, 6, 7]


async def test_no_gap_when_appends_race_the_backlog_read(eventlog: EventLog) -> None:
    """The handover bug this design exists to prevent: subscribing *after* reading the
    head would drop anything appended in between."""
    for i in range(20):
        await eventlog.append(_ev(i))

    received: list[int] = []
    done = asyncio.Event()

    async def consume() -> None:
        async for ev in eventlog.stream(since_seq=0):
            received.append(ev.seq)
            if ev.seq == 40:
                done.set()
                break

    task = asyncio.create_task(consume())
    # Append immediately, without yielding long enough for the backlog to finish.
    for i in range(20):
        await eventlog.append(_ev(200 + i))
    await asyncio.wait_for(done.wait(), timeout=3)
    task.cancel()

    assert received == list(range(1, 41)), "gap or duplicate in backlog->live handover"


async def test_two_subscribers_see_identical_streams(eventlog: EventLog) -> None:
    a: list[int] = []
    b: list[int] = []

    async def consume(sink: list[int]) -> None:
        async for ev in eventlog.stream(since_seq=0):
            sink.append(ev.seq)
            if len(sink) == 10:
                break

    ta = asyncio.create_task(consume(a))
    tb = asyncio.create_task(consume(b))
    await asyncio.sleep(0.05)
    for i in range(10):
        await eventlog.append(_ev(i))
    await asyncio.wait_for(asyncio.gather(ta, tb), timeout=3)

    assert a == b == list(range(1, 11))


async def test_slow_subscriber_is_dropped_not_silently_starved(
    conn: aiosqlite.Connection,
) -> None:
    """Overflow closes the subscriber. Shedding a critical event silently would be the
    worse failure (docs/API.md#backpressure)."""
    el = EventLog(conn, queue_size=4)
    await el.load_head()

    async def idle() -> None:
        async for _ in el.stream(since_seq=0):
            await asyncio.sleep(10)  # never drains

    task = asyncio.create_task(idle())
    await asyncio.sleep(0.05)
    assert el.subscriber_count == 1
    for i in range(50):
        await el.append(_ev(i))
    assert el.subscriber_count == 0
    task.cancel()


async def test_payload_is_redacted_before_persistence(eventlog: EventLog) -> None:
    stored = await eventlog.append(
        Event(
            type="message.delta",
            trace_id="tr_test",
            payload={"text": "key is sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF"},
        )
    )
    assert "sk-ant" not in stored.payload["text"]
    assert "[REDACTED:anthropic_key]" in stored.payload["text"]

    rows = await eventlog.read_range(0, stored.seq)
    assert "sk-ant" not in rows[-1].payload["text"]


@pytest.mark.parametrize(
    "etype,critical", [("approval.requested", True), ("system.metrics", False)]
)
def test_critical_classification(etype: str, critical: bool) -> None:
    assert Event(type=etype, trace_id="t").critical is critical
