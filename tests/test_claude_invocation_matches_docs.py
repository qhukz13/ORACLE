"""The documented Claude invocation must be the invocation.

`ClaudeCodeAdapter.command()` calls itself *"the pinned invocation, flag for flag
(INTEGRATIONS.md §3)"*. On 2026-09-10 it was not, in both directions at once: the document listed
`--append-system-prompt-file`, which the adapter has never passed — constraints ride inside the
`-p` argument via `HandoffPacket.render_prompt()` — and it omitted `--mcp-config`, which the
adapter does pass when ORACLE lends its own tools.

**Why this is worth a test rather than a careful reading.** `command()` is public precisely so the
egress preview can render the exact argv without submitting anything, and SECURITY.md's rule for
that surface is that it shows the real action and never a paraphrase. The preview was always
correct, so nothing unsafe shipped. But a person checking the preview against §3 would have found
a discrepancy on the one surface where "which of these two is lying?" is the worst possible
question, and nothing in the repo could have answered it.

The assertion is deliberately about the *flag set*, not the exact text. Ordering, placeholders and
prose are the document's business; which flags reach an external agent is the contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from oracle.integrations.claude import ClaudeCodeAdapter
from oracle.integrations.types import HandoffPacket, Workspace

DOC = Path(__file__).resolve().parent.parent / "docs" / "INTEGRATIONS.md"

#: Flags the adapter may pass that carry no meaning for the reader of a pinned invocation.
#: Empty today, and kept as the named place to record an exemption rather than editing the
#: assertion — an exemption with no name is how a contract stops being one.
EXEMPT: frozenset[str] = frozenset()


def documented_flags() -> set[str]:
    """Every `--flag` inside §3's bash block, brackets and all — `[--mcp-config …]` counts."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index("```bash\nclaude -p")
    end = text.index("```", start + 8)
    return set(re.findall(r"--[a-zA-Z][\w-]*", text[start:end]))


def adapter_flags() -> set[str]:
    """Every `--flag` the adapter can emit, with every optional part of the packet present."""
    packet = HandoffPacket(
        task_id="tk_doc",
        task="do the thing",
        acceptance=["it works"],
        constraints=["do not touch main"],
        allowed_tools=["Read", "Edit"],
        context_dir="C:/tmp/ctx",
        mcp_config="C:/tmp/mcp.json",
        result_schema={"type": "object"},
    )
    cmd = ClaudeCodeAdapter().command(packet, Workspace(path=Path("C:/tmp/ws")))
    return {a for a in cmd if a.startswith("--")}


def test_the_document_lists_every_flag_the_adapter_passes() -> None:
    """The direction that matters most: a flag reaching an external agent unmentioned in the
    pinned invocation is undocumented egress behaviour, however benign."""
    missing = adapter_flags() - documented_flags() - EXEMPT
    assert not missing, (
        f"the adapter passes {sorted(missing)} but INTEGRATIONS.md §3 does not list them — "
        f"the egress preview would show a flag the pinned invocation does not mention"
    )


def test_the_document_lists_no_flag_the_adapter_does_not_pass() -> None:
    """The direction that actually bit: §3 carried `--append-system-prompt-file` for weeks while
    the adapter delivered constraints inside `-p`."""
    invented = documented_flags() - adapter_flags() - EXEMPT
    assert not invented, (
        f"INTEGRATIONS.md §3 documents {sorted(invented)}, which the adapter never passes — "
        f"either the flag was dropped from the code or it was never there"
    )


def test_constraints_and_acceptance_reach_the_delegate_in_the_prompt() -> None:
    """The reason `--append-system-prompt-file` is absent rather than missing. If this ever stops
    being true, the flag has to come back and §3 with it — so the mechanism is pinned, not just
    the flag list."""
    packet = HandoffPacket(
        task_id="tk_doc",
        task="do the thing",
        acceptance=["tests pass"],
        constraints=["do not touch main"],
        allowed_tools=["Read"],
    )
    prompt = packet.render_prompt()
    assert "tests pass" in prompt
    assert "do not touch main" in prompt

    cmd = ClaudeCodeAdapter().command(packet, Workspace(path=Path("C:/tmp/ws")))
    assert prompt in cmd, "the rendered prompt must be the -p argument, verbatim"
    assert cmd[cmd.index(prompt) - 1] == "-p"


@pytest.mark.parametrize(
    "flag",
    ["--strict-mcp-config", "--setting-sources", "--permission-mode", "--allowedTools"],
)
def test_the_isolation_flags_are_not_quietly_droppable(flag: str) -> None:
    """These four are not merely documented, they are the isolation. `--strict-mcp-config` is what
    stops a project's own `.mcp.json` joining the run; `--setting-sources user` bounds which
    settings load; the other two bound what the delegate may do. A refactor that drops one would
    still pass the set-comparison above if the document were edited to match, so they are named."""
    assert flag in adapter_flags()
    assert flag in documented_flags()
