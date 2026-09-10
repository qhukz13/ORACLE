"""The boot health phase — [ROADMAP P13](../../../docs/ROADMAP.md), [ARCHITECTURE §8].

> Boot with Ollama down reaches ONLINE and says which capability is missing.

That sentence is the whole specification, and both halves of it are load-bearing.

**Reaches ONLINE.** A probe never fails the boot. [ADR-0025](../../../docs/DECISIONS.md) makes
ORACLE a resident service, and a service that refuses to start because one dependency is down is a
service that is down. Everything here is advisory: it reports, it does not gate. The single
exception is policy, and it is not an exception to *starting* — a broken policy file already fails
closed inside the engine, and this probe exists to make that state visible rather than to cause it.

**Says which capability is missing.** `ok: false` is not a health report, it is a shrug. Every probe
that fails must name the capability that is gone and what to do about it, because
[ARCHITECTURE §8](../../../docs/ARCHITECTURE.md#8-degradation--what-happens-when-a-piece-is-missing)
already decided what each failure *costs* — retrieval falls back to lexical search, delegation falls
back to a handoff packet, reasoning falls back to the deterministic router — and a user who cannot
see which fallback they are in cannot tell a degraded ORACLE from a broken one.

**Why this does not block startup.** P13 budgets ≤400 ms to a usable window, and probing an external
CLI costs seconds — `claude --version` spawns a process. So `run()` is meant to be spawned, not
awaited, and `/api/v1/status` reports `probing` until it lands. Boot that waits for the slowest
thing on the machine is exactly the eager start ADR-0025 rejected.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: A single probe's budget. `claude --version` spawns a process and the adapter's own preflight
#: allows 15 s, which is right for a delegation about to cost minutes and far too long for a boot
#: report. A probe that has not answered in this long is reported as unknown, not as broken:
#: "I could not tell" and "it is down" are different facts and the remedy differs.
PROBE_TIMEOUT_S = 4.0

#: The whole phase's budget. Probes run concurrently, so this bounds the slowest one plus overhead
#: rather than their sum.
PHASE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Probe:
    """One subsystem, and — when it is missing — what stops working because of it."""

    component: str
    ok: bool
    #: What is true, stated either way. A healthy probe that says nothing teaches nothing:
    #: "bge-m3, 17,439 chunks" is how you notice the index is a tenth of the size it was.
    detail: str = ""
    #: The capability that is unavailable. Required when `ok` is false, and the reason this
    #: type exists rather than a bare bool — see the module docstring.
    lost: str = ""
    remedy: str = ""
    #: True when the probe could not answer in time. Distinct from `ok=False`: an unanswered
    #: probe is a fact about the probe, and reporting it as a failure would invent an outage.
    unknown: bool = False
    elapsed_ms: int = 0

    def wire(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "ok": self.ok,
            "detail": self.detail,
            "lost": self.lost,
            "remedy": self.remedy,
            "unknown": self.unknown,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class BootHealth:
    """The phase's result. `ok` means every probe passed, which is not the same as usable."""

    probes: tuple[Probe, ...] = ()
    elapsed_ms: int = 0
    #: False until `run()` has finished, so a client can tell "everything is fine" from
    #: "nothing has been checked yet". These are the two states a naive health endpoint
    #: renders identically, and they are opposites.
    complete: bool = False

    @property
    def ok(self) -> bool:
        return all(p.ok for p in self.probes)

    @property
    def degraded(self) -> tuple[Probe, ...]:
        """The probes worth putting in front of a person, unknowns included — a probe that
        timed out is exactly as actionable as one that failed, and hiding it would make a
        hung dependency look like a healthy one."""
        return tuple(p for p in self.probes if not p.ok or p.unknown)

    def wire(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "ok": self.ok,
            "elapsed_ms": self.elapsed_ms,
            "probes": [p.wire() for p in self.probes],
            "lost": [p.lost for p in self.degraded if p.lost],
        }


ProbeFn = Callable[[], Awaitable[Probe]]


async def _timed(component: str, fn: ProbeFn) -> Probe:
    """Run one probe under its own budget, and never let it raise.

    A probe that throws must not take the phase with it. This is the boot path of a resident
    service: the failure mode to design against is "one bad probe means no health report at all",
    which is strictly worse than the outage it was trying to describe.
    """
    t0 = time.perf_counter()
    try:
        probe = await asyncio.wait_for(fn(), timeout=PROBE_TIMEOUT_S)
    except TimeoutError:
        return Probe(
            component=component,
            ok=False,
            unknown=True,
            detail=f"did not answer within {PROBE_TIMEOUT_S:.0f}s",
            lost=f"unknown — {component} could not be checked",
            remedy="check it by hand; a hung probe usually means a hung dependency",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
    except Exception as exc:  # a probe may not be fatal — see this function's docstring
        log.warning("health.probe_failed", component=component, error=str(exc))
        return Probe(
            component=component,
            ok=False,
            detail=f"probe raised {type(exc).__name__}: {exc}",
            lost=f"unknown — {component} could not be checked",
            remedy="this is a bug in the probe, not necessarily in the component",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
    object.__setattr__(probe, "elapsed_ms", int((time.perf_counter() - t0) * 1000))
    return probe


@dataclass
class HealthPhase:
    """Holds the latest result so `/api/v1/status` can read it without re-probing.

    Mutable on purpose and deliberately not a cache with an expiry: this reports what was true at
    boot. A live re-probe on every status call would turn a cheap endpoint into a process spawn,
    and the question this answers — *what did ORACLE come up with?* — does not change per request.
    """

    latest: BootHealth = field(default_factory=BootHealth)

    async def run(self, probes: dict[str, ProbeFn]) -> BootHealth:
        t0 = time.perf_counter()
        names = list(probes)
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(_timed(n, probes[n]) for n in names)),
                timeout=PHASE_TIMEOUT_S,
            )
        except TimeoutError:
            # Every probe has its own shorter budget, so reaching this means something broke
            # the arithmetic rather than one dependency being slow. Report it as such.
            log.warning("health.phase_timeout", budget_s=PHASE_TIMEOUT_S)
            results = [
                Probe(
                    component=n,
                    ok=False,
                    unknown=True,
                    detail="the health phase itself timed out",
                    lost=f"unknown — {n} could not be checked",
                )
                for n in names
            ]
        self.latest = BootHealth(
            probes=tuple(results),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            complete=True,
        )
        for probe in self.latest.degraded:
            log.warning(
                "health.degraded",
                component=probe.component,
                detail=probe.detail,
                lost=probe.lost,
            )
        return self.latest
