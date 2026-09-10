"""A tool's documented risk tier must be the tier it ships with.

`TOOLS.md`'s table is where a reader learns what ORACLE may do and what it will ask before doing.
`Tier` in the registry is what the policy gate actually enforces. If those disagree, the document
describes a safer product than the one running — and a reader auditing the design would come away
reassured by a number nothing honours.

**This found nothing when it was written on 2026-09-11: 33 shipped tools, 48 documented rows, zero
tier mismatches.** That is the point of adding it. A parity test written after a divergence only
proves somebody noticed; written while the property holds, it is what keeps it holding — and tier
is the one field here where drift is silent by construction, because both sides look plausible in
isolation and no runtime check compares them.

**The asymmetry is deliberate.** A documented tool that does not ship is normal in a design-first
repo — `TOOLS.md` describes the full intended surface, and 17 of its rows are for later phases. A
*shipped* tool with no documented tier is the direction that matters, and it is asserted separately
and narrowly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from oracle.tools import build_registry

TOOLS_MD = Path(__file__).resolve().parent.parent.parent / "docs" / "TOOLS.md"

#: `| `term.write` | **T2, always confirmed** | …` — the tier cell may be bold and qualified.
#: Matching a bare `T2` in a clean cell missed that row and reported a false gap; the lesson is
#: the same one the doc-link checker taught with 237 of them, so the pattern is deliberately loose
#: and the tests below prove it matches both shapes.
ROW = re.compile(r"^\|\s*`([a-z][a-z_]*\.[a-z_.]+)`\s*\|[^|]*?\b(T[0-3])\b", re.M)

#: Shipped tools with no tier row in TOOLS.md, each with the reason it is acceptable.
#: An exemption must be *named*; the failure message says so, because a set that can be widened
#: silently is not an exemption list, it is a suppression.
UNDOCUMENTED_BY_DESIGN = {
    "fs.stat": "T0 metadata read with no side effects; the fs family's row covers the shape",
    "term.input": "hidden internal — the HUMAN's keystrokes, not the agent's. Unreachable by the "
    "model, and test_executor.py::test_hidden_tools_are_unreachable_from_the_model enforces that",
    "term.resize": "hidden internal — changes PTY dimensions and executes nothing",
}


def documented_tiers() -> dict[str, str]:
    doc = TOOLS_MD.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for tool, tier in ROW.findall(doc):
        out.setdefault(tool, tier)  # first row wins; later mentions are prose
    return out


def shipped_tiers() -> dict[str, str]:
    return {c.id: c.risk.label for c in build_registry().all()}


def test_the_table_is_being_parsed_at_all() -> None:
    """A regex that matches nothing makes every assertion below vacuous."""
    found = documented_tiers()
    assert len(found) > 30, f"only {len(found)} tier rows parsed from TOOLS.md"
    assert found.get("term.write") == "T2", (
        "the qualified tier cell '**T2, always confirmed**' is not being matched — "
        "this is the exact false gap that motivated the loose pattern"
    )
    assert found.get("fs.read") is not None, "a plain tier cell is not being matched"


@pytest.mark.parametrize("tool_id", sorted(shipped_tiers()))
def test_a_shipped_tool_ships_at_its_documented_tier(tool_id: str) -> None:
    """The assertion with teeth. A tool that ships one tier lower than TOOLS.md says is a
    confirmation the reader was promised and will not get."""
    documented = documented_tiers().get(tool_id)
    if documented is None:
        pytest.skip("no tier row; covered by test_every_shipped_tool_is_documented_or_exempt")
    assert documented == shipped_tiers()[tool_id], (
        f"TOOLS.md documents {tool_id} as {documented}; it ships as {shipped_tiers()[tool_id]}. "
        f"If the tier moved deliberately, move the document with it — a reader auditing the "
        f"design must not be reassured by a number nothing enforces."
    )


def test_every_shipped_tool_is_documented_or_exempt() -> None:
    """The direction that matters. The reverse — documented but unshipped — is normal here and
    is not asserted: TOOLS.md describes the intended surface, not just the built one."""
    undocumented = set(shipped_tiers()) - set(documented_tiers()) - set(UNDOCUMENTED_BY_DESIGN)
    assert not undocumented, (
        f"shipped with no tier row in TOOLS.md: {sorted(undocumented)}. Add a row, or add the "
        f"tool to UNDOCUMENTED_BY_DESIGN with the reason — an exemption nobody has to justify "
        f"is a suppression."
    )


def test_the_exemptions_still_describe_shipped_tools() -> None:
    """An exemption for a tool that no longer exists is a stale suppression that would hide the
    next real gap under the same name."""
    stale = sorted(set(UNDOCUMENTED_BY_DESIGN) - set(shipped_tiers()))
    assert not stale, f"exempted but not shipped: {stale}"


def test_the_exempted_terminal_internals_are_actually_hidden() -> None:
    """The exemption for `term.input` rests entirely on the model being unable to reach it — it
    is `term.write` without the confirmation, which is the hole that separation exists to
    prevent. If it ever stops being hidden, this exemption stops being defensible."""
    by_id = {c.id: c for c in build_registry().all()}
    for tool_id in ("term.input", "term.resize"):
        assert by_id[tool_id].hidden, f"{tool_id} is exempt from TOOLS.md only because it is hidden"
        assert not by_id[tool_id].intents, f"{tool_id} must have no intent routing to it"
