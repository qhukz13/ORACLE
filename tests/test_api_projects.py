"""The projects endpoints, over the real ASGI app (docs/PROJECT_STATE.md, ADR-0024).

Two properties get most of the attention here, because both are easy to lose later:

  * the list endpoint **runs no git**, so a sidebar with twenty projects is not twenty
    subprocesses on a page-load;
  * `name` must be a directory the daemon actually discovered, so a request body can never
    become a filesystem path.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oracle.api.app import AppState, _build_state, _continue_project, create_app
from oracle.config import Settings

#: A policy that actually covers the temporary projects tree. Written out rather than
#: reusing `config/policy.yaml`, which names `C:/Projects` — a test that silently
#: depended on the developer's own directories would pass here and nowhere else.
POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
  deny_always:
    - "**/*.env"
tools:
  fs.read: {{ tier: T0, scopes: [projects] }}
"""


@pytest.fixture
def populated(settings: Settings) -> Settings:
    """Two real project directories and one thing that is only a folder.

    `New folder` is not invented for the test — the real projects root on this machine has
    one, next to `docs.zip`, which is exactly why registration is explicit.
    """
    root = settings.projects_root
    (root / "Asterim").mkdir(parents=True)
    (root / "GameRecs").mkdir(parents=True)
    (root / "New folder").mkdir(parents=True)
    return settings


@pytest.fixture
def client(populated: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(populated)) as c:
        yield c


def test_nothing_is_registered_until_someone_registers_it(client: TestClient) -> None:
    """Discovery is a suggestion. Auto-registering every directory would put
    `New folder` in the briefing, and the briefing's whole value is that it is short."""
    body = client.get("/api/v1/projects").json()

    assert body["projects"] == []
    assert set(body["candidates"]) == {"Asterim", "GameRecs", "New folder"}


def test_registering_moves_a_candidate_into_projects(client: TestClient) -> None:
    created = client.post("/api/v1/projects", params={"name": "Asterim"}).json()
    assert created["id"].startswith("pj_")

    body = client.get("/api/v1/projects").json()
    assert [p["name"] for p in body["projects"]] == ["Asterim"]
    assert "Asterim" not in body["candidates"]
    assert "GameRecs" in body["candidates"]


def test_registering_an_undiscovered_name_is_refused(client: TestClient) -> None:
    """The safety rule, not a convenience: a name outside the discovered list would be a
    filesystem path assembled from a request body."""
    assert client.post("/api/v1/projects", params={"name": "Nonexistent"}).status_code == 404


@pytest.mark.parametrize(
    "name",
    ["../Secret", "..\\Secret", "Asterim/../../Secret", "C:\\Windows", "/etc/passwd"],
)
def test_a_traversal_in_the_name_never_reaches_the_filesystem(
    client: TestClient, name: str
) -> None:
    """Rejected by the candidate check before any path is built, which is why the
    traversal shapes are enumerated rather than trusted to `resolve()`."""
    assert client.post("/api/v1/projects", params={"name": name}).status_code == 404


def test_registering_twice_is_idempotent(client: TestClient) -> None:
    first = client.post("/api/v1/projects", params={"name": "Asterim"}).json()
    second = client.post("/api/v1/projects", params={"name": "Asterim"}).json()
    assert first["id"] == second["id"]
    assert len(client.get("/api/v1/projects").json()["projects"]) == 1


def test_the_list_endpoint_reports_no_observed_state(client: TestClient) -> None:
    """Branch and dirty count are absent by design: producing them for every row would
    mean a `git` subprocess per project on every page-load, and caching them would mean
    the sidebar lies the moment someone switches branches."""
    client.post("/api/v1/projects", params={"name": "Asterim"})
    row = client.get("/api/v1/projects").json()["projects"][0]

    assert not ({"branch", "ahead", "behind", "dirty", "clean", "observation"} & set(row))
    assert {"open_tasks", "failed_tasks", "status", "root"} <= set(row)


