"""Task persistence (ORCHESTRATION.md §2 "Storage").

Every state change is written before it is announced. That ordering is the whole point:
a daemon that dies between "the task is running" and "the row says so" leaves a graph
that recovery cannot reason about, and recovery's rules (never auto-restart an
interrupted agent) only work if the row is at least as current as the world.

The table is a projection; the event log remains the source of truth (ADR-0010). So this
module never invents state — it round-trips exactly what the models hold.
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from oracle.orchestration.models import Task, TaskStatus

_COLUMNS = (
    "id",
    "root_id",
    "parent_id",
    "plan_id",
    "kind",
    "status",
    "agent",
    "spec",
    "depends_on",
    "attempt",
    "max_attempts",
    "timeout_s",
    "supersedes",
    "created_at",
    "started_at",
    "finished_at",
    "result",
)


def _row_values(task: Task) -> tuple[Any, ...]:
    return (
        task.id,
        task.root_id,
        task.parent_id,
        task.plan_id,
        str(task.kind),
        str(task.status),
        task.agent,
        task.spec.model_dump_json(),
        json.dumps(list(task.depends_on)),
        task.attempt,
        task.max_attempts,
        task.timeout_s,
        task.supersedes,
        task.created_at,
        task.started_at,
        task.finished_at,
        task.result.model_dump_json() if task.result is not None else None,
    )


def _to_task(row: aiosqlite.Row) -> Task:
    return Task.model_validate(
        {
            "id": row["id"],
            "root_id": row["root_id"],
            "parent_id": row["parent_id"],
            "plan_id": row["plan_id"],
            "kind": row["kind"],
            "status": row["status"],
            "agent": row["agent"],
            "spec": json.loads(row["spec"]),
            "depends_on": tuple(json.loads(row["depends_on"])),
            "attempt": row["attempt"],
            "max_attempts": row["max_attempts"],
            "timeout_s": row["timeout_s"],
            "supersedes": row["supersedes"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "result": json.loads(row["result"]) if row["result"] is not None else None,
        }
    )


class TaskStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def save(self, task: Task) -> None:
        """Insert or replace, in one statement. `INSERT OR REPLACE` rather than an
        UPDATE-then-INSERT dance because a task's identity is its id and there is no
        column the store owns that a caller could stomp."""
        placeholders = ", ".join("?" for _ in _COLUMNS)
        await self._conn.execute(
            # S608 below: the interpolated parts are `_COLUMNS`, a module constant, and
            # a run of `?`. Every value is bound. Writing the column list out twice
            # would be the actual hazard - two lists that can drift apart.
            f"INSERT OR REPLACE INTO tasks ({', '.join(_COLUMNS)}) VALUES ({placeholders})",  # noqa: S608
            _row_values(task),
        )
        await self._conn.commit()

    async def save_all(self, tasks: list[Task]) -> None:
        """One transaction for a whole graph: a half-written graph is not a graph, and
        the scheduler's first act is to write every task it was handed."""
        placeholders = ", ".join("?" for _ in _COLUMNS)
        await self._conn.executemany(
            f"INSERT OR REPLACE INTO tasks ({', '.join(_COLUMNS)}) VALUES ({placeholders})",  # noqa: S608
            [_row_values(task) for task in tasks],
        )
        await self._conn.commit()

    async def load_graph(self, root_id: str) -> list[Task]:
        async with self._conn.execute(
            "SELECT * FROM tasks WHERE root_id = ? ORDER BY created_at, id", (root_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [_to_task(row) for row in rows]

    async def load(self, task_id: str) -> Task | None:
        async with self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cur:
            row = await cur.fetchone()
        return _to_task(row) if row is not None else None

    async def unfinished(self) -> list[Task]:
        """What was in flight when the daemon died. Recovery reads this and — per
        ORCHESTRATION.md §3 — gates rather than resuming: a supervisor that cannot prove
        what a child did while it was dead does not pretend to."""
        terminal = (
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.TIMEOUT,
            TaskStatus.SKIPPED,
            TaskStatus.CANCELLED,
        )
        placeholders = ", ".join("?" for _ in terminal)
        async with self._conn.execute(
            # Same shape: `placeholders` is a run of `?`, the statuses are bound.
            f"SELECT * FROM tasks WHERE status NOT IN ({placeholders}) ORDER BY created_at, id",  # noqa: S608
            tuple(str(status) for status in terminal),
        ) as cur:
            rows = await cur.fetchall()
        return [_to_task(row) for row in rows]
