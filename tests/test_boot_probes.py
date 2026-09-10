"""The boot probes themselves — the ones that read this daemon's real subsystems.

`test_boot_health.py` covers the phase's machinery against fake probes. This file covers the
probes, because they are where ARCHITECTURE §8's table actually lives: each one turns a subsystem's
state into the sentence a person reads when something is missing, and a wrong sentence there is a
user deciding whether to keep working on bad information.

They are exercised directly rather than through startup. `Settings.boot_health` is off in the test
suite — the delegation probe runs `claude --version`, and a hermetic run does not spawn the
developer's CLIs — which would otherwise leave every probe untested. Calling `_boot_probes` gives
the coverage back without the process spawn, so the two facts stay compatible: the suite stays
hermetic *and* the strings are pinned. The delegation probe is the one that needs a stand-in, and
it gets one.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from oracle.api.app import _boot_probes, create_app, state_of
from oracle.config import Settings
from oracle.integrations.types import Preflight


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.mark.asyncio
async def test_policy_reports_its_scopes_when_it_loaded(client: TestClient) -> None:
    """The row ARCHITECTURE §8 calls the important one. A healthy policy still says what it
    grants, because "armed, 3 scopes" is how you notice one went missing."""
    probes = _boot_probes(state_of(client.app))
    probe = await probes["policy"]()
    assert probe.ok
    assert "scopes" in probe.detail
    assert probe.lost == "", "a healthy probe must not claim something is lost"


@pytest.mark.asyncio
async def test_a_policy_granting_nothing_is_reported_as_failing_closed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§8: "Policy file unreadable or invalid → fail closed." The engine already does that; the
    probe's job is to make the state *visible*, and to say that denying everything is correct
    rather than broken — otherwise the report reads like an outage to be worked around."""
    st = state_of(client.app)
    monkeypatch.setattr(st.policy.policy, "scopes", ())
    probe = await _boot_probes(st)["policy"]()
    assert not probe.ok
    assert "failing closed, which is correct" in probe.lost
    assert probe.remedy


@pytest.mark.asyncio
async def test_events_counts_the_log_it_is_standing_on(client: TestClient) -> None:
    probe = await _boot_probes(state_of(client.app))["events"]()
    assert probe.ok
    assert "last seq" in probe.detail


@pytest.mark.asyncio
async def test_a_missing_index_names_the_fallback_that_still_works(client: TestClient) -> None:
    """The test suite's data dir has no `knowledge.db`, which is exactly the case §8 describes:
    retrieval is gone, lexical file search is not. A probe that said only "not built" would leave
    the reader unable to tell whether search still works."""
    probe = await _boot_probes(state_of(client.app))["knowledge"]()
    assert not probe.ok
    assert "no index at" in probe.detail
    assert "lexical file search still works" in probe.lost
    assert probe.remedy


@pytest.mark.asyncio
async def test_no_model_names_what_still_answers(client: TestClient) -> None:
    """`llm_enabled=False` in the suite, so this is the Ollama-down row for free — and it is
    P13's acceptance criterion in miniature. The `lost` string has to list what survives, because
    "reasoning is down" alone reads as "ORACLE is down", which is false and is the whole point of
    ADR-0011's banner-not-modal rule."""
    probe = await _boot_probes(state_of(client.app))["reasoning"]()
    assert not probe.ok
    assert "deterministic router still answers" in probe.lost
    assert "search all still work" in probe.lost
    assert probe.remedy


@pytest.mark.asyncio
async def test_an_unavailable_agent_cli_degrades_to_the_handoff_packet(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one probe that spawns a process, given a stand-in so the suite does not.

    §8's row: "Claude/Antigravity unavailable → delegation degrades to the Handoff Packet
    fallback." The adapter already computes this in `preflight()`; the probe must carry its
    `reason` and `remedy` through rather than replacing them with its own words — the adapter
    knows whether the binary is missing or merely unauthenticated, and the probe does not.
    """
    st = state_of(client.app)

    async def unavailable() -> Preflight:
        return Preflight(ok=False, reason="'claude' is not on PATH", remedy="install the CLI")

    monkeypatch.setattr(st.delegations.adapter, "preflight", unavailable)
    probe = await _boot_probes(st)["delegation"]()
    assert not probe.ok
    assert probe.component.startswith("delegation/")
    assert probe.detail == "'claude' is not on PATH"
    assert probe.remedy == "install the CLI", "the adapter's remedy, not the probe's guess"
    assert "Handoff Packet" in probe.lost


@pytest.mark.asyncio
async def test_an_available_agent_cli_reports_its_version(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    st = state_of(client.app)

    async def available() -> Preflight:
        return Preflight(ok=True, version="2.1.251")

    monkeypatch.setattr(st.delegations.adapter, "preflight", available)
    probe = await _boot_probes(st)["delegation"]()
    assert probe.ok
    assert probe.detail == "2.1.251"


def test_status_reports_the_phase_as_unrun_rather_than_healthy(client: TestClient) -> None:
    """`boot_health=False` in the suite, so this is the "not checked yet" state — and the
    assertion that a client cannot mistake it for a clean bill of health. `ok` is `true` here
    purely because `all([])` is, which is why `complete` has to be read first."""
    health = client.get("/api/v1/status").json()["health"]
    assert health["complete"] is False
    assert health["probes"] == []
    assert health["lost"] == []
