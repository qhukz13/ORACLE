#!/usr/bin/env python
"""Record a real task graph off the event log as a replayable UI fixture.

    uv run python scripts/record_graph_fixture.py --list
    uv run python scripts/record_graph_fixture.py --root tk_fbd19d086933 --name continue-run

`TaskTree`'s tests hand-wrote the `Graph` object they asserted against, and `protocol.ts` records
exactly what that costs:

    dependsOn — Populated from `task.created` since 2026-08-26 — before that the scheduler never
    sent it, so this was always `[]` in the running app while a test that hand-wrote the field
    asserted it rendered. A list is not a graph without it.

A hand-written fixture cannot catch that, because it is a drawing of what somebody believed the
wire carries. **This writes out the wire itself** — the raw `task.*` events in `seq` order, exactly
as `wire()` shaped them for a client — so the test folds them through the real reducer and renders
what a real graph produces. If the scheduler stops sending a field, the fixture stops carrying it
and the test notices.

Deliberately not summarised or reshaped on the way out. The point of a recording is that nobody
edited it.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures/graphs"

#: Everything the UI folds into a graph. `task.*` carries the structure; the approvals are what
#: make a graph's history readable ("why did this never run?"), and a recording that dropped them
#: would describe a machine nobody has to argue with.
GRAPH_TYPES = ("task.created", "task.updated", "task.finished", "task.failed")


def rows(db: sqlite3.Connection, root_id: str) -> list[dict[str, Any]]:
    """Every graph event for one root, in `seq` order, in wire shape."""
    out: list[dict[str, Any]] = []
    for r in db.execute(
        "SELECT seq, ts, type, session_id, turn_id, task_id, trace_id, payload"
        " FROM events WHERE type LIKE 'task.%' ORDER BY seq"
    ):
        try:
            payload = json.loads(r["payload"])
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        # A task belongs to this graph if it says so. `root_id` lives in the payload rather than
        # in a column, so this is a filter and not a join.
        if payload.get("root_id") != root_id and not str(r["task_id"] or "").startswith(root_id):
            continue
        out.append(
            {
                "v": 1,
                "seq": r["seq"],
                "ts": r["ts"],
                "type": r["type"],
                "session_id": r["session_id"],
                "turn_id": r["turn_id"],
                "task_id": r["task_id"],
                "trace_id": r["trace_id"],
                "payload": payload,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="D:/ORACLE/data/oracle.db")
    ap.add_argument("--root", default="", help="root task id to record")
    ap.add_argument("--name", default="", help="fixture file name, without .json")
    ap.add_argument("--list", action="store_true", help="show the graphs available to record")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    path = Path(args.db)
    if not path.exists():
        print(f"no event log at {path}")
        return 2
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    if args.list or not args.root:
        print("graphs in the event log:\n")
        for r in db.execute(
            "SELECT root_id, COUNT(*) n, GROUP_CONCAT(DISTINCT status) statuses"
            " FROM tasks GROUP BY root_id ORDER BY root_id"
        ):
            print(f"  {r['root_id']}  {r['n']:>2} tasks   {r['statuses']}")
        print("\nrecord one with --root <id> --name <fixture-name>")
        return 0

    events = rows(db, args.root)
    if not events:
        print(f"no task events for root {args.root!r}")
        return 1

    statuses = sorted(
        {str(e["payload"].get("status")) for e in events if e["payload"].get("status")}
    )
    name = args.name or args.root
    FIXTURES.mkdir(parents=True, exist_ok=True)
    out = FIXTURES / f"{name}.json"
    out.write_text(
        json.dumps(
            {
                "_": "Recorded from a real run by scripts/record_graph_fixture.py. Do not hand-edit:"
                " the value of this file is that nobody did.",
                "root_id": args.root,
                "recorded_at": events[-1]["ts"],
                "statuses": statuses,
                "events": events,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out.relative_to(ROOT)}  ({len(events)} events, statuses: {', '.join(statuses)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
