"""Session lifecycle."""

from __future__ import annotations

import aiosqlite

from oracle.core.events import new_id, now_iso


class SessionStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def create(self, origin: str = "desktop", title: str | None = None) -> str:
        sid = new_id("s")
        ts = now_iso()
        await self._conn.execute(
            "INSERT INTO sessions(id, created_at, last_active_at, title, project_id, origin)"
            " VALUES (?,?,?,?,?,?)",
            (sid, ts, ts, title, None, origin),
        )
        await self._conn.commit()
        return sid

    async def exists(self, session_id: str) -> bool:
        async with self._conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)) as cur:
            return await cur.fetchone() is not None

    async def touch(self, session_id: str) -> None:
        await self._conn.execute(
            "UPDATE sessions SET last_active_at = ? WHERE id = ?", (now_iso(), session_id)
        )
        await self._conn.commit()

    async def list(self, limit: int = 50) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        async with self._conn.execute(
            "SELECT * FROM sessions ORDER BY last_active_at DESC LIMIT ?", (limit,)
        ) as cur:
            async for row in cur:
                out.append(dict(row))
        return out
