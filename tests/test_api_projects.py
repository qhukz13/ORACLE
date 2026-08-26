"""The projects endpoints, over the real ASGI app (docs/PROJECT_STATE.md, ADR-0024).

Two properties get most of the attention here, because both are easy to lose later:

  * the list endpoint **runs no git**, so a sidebar with twenty projects is not twenty
    subprocesses on a page-load;
  * `name` must be a directory the daemon actually discovered, so a request body can never
    become a filesystem path.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from oracle.api.app import create_app
from oracle.config import Settings


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
