"""The briefing endpoints, and the two guarantees that are easiest to lose.

`GET /api/v1/briefing` must be **idempotent** — a client that polls it, or a person who
glances and walks away, must not consume what they came back for — and it must call **no
model**, because it is the one surface with a three-to-five second budget and the one
where a fabricated summary would be a summary of the owner's own work.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oracle.api.app import create_app
from oracle.config import Settings
from oracle.core import briefing as briefing_mod


@pytest.fixture
def populated(settings: Settings) -> Settings:
    root = settings.projects_root
    (root / "Asterim").mkdir(parents=True)
    (root / "GameRecs").mkdir(parents=True)
    return settings


@pytest.fixture
def client(populated: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(populated)) as c:
        yield c


def test_a_fresh_daemon_briefs_its_own_first_boot(client: TestClient) -> None:
    """A first-ever boot is not a crash: there was no previous run for the absence of a
    shutdown to mean anything about."""
    body = client.get("/api/v1/briefing").json()

    assert body["system"]["restarted_at"] is not None
    assert body["system"]["unclean"] is False
    assert "unexpectedly" not in body["text"]


def test_reading_the_briefing_twice_returns_the_same_thing(client: TestClient) -> None:
    """Rendering does not advance the watermark. This is the property a polling client
    depends on, and the one a person depends on when they look and walk away."""
    first = client.get("/api/v1/briefing").json()
    second = client.get("/api/v1/briefing").json()

    assert first["system"] == second["system"]
    assert first["projects"] == second["projects"]


def test_acknowledging_clears_it(client: TestClient) -> None:
    body = client.get("/api/v1/briefing").json()
    assert not body["empty"]

    ack = client.post("/api/v1/briefing/ack", params={"through_seq": body["through_seq"]})
    assert ack.status_code == 200
    assert ack.json()["acknowledged_through"] == body["through_seq"]

    after = client.get("/api/v1/briefing").json()
    assert after["empty"] is True
    assert after["text"].startswith("Nothing ran")


def test_acknowledging_an_unknown_project_is_a_404(client: TestClient) -> None:
    """Better than silently acknowledging everything, which is what a permissive handler
    would do with an id it could not find."""
    r = client.post("/api/v1/briefing/ack", params={"through_seq": 1, "project_id": "pj_nope"})
    assert r.status_code == 404


def test_acknowledging_one_project_is_scoped_to_it(client: TestClient) -> None:
    created = client.post("/api/v1/projects", params={"name": "Asterim"}).json()
    body = client.get("/api/v1/briefing").json()

    client.post(
        "/api/v1/briefing/ack",
        params={"through_seq": body["through_seq"], "project_id": created["id"]},
    )

    after = client.get("/api/v1/briefing").json()
    # The daemon's own news is not swept up by a per-project dismissal.
    assert after["system"]["restarted_at"] is not None


def test_the_wire_shape_is_stable_when_empty(client: TestClient) -> None:
    body = client.get("/api/v1/briefing").json()
    client.post("/api/v1/briefing/ack", params={"through_seq": body["through_seq"]})

    empty = client.get("/api/v1/briefing").json()
    assert set(empty) == {"through_seq", "since_ts", "empty", "text", "projects", "system"}
    assert empty["projects"] == []
    assert set(empty["system"]) == {"restarted_at", "unclean", "degraded", "errors"}


def test_a_registered_project_with_no_activity_says_nothing(client: TestClient) -> None:
    """A project that has done nothing is not a line. The briefing's value is that it is
    short, so silence about silence is the correct output."""
    client.post("/api/v1/projects", params={"name": "Asterim"})
    body = client.get("/api/v1/briefing").json()
    assert body["projects"] == []


class TestNoModelIsOnThisPath:
    """The briefing is deterministic arithmetic over task rows.

    Checked against the source rather than by mocking a provider, because the claim is
    architectural: what matters is that no future edit can quietly add a summariser to
    the one surface with a 3-5 second budget.
    """

    def test_the_module_imports_no_provider(self) -> None:
        forbidden = {
            "oracle.llm",
            "oracle.llm.ollama",
            "oracle.llm.provider",
            "oracle.llm.structured",
            "oracle.router",
            "oracle.router.pipeline",
        }
        tree = ast.parse(inspect.getsource(briefing_mod))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
        assert not (found & forbidden), f"briefing must not reach a model: {found & forbidden}"

    def test_the_renderer_is_a_pure_function(self) -> None:
        """No `await`, so it cannot do I/O — which is what makes it both fast and
        incapable of inventing anything."""
        source = inspect.getsource(briefing_mod.render)
        tree = ast.parse(source.lstrip())
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Await)]
        assert isinstance(tree.body[0], ast.FunctionDef), "render must not be async"


def test_the_briefing_survives_a_project_whose_root_vanished(
    client: TestClient, populated: Settings
) -> None:
    """One absent path degrades one row, not the surface — the lesson from the dead
    collection root that took the whole RAG watcher down."""
    client.post("/api/v1/projects", params={"name": "GameRecs"})
    (populated.projects_root / "GameRecs").rmdir()

    r = client.get("/api/v1/briefing")
    assert r.status_code == 200


def test_boot_and_shutdown_bracket_the_daemon(populated: Settings) -> None:
    """`system.shutdown` exists so the *next* boot can tell a stop from a crash. Without
    it a silent gap in the log is indistinguishable from an idle night, and ADR-0025's
    named risk — a background service failing invisibly — would be unreportable.
    """
    with TestClient(create_app(populated)) as c:
        assert c.get("/api/v1/briefing").json()["system"]["unclean"] is False

    # A second daemon over the same database, after a clean stop.
    with TestClient(create_app(populated)) as c:
        body = c.get("/api/v1/briefing").json()
        assert body["system"]["restarted_at"] is not None
        assert body["system"]["unclean"] is False, "a clean stop must not read as a crash"


def test_a_crash_is_visible_to_the_next_boot(populated: Settings, tmp_path: Path) -> None:
    """Simulated by writing an event *after* the shutdown one, which is exactly what a
    daemon that died mid-work leaves behind: activity, and then nothing."""
    import asyncio

    from oracle.core.eventlog import EventLog
    from oracle.core.events import Event
    from oracle.storage.db import connect, migrate

    with TestClient(create_app(populated)) as c:
        c.get("/api/v1/briefing")

    async def scribble() -> None:
        conn = await connect(populated.db_path)
        await migrate(conn)
        log = EventLog(conn)
        await log.load_head()
        await log.append(
            Event(type="tool.finished", trace_id="t", actor="tool", payload={"tool": "fs.read"})
        )
        await conn.close()

    asyncio.run(scribble())

    with TestClient(create_app(populated)) as c:
        body = c.get("/api/v1/briefing").json()
        assert body["system"]["unclean"] is True
        assert "stopped unexpectedly" in body["text"]
