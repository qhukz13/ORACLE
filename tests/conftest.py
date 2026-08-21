from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from oracle.config import Settings
from oracle.core.eventlog import EventLog
from oracle.core.sessions import SessionStore
from oracle.storage.db import connect, migrate


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Every test gets its own scope.

    Nothing touches D:\\ORACLE, and `llm_enabled=False` keeps the suite hermetic. An
    earlier version of this fixture silently reached out to a live Ollama on localhost,
    which made the whole suite depend on a running daemon — exactly what
    docs/TESTING.md forbids. Accuracy against a real model is measured separately by
    scripts/eval_intent.py.
    """
    return Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        port=0,
        llm_enabled=False,
        projects_root=tmp_path / "projects",
    )


@pytest_asyncio.fixture
async def conn(settings: Settings) -> AsyncIterator[aiosqlite.Connection]:
    settings.ensure_dirs()
    c = await connect(settings.db_path)
    await migrate(c)
    try:
        yield c
    finally:
        await c.close()


@pytest_asyncio.fixture
async def eventlog(conn: aiosqlite.Connection) -> EventLog:
    el = EventLog(conn)
    await el.load_head()
    return el


@pytest_asyncio.fixture
async def sessions(conn: aiosqlite.Connection) -> SessionStore:
    return SessionStore(conn)