def test_the_detail_endpoint_reads_observed_state_fresh(client: TestClient) -> None:
    """A plain directory is not a repository. That is a state, not a failure, and it must
    render — `error` is a field on the observation, not an exception out of it."""
    created = client.post("/api/v1/projects", params={"name": "Asterim"}).json()
    body = client.get(f"/api/v1/projects/{created['id']}").json()

    assert body["name"] == "Asterim"
    assert body["observation"]["error"] is not None
    assert body["observation"]["branch"] is None
    # Classification does not need git, so it still answers.
    assert body["observation"]["kinds"] == ["unknown"]


def test_an_unknown_project_id_is_a_404(client: TestClient) -> None:
    assert client.get("/api/v1/projects/pj_nope").status_code == 404


def test_a_deleted_root_renders_as_missing_rather_than_crashing(
    client: TestClient, populated: Settings
) -> None:
    """The lesson from the dead collection root that took the whole RAG watcher down:
    one absent path must degrade one row, not the surface."""
    created = client.post("/api/v1/projects", params={"name": "GameRecs"}).json()
    (populated.projects_root / "GameRecs").rmdir()

    detail = client.get(f"/api/v1/projects/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["observation"]["error"] == "root does not exist"

    listing = client.get("/api/v1/projects")
    assert listing.status_code == 200
    row = listing.json()["projects"][0]
    assert row["name"] == "GameRecs"
    # Corrected live, not at the next boot: existence is observed state.
    assert row["status"] == "missing"


def test_status_still_reports_the_raw_candidate_list(client: TestClient) -> None:
    """`/status` keeps returning directory names, because the pre-router and the intent
    classifier resolve against what is on disk — registration is about what ORACLE
    *tracks*, not about what it is allowed to be asked about."""
    body = client.get("/api/v1/status").json()
    assert set(body["projects"]) == {"Asterim", "GameRecs", "New folder"}


# -- the continue hook, over the real daemon state ------------------------------
#
# `_continue_project` is where the router, the task table, the gate and the planner meet,
# and none of the unit suites reach it. These drive it against a real `AppState` — no
# model involved, because every path that matters here happens before a planner is asked
# anything, and `llm_enabled=False` keeps it that way.


@contextlib.asynccontextmanager
async def _daemon(settings: Settings) -> AsyncIterator[AppState]:
    """A real `AppState`, built in **this** test's event loop.

    `TestClient` runs the app's lifespan on its own portal, so a tool call made from a
    pytest-asyncio test creates the toolhost subprocess on one loop and awaits it on
    another — `got Future attached to a different loop`. These drive the daemon directly
    instead, which is also closer to what they are about: the seam between the router's
    hook and the supervisor, not HTTP.
    """
    st = await _build_state(settings)
    try:
        yield st
    finally:
        await st.host.stop()
        await st.conn.close()


async def _texts(st: AppState) -> str:
    events = await st.eventlog.read_range(0, st.eventlog.last_seq, 500)
    return " ".join(str(e.payload.get("text", "")) for e in events if e.type == "message.completed")


async def _derived(st: AppState) -> list[dict]:
    events = await st.eventlog.read_range(0, st.eventlog.last_seq, 500)
    return [dict(e.payload) for e in events if e.type == "continue.derived"]


async def test_continue_registers_a_project_on_first_use(populated: Settings) -> None:
    """Naming a project in a `continue` is the human act registration requires. It is not
    auto-discovery: `discover_projects()` alone still creates nothing."""
    async with _daemon(populated) as st:
        assert await st.project_store.by_name("Asterim") is None

        await _continue_project(st, "Asterim", None, "trace-1")

        tracked = await st.project_store.by_name("Asterim")
        assert tracked is not None
        assert tracked.name == "Asterim"


async def test_continue_with_nothing_to_do_asks_and_plans_nothing(
    populated: Settings,
) -> None:
    """The load-bearing refusal, end to end. An empty project produces a question and no
    `continue.derived` — because nothing was derived."""
    async with _daemon(populated) as st:
        await _continue_project(st, "Asterim", None, "trace-2")

        assert "won't invent work" in await _texts(st)
        assert await _derived(st) == []


async def test_a_project_that_asks_is_not_marked_touched(populated: Settings) -> None:
    """`last_touched` means "ORACLE did something here". Asking a question is not that,
    and the briefing would be wrong if it were."""
    async with _daemon(populated) as st:
        await _continue_project(st, "Asterim", None, "trace-3")

        tracked = await st.project_store.by_name("Asterim")
        assert tracked is not None and tracked.last_touched is None


async def test_an_unknown_project_never_becomes_a_row(populated: Settings) -> None:
    """The router only hands over names it resolved, but the invariant is held locally
    too: a name from anywhere else would become a filesystem path and then a registry
    row."""
    async with _daemon(populated) as st:
        await _continue_project(st, "../Secret", None, "trace-4")
        await _continue_project(st, "NeverDiscovered", None, "trace-5")

        assert await st.project_store.all(include_archived=True) == []


async def test_a_project_outside_the_policy_scope_yields_no_notes(
    populated: Settings,
) -> None:
    """**Registration grants nothing, proved end to end.**

    This fixture's projects live under `tmp_path` while the loaded policy is the real
    `config/policy.yaml`, whose scopes point elsewhere. So the `TODO.md` exists, ORACLE
    knows the project, and `fs.read` is still denied — which is the whole security
    property of ADR-0024 observed from outside rather than asserted in a unit test.

    It also means the derivation is empty, so ORACLE asks. A denied read is not a reason
    to invent work.
    """
    (populated.projects_root / "Asterim" / "TODO.md").write_text(
        "- port the auth module", encoding="utf-8"
    )
    async with _daemon(populated) as st:
        await _continue_project(st, "Asterim", None, "trace-6")

        assert await _derived(st) == []
        assert "won't invent work" in await _texts(st)


@pytest.fixture
def in_scope(populated: Settings, tmp_path: Path) -> Settings:
    """The same tree, with a policy that actually covers it.

    Written out rather than reusing `config/policy.yaml` because the real one names
    `C:/Projects`, and a test that silently depended on the developer's own directories
    would pass here and nowhere else.
    """
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        POLICY.format(root=populated.projects_root.as_posix()),
        encoding="utf-8",
    )
    return populated.model_copy(update={"policy_path": policy})


