"""The boot health phase — ROADMAP P13's "boot with Ollama down reaches ONLINE and says which
capability is missing".

Both halves of that sentence are testable and both are tested here, because both have an obvious
wrong implementation that passes a naive check:

- *reaches ONLINE* — a probe that raises, or hangs, must not take the boot with it. The tempting
  implementation awaits `gather()` and lets one bad probe kill the phase, which is strictly worse
  than the outage it was describing.
- *says which capability is missing* — `ok: false` is a shrug. A failing probe has to name what
  stopped working, because ARCHITECTURE §8 already decided what each failure costs and a user who
  cannot see which fallback they are in cannot tell degraded from broken.

The third property has no sentence in the roadmap and is the one most likely to rot: **"not checked
yet" must not render as "healthy"**. They are opposites and a bare `ok` bool makes them identical.
"""

from __future__ import annotations

import asyncio

import pytest

from oracle.core.health import BootHealth, HealthPhase, Probe


async def _ok() -> Probe:
    return Probe(component="fine", ok=True, detail="all good")


async def _bad() -> Probe:
    return Probe(
        component="reasoning",
        ok=False,
        detail="Ollama is not reachable",
        lost="reasoning — the deterministic router still answers",
        remedy="start Ollama",
    )


async def _raises() -> Probe:
    raise RuntimeError("the probe itself is broken")


async def _hangs() -> Probe:
    await asyncio.sleep(3600)
    raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_a_degraded_dependency_still_completes_the_phase() -> None:
    """The headline. Ollama down is a *supported state*, not a boot failure (ADR-0025)."""
    health = await HealthPhase().run({"fine": _ok, "reasoning": _bad})
    assert health.complete
    assert not health.ok
    assert {p.component for p in health.degraded} == {"reasoning"}


@pytest.mark.asyncio
async def test_a_failing_probe_names_the_capability_and_the_remedy() -> None:
    """`ok: false` is not a health report. This is the assertion that stops it becoming one."""
    health = await HealthPhase().run({"reasoning": _bad})
    probe = health.degraded[0]
    assert probe.lost, "a failed probe must say what stopped working"
    assert probe.remedy, "and what to do about it"
    assert "router still answers" in probe.lost
    # The wire form carries the same, because the UI is where this is read.
    assert health.wire()["lost"] == [probe.lost]


@pytest.mark.asyncio
async def test_a_probe_that_raises_does_not_take_the_boot_with_it() -> None:
    """One bad probe means one bad probe, never "no health report at all"."""
    health = await HealthPhase().run({"broken": _raises, "fine": _ok})
    assert health.complete
    assert {p.component for p in health.probes} == {"broken", "fine"}
    broken = next(p for p in health.probes if p.component == "broken")
    assert not broken.ok
    assert "RuntimeError" in broken.detail
    assert "bug in the probe" in broken.remedy, "do not blame the component for a probe bug"


@pytest.mark.asyncio
async def test_a_hung_probe_is_unknown_rather_than_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "I could not tell" and "it is down" are different facts with different remedies, and a
    health phase that collapses them invents outages. It still counts as degraded — a hung
    dependency is exactly as actionable as a dead one."""
    monkeypatch.setattr("oracle.core.health.PROBE_TIMEOUT_S", 0.05)
    health = await HealthPhase().run({"slow": _hangs, "fine": _ok})
    slow = next(p for p in health.probes if p.component == "slow")
    assert slow.unknown
    assert not slow.ok
    assert "did not answer" in slow.detail
    assert slow in health.degraded


@pytest.mark.asyncio
async def test_probes_run_concurrently_so_the_phase_is_not_their_sum() -> None:
    """P13 budgets ~400 ms to a usable window. Three 100 ms probes must cost ~100 ms, not 300 —
    otherwise adding a probe is a boot regression and nobody will add one."""

    async def slow() -> Probe:
        await asyncio.sleep(0.1)
        return Probe(component="s", ok=True)

    health = await HealthPhase().run({"a": slow, "b": slow, "c": slow})
    assert health.elapsed_ms < 250, f"probes appear to be serial: {health.elapsed_ms} ms"


def test_unchecked_is_not_the_same_as_healthy() -> None:
    """The property with no roadmap sentence and the longest reach. A fresh phase has no probes,
    so `all()` is vacuously true and `ok` reads True — which is correct and useless on its own.
    `complete` is what stops a client rendering "not checked yet" as a clean bill of health."""
    fresh = BootHealth()
    assert fresh.ok, "vacuous truth, and precisely why `ok` alone cannot be trusted"
    assert not fresh.complete
    assert fresh.wire()["complete"] is False
    assert HealthPhase().latest.complete is False


@pytest.mark.asyncio
async def test_the_result_is_readable_while_the_phase_is_still_running() -> None:
    """The claim that keeps boot under budget: `/api/v1/status` reads `health.latest` without
    waiting for the phase, so a slow probe delays the *report* and never the daemon.

    Asserted here rather than against a live daemon on purpose — the phase measured 376 ms in
    the running system, which is shorter than its own startup latency, so the `complete: false`
    window is real but too short to catch by polling. A property you can only observe when
    something is slow is exactly the kind that rots.
    """
    phase = HealthPhase()
    gate = asyncio.Event()

    async def waits() -> Probe:
        await gate.wait()
        return Probe(component="slow", ok=True)

    task = asyncio.create_task(phase.run({"slow": waits}))
    await asyncio.sleep(0)  # let the phase start
    assert phase.latest.complete is False, "a running phase must not read as a finished one"
    assert phase.latest.probes == ()
    gate.set()
    await task
    assert phase.latest.complete is True
    assert len(phase.latest.probes) == 1


@pytest.mark.asyncio
async def test_every_probe_is_timed_so_a_slow_boot_can_be_attributed() -> None:
    """A boot that got slower is a question about *which* probe, and the answer has to be in the
    report — otherwise the next person bisects it by hand."""
    health = await HealthPhase().run({"fine": _ok, "reasoning": _bad})
    assert all(p.elapsed_ms >= 0 for p in health.probes)
    assert all("elapsed_ms" in p.wire() for p in health.probes)
