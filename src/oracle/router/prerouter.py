"""Deterministic pre-router (ADR-0011).

Boring code, highest leverage in the system. Every turn resolved here costs zero model
latency, zero hallucination risk and zero tokens. Target: >50% of daily turns.

Matching is ORDERED and EXACT. No fuzzy matching, no embeddings, no "close enough" —
ambiguity is supposed to fall through to the model. A pre-router that guesses is worse
than one that declines, because it guesses without the model's context.

It is also the degraded-mode path: with Ollama down, everything here still works
(docs/ARCHITECTURE.md#8-degradation--what-happens-when-a-piece-is-missing).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class PreRouteKind(StrEnum):
    COMMAND = "command"
    HALT = "halt"
    DELEGATE = "delegate"
    PIPELINE = "pipeline"
    NONE = "none"


@dataclass(frozen=True)
class PreRouteResult:
    kind: PreRouteKind
    command: str | None = None
    args: str = ""
    #: Human-readable reason, surfaced in the trace so "why didn't the model run?"
    #: is answerable.
    reason: str = ""

    @property
    def matched(self) -> bool:
        return self.kind is not PreRouteKind.NONE


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    aliases: tuple[str, ...] = ()
    takes_args: bool = False


#: The palette/slash surface. Grows as phases land; each entry is a turn the model
#: never sees.
COMMANDS: tuple[Command, ...] = (
    Command("help", "List available commands", aliases=("h", "?")),
    Command("status", "Agent state, model, event sequence", aliases=("st",)),
    Command("halt", "Emergency stop — cancel everything", aliases=("stop", "abort")),
    Command("sessions", "List recent sessions"),
    Command("clear", "Clear the current conversation view"),
    Command("events", "Show the raw event stream", takes_args=True),
)

_BY_NAME: dict[str, Command] = {}
for _c in COMMANDS:
    _BY_NAME[_c.name] = _c
    for _a in _c.aliases:
        _BY_NAME[_a] = _c

_SLASH = re.compile(r"^/([a-zA-Z][a-zA-Z0-9_-]*)\s*(.*)$", re.DOTALL)

#: Bare words that mean "stop" in either language. Safety path: it must work when the
#: model is down, mid-generation, or wrong. Intentionally a tiny exact-match set —
#: broadening it would start eating ordinary conversation.
_BARE_HALT = frozenset({"stop", "halt", "стоп", "хватит", "остановись", "отмена", "cancel"})

#: Naming an external agent is an explicit instruction, not something to infer.
#: MEASURED (2026-08-21): qwen3.5:0.8b classified "ask Claude to fix the migration" as
#: `investigate`. It is a deterministic fact of the sentence — so it is decided here.
_AGENT_NAMES = ("claude", "antigravity", "agy", "клод")
_AGENT_VERBS = ("ask", "tell", "have", "get", "use", "delegate", "send", "спроси", "попроси")
_AGENT_RE = re.compile(
    r"\b(?:" + "|".join(_AGENT_VERBS) + r")\b[^.?!]{0,24}?\b(?:" + "|".join(_AGENT_NAMES) + r")\b",
    re.IGNORECASE,
)


def pre_route(text: str, *, pipelines: frozenset[str] | None = None) -> PreRouteResult:
    """Resolve deterministically, or decline.

    `pipelines` are registered names. A pipeline reference is a lookup, not a
    judgement — the model has no more information than the registry does.
    """
    raw = text.strip()
    if not raw:
        return PreRouteResult(PreRouteKind.NONE, reason="empty")

    # 1. slash command
    m = _SLASH.match(raw)
    if m:
        name, args = m.group(1).lower(), m.group(2).strip()
        cmd = _BY_NAME.get(name)
        if cmd is None:
            # A typo'd slash command is still a command: answer it deterministically
            # rather than handing "/staus" to a language model.
            return PreRouteResult(
                PreRouteKind.COMMAND, command="help", args=name, reason=f"unknown command /{name}"
            )
        if cmd.name == "halt":
            return PreRouteResult(PreRouteKind.HALT, command="halt", reason="slash halt")
        return PreRouteResult(
            PreRouteKind.COMMAND, command=cmd.name, args=args, reason="slash command"
        )

    # 2. bare stop words — exact match only, never substring
    if raw.lower().rstrip("!.") in _BARE_HALT:
        return PreRouteResult(PreRouteKind.HALT, command="halt", reason="bare stop word")

    # 3. an explicitly named external agent
    if _AGENT_RE.search(raw):
        return PreRouteResult(PreRouteKind.DELEGATE, reason="named an external agent")

    # 4. a registered pipeline name, matched as a whole word
    for name in sorted(pipelines or frozenset(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", raw, re.IGNORECASE):
            return PreRouteResult(
                PreRouteKind.PIPELINE, command=name, reason=f"registered pipeline {name!r}"
            )

    return PreRouteResult(PreRouteKind.NONE, reason="no deterministic match")


def help_text(unknown: str = "") -> str:
    lines = ["Commands:"]
    lines += [
        f"  /{c.name:<10} {c.summary}"
        + (f"   (aliases: {', '.join(c.aliases)})" if c.aliases else "")
        for c in COMMANDS
    ]
    if unknown:
        lines.insert(0, f"Unknown command: /{unknown}\n")
    return "\n".join(lines)
