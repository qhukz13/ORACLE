"""Every internal markdown link in this repo must resolve.

This repo navigates by cross-reference. A spec cites an ADR, an ADR cites an open question, a test
docstring cites a spec, and the argument for a decision is spread across all four. **A link that
404s is a claim nobody can check**, which in a design-first repo is close to a claim that was never
made — and the failure is silent, because a renamed heading breaks every inbound reference at once
while both files still read perfectly.

Found on 2026-09-11: five broken anchors, two of them written the same day. One pointed at
`UI.md#6b-the-execution-tree`, a section that does not exist and has not for some time, from two
different documents; one cited `ADR-0010` by a title it never had; and one — inside ADR-0030 —
cited `ADR-0007` as "the shell holds no business logic" when its actual title is "Clients are peers
of one local API". Every one of those was written by somebody who believed it.

**The slug rule is the whole trick, and getting it wrong makes this test useless in the loud
direction.** A first pass reported 237 breakages because it collapsed *runs* of whitespace into one
hyphen. GitHub converts **each** space, so `## ADR-0013 — Deterministic SVG orbit` — whose em dash
is stripped, leaving two adjacent spaces — slugs to `adr-0013--deterministic-svg-orbit`, with two
hyphens. A checker that cries wolf 237 times is worse than no checker, so the rule is spelled out
here and pinned by its own tests below.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "node_modules", ".git", "target", "__pycache__", "dist", "build"}

MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def markdown_files() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.md") if not any(part in SKIP_DIRS for part in p.parts))


def slugify(heading: str) -> str:
    """GitHub's heading anchor, from the heading's **rendered** text.

    Three rules, each learned by getting it wrong:
    1. A markdown link contributes only its label — `[OQ-06](OPEN_QUESTIONS.md)` renders as
       `OQ-06`, so the URL must not reach the slug.
    2. Emphasis and code markers are formatting, not text.
    3. Punctuation is dropped, then **every remaining space** becomes a hyphen — not every run.
    """
    text = heading.lstrip("#").strip()
    text = MD_LINK.sub(r"\1", text)
    text = text.replace("**", "").replace("*", "").replace("`", "")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"\s", "-", text)


def anchors_of(path: Path) -> set[str]:
    return {
        slugify(line)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.startswith("#")
    }


def internal_links() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for path in markdown_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for _label, target in MD_LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#!")):
                continue
            out.append((path, target))
    return out


LINKS = internal_links()


def test_there_are_links_to_check() -> None:
    """A glob that matches nothing is a test that passes by finding no work."""
    assert len(LINKS) > 100, f"only {len(LINKS)} internal links found — the collector is broken"


@pytest.mark.parametrize(
    ("source", "target"),
    LINKS,
    ids=[f"{p.relative_to(ROOT).as_posix()}->{t}" for p, t in LINKS],
)
def test_an_internal_link_resolves(source: Path, target: str) -> None:
    file_part, _, fragment = target.partition("#")
    dest = (source.parent / file_part).resolve() if file_part else source

    assert dest.exists(), f"{source.relative_to(ROOT)} links to {target}, which does not exist"
    if not fragment or dest.suffix != ".md":
        return
    known = anchors_of(dest)
    assert fragment in known, (
        f"{source.relative_to(ROOT)} links to '#{fragment}' in {dest.name}, "
        f"which has no such heading"
    )


class TestTheSlugRule:
    """The rule this file rests on, pinned so a 237-false-positive pass cannot happen twice."""

    def test_each_space_becomes_a_hyphen_not_each_run(self) -> None:
        # The em dash is stripped, leaving two adjacent spaces, which become two hyphens.
        assert slugify("## ADR-0013 — Deterministic SVG orbit, no force simulation") == (
            "adr-0013--deterministic-svg-orbit-no-force-simulation"
        )

    def test_a_link_in_a_heading_contributes_only_its_label(self) -> None:
        assert slugify("### The open problem — [OQ-06](OPEN_QUESTIONS.md)") == (
            "the-open-problem--oq-06"
        )

    def test_emphasis_and_code_are_formatting_not_text(self) -> None:
        assert slugify("## 3. The state indicator — **the orbital view was cut**") == (
            "3-the-state-indicator--the-orbital-view-was-cut"
        )
        assert slugify("## 10. Command palette (`Ctrl+K`)  `BUILT 2026-08-21`") == (
            "10-command-palette-ctrlk--built-2026-08-21"
        )

    def test_a_heading_that_is_only_punctuation_does_not_crash(self) -> None:
        assert slugify("### ---") == "---"
