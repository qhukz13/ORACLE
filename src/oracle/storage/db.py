"""SQLite access and the migration runner.

Numbered .sql files, applied in order, recorded in `schema_version`. No ORM: the schema
is ours, we query it with SQLite-specific features, and explicit SQL is easier for a
coding agent to reason about than session semantics (ADR-0006).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import aiosqlite

from oracle.logsink import get_logger

log = get_logger(__name__)

MIGRATIONS_DIR: Final[Path] = Path(__file__).parent / "migrations"
_MIGRATION_RE: Final[re.Pattern[str]] = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

_PRAGMAS: Final[tuple[str, ...]] = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
)


async def connect(path: Path) -> aiosqlite.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    for pragma in _PRAGMAS:
        await conn.execute(pragma)
    await conn.commit()
    return conn


def _discover() -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _MIGRATION_RE.match(p.name)
        if not m:
            raise ValueError(f"migration filename does not match NNNN_name.sql: {p.name}")
        found.append((int(m.group(1)), p))
    versions = [v for v, _ in found]
    if versions != sorted(set(versions)):
        raise ValueError(f"duplicate or unordered migration versions: {versions}")
    return found


async def migrate(conn: aiosqlite.Connection) -> int:
    """Apply pending migrations. Returns the resulting schema version."""
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    await conn.commit()

    async with conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_version") as cur:
        row = await cur.fetchone()
    current = int(row["v"]) if row else 0

    for version, path in _discover():
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        await conn.executescript(sql)
        await conn.execute(
            "INSERT INTO schema_version(version, name, applied_at) VALUES (?, ?, datetime('now'))",
            (version, path.name),
        )
        await conn.commit()
        current = version
        log.info("migration.applied", version=version, name=path.name)

    return current
