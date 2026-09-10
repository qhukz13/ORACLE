"""Every event type ORACLE emits must be in `KNOWN_TYPES`.

`core/events.py` says of that set: *"Not enforced on ingest (forward compatibility), but used to
catch typos in our own code via `is_known`."* **`is_known` has never had a caller.** So the set was
a list somebody maintained by remembering to, and on 2026-09-11 a parity check found nine emitted
types missing from it — `system.health`, added earlier that same day, plus five `memory.*`,
`plan.descended`, `plan.rejected` and `graph.replan_exhausted`.

Nothing broke, because nothing reads the set at runtime either. That is the shape of the defect:
**a control that was never wired up degrades silently into a comment.** The registry's real jobs
are downstream — `CRITICAL_TYPES` is a subset of it and decides what survives backpressure, and a
type absent from both is one nobody has decided about.

Enforcing at ingest would be wrong, and the existing comment already explains why: a client may
legitimately send a type this build has not heard of, and rejecting it would trade forward
compatibility for tidiness. So the check lives here, where it costs a test run and cannot be
forgotten.

**What this deliberately does not check.** `docs/API.md` documents events in a table *and* in prose
sections, so a naive extraction disagrees with the registry in both directions for reasons that are
formatting rather than drift. A checker that cries wolf is worse than none — the doc-link checker
reported 237 false breakages before its slug rule was fixed — so API.md parity is left unpinned
rather than pinned badly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from oracle.core.events import CRITICAL_TYPES, KNOWN_TYPES, is_known

SRC = Path(__file__).resolve().parent.parent / "src" / "oracle"

#: How an event type reaches the log. `Event(type="…")` is the direct form; `_emit("…", …)` is the
#: helper the memory store, the delegation service and the scheduler use. Both are matched because
#: only matching one is how five `memory.*` types stayed invisible.
EMIT_PATTERNS = (
    re.compile(r'type=\s*"([a-z][a-z_]*\.[a-z_.]+)"'),
    re.compile(r'_emit\(\s*"([a-z][a-z_]*\.[a-z_.]+)"'),
)

#: Types that look like events and are not. Structlog uses dotted names for message keys, so a
#: `log.warning("memory.remember_refused", …)` is prose, not protocol. Nothing here today —
#: the patterns above are anchored on `type=` and `_emit(`, which structlog calls never use — and
#: the entry exists so an exemption has to be named rather than silently widened.
NOT_EVENTS: frozenset[str] = frozenset()


def emitted_types() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in EMIT_PATTERNS:
            found.update(pattern.findall(text))
    return found - NOT_EVENTS


def test_the_scan_finds_events_at_all() -> None:
    """A regex that matches nothing passes every assertion below by finding no work."""
    found = emitted_types()
    assert len(found) > 20, f"only {len(found)} emitted types found — the scan is broken"
    assert "task.created" in found, "the helper form is not being matched"
    assert "system.boot" in found, "the direct form is not being matched"


def test_every_emitted_type_is_registered() -> None:
    """The one that would have caught all nine."""
    unregistered = sorted(emitted_types() - KNOWN_TYPES)
    assert not unregistered, (
        f"emitted but absent from KNOWN_TYPES: {unregistered}. Add them to core/events.py — to "
        f"CRITICAL_TYPES if losing one under backpressure would break a live decision that "
        f"replaying from since_seq cannot repair, and to the coalescable half otherwise."
    )


def test_critical_types_are_a_subset_of_known() -> None:
    """`KNOWN_TYPES` is built as `CRITICAL_TYPES | {…}`, so this holds by construction today.
    It is asserted because that construction is an implementation detail one refactor away from
    two independent lists, and two lists drift."""
    assert CRITICAL_TYPES <= KNOWN_TYPES


def test_is_known_answers_for_both_halves_and_rejects_a_stranger() -> None:
    """The function has no production caller. Pinning its behaviour keeps it honest for the day
    something does call it, rather than leaving dead code that has quietly stopped working."""
    assert is_known("system.boot")
    assert is_known("system.health")
    assert is_known("term.output")
    assert not is_known("system.definitely_not_a_real_event")


@pytest.mark.parametrize("event_type", sorted(CRITICAL_TYPES))
def test_a_critical_type_is_emitted_by_something(event_type: str) -> None:
    """The reverse direction: a critical type nothing emits is a promise to a client that no
    code keeps. `turn.started`/`turn.finished` are emitted through the turn pipeline's own
    helper, so this also proves the scan reaches beyond the two obvious call shapes."""
    if event_type in emitted_types():
        return
    text = " ".join(p.read_text(encoding="utf-8", errors="replace") for p in SRC.rglob("*.py"))
    assert f'"{event_type}"' in text, (
        f"{event_type} is CRITICAL — never dropped under backpressure — but nothing in "
        f"src/oracle mentions it. Either it is emitted from somewhere the scan cannot see, "
        f"or it is a type the product no longer has."
    )