async def test_a_repo_task_document_is_derived_and_named(in_scope: Settings) -> None:
    """The secondary source, reaching the event that records provenance.

    Stops before the planner — `llm_enabled=False` — which is exactly the seam worth
    checking: what ORACLE knew, and where it came from, *before* anything was asked of a
    model.
    """
    (in_scope.projects_root / "Asterim" / "TODO.md").write_text(
        "- port the auth module", encoding="utf-8"
    )
    async with _daemon(in_scope) as st:
        await _continue_project(st, "Asterim", None, "trace-7")

        derived = await _derived(st)
        assert len(derived) == 1
        assert derived[0]["project"] == "Asterim"
        assert derived[0]["notes"] == ["TODO.md"]
        assert derived[0]["tainted"] is True
        assert derived[0]["open_tasks"] == 0

        tracked = await st.project_store.by_name("Asterim")
        assert tracked is not None and tracked.last_touched is not None


async def test_a_denied_env_file_is_never_a_note(in_scope: Settings) -> None:
    """`deny_always` outranks the scope, and nothing about the continue path may soften
    it. Belt and braces: `.env` is not in `TASK_DOC_NAMES` either."""
    (in_scope.projects_root / "Asterim" / ".env").write_text("SECRET=1", encoding="utf-8")
    async with _daemon(in_scope) as st:
        await _continue_project(st, "Asterim", None, "trace-8")

        assert await _derived(st) == []
