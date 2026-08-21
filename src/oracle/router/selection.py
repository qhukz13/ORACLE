"""Tool selection: turning a classified intent into one concrete call.

Two rules from docs/AGENT_RUNTIME.md shape all of this, and they are the reason a 0.8B
model can be trusted with the job at all:

**1. The model fills in a bounded schema; it does not author control flow.**
It picks a tool *name* from an enum built out of the tools that survived intent
filtering, and supplies at most one free-text value. It never writes a path, never
writes an argv, and never decides that two tools should run.

**2. Arguments are constructed in code, from resolved facts.**
The project path comes from the registry, not from the model — a hallucinated project
resolves to nothing and asks for clarification, rather than becoming a filesystem path.
The only thing the model contributes is text that is *inherently* text: a commit
message, a test filter, an app alias.

That split is why this is safe with a small model. The failure mode of a bad selection
is "the wrong tool refused by the gate" or "an obviously wrong commit message", not
"an argv nobody predicted".

**Only tools whose arguments can be built honestly are offered.** `fs.write` needs file
content, `dev.execute` needs an argv, `term.write` needs a command — none can be filled
from (project, one string) without inventing something. They are excluded here rather
than half-filled, because a tool that is offered and then fails to be callable is worse
than one that was never offered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from oracle.context.budget import Band, ContextAssembler, Item
from oracle.llm.provider import LLMProvider
from oracle.llm.structured import StructuredStats, generate_structured
from oracle.llm.types import CallType
from oracle.logsink import get_logger
from oracle.tools.contract import ToolContract, ToolRegistry

log = get_logger(__name__)

#: The answer for "none of these fit". Present in every enum so refusing is always
#: expressible — a model with no way to say no will pick something.
NO_TOOL = "none"

#: Tools the router may choose, and how their arguments are built. A tool absent from
#: here is never offered, however well it matches the intent.
#:
#: `text` is whatever the model extracted; `path` is the resolved project directory.
ARG_BUILDERS: dict[str, str] = {
    # project path only
    "git.status": "path",
    "git.diff": "path",
    "git.log": "path",
    "git.add": "path",
    "dev.build": "path",
    "dev.lint": "path",
    "fs.list": "path",
    # Buildable from the project alone because both of its other arguments have honest
    # defaults: `origin`, and the branch that is actually checked out. "push my changes"
    # means exactly that. It is also the only routable tool above T1, which matters —
    # without it the Confirmation Center could never fire from a routed turn, and the
    # most safety-critical surface in the product would be unreachable by the user.
    "git.push": "path",
    # project path plus one string the model supplies
    "git.commit": "path+message",
    "dev.run_tests": "path+filter",
    # neither
    "sys.info": "none",
    "sys.processes": "none",
}

#: How much of the tool catalogue may reach the prompt. Measured elsewhere: schemas are
#: the biggest avoidable cost in the router's context, and near-duplicates degrade
#: selection accuracy in small models (docs/TOOLS.md rule 2).
MAX_CANDIDATES = 8

_SYSTEM = """Choose ONE tool that does what the user asked, or "none".

Rules:
- Pick from the list. Never invent a name.
- "none" whenever no tool clearly does it. A near-miss is worse than "none":
  the user gets an action they did not ask for.
- text: ONLY when the tool needs a message or a filter. Otherwise leave it empty.
  * a commit message -> the words the user wants recorded, nothing else
  * a test filter -> the test name or keyword they mentioned

Never put a file path, a directory or a command in `text`. Those are not yours to
choose."""

#: MEASURED: few-shot examples took selection accuracy from 83.3% to 100% on the
#: eval set (scripts/eval_selection.py). Same lever, same reason as the intent
#: classifier's: prompt processing runs ~1700 tok/s on this GPU while generation runs at
#: ~45, so examples are close to free and generated tokens are not.
#:
#: Chosen to cover the pairs that actually confused the model, not to be tidy:
#:   * add vs commit — "commit my changes" selected `git.add` in a live run, and staged
#:     without committing. Plausible, wrong, and silent.
#:   * status vs diff — "is X clean" wants a verdict, not a patch.
#:   * "none" appears twice, because a model that never sees a refusal never produces
#:     one: both misses in the baseline were the model reaching for the nearest tool.
_EXAMPLES = """Examples:

"commit my changes with message fix the login redirect"
  -> {"tool":"git.commit","text":"fix the login redirect"}
"закоммить с сообщением почини редирект"
  -> {"tool":"git.commit","text":"почини редирект"}
"stage everything" -> {"tool":"git.add","text":""}
"push my changes" -> {"tool":"git.push","text":""}
"is the repo clean" -> {"tool":"git.status","text":""}
"what changed since the last commit" -> {"tool":"git.diff","text":""}
"run the tests" -> {"tool":"dev.run_tests","text":""}
"run only the login tests" -> {"tool":"dev.run_tests","text":"login"}
"delete all the log files" -> {"tool":"none","text":""}
"send this to the printer" -> {"tool":"none","text":""}"""


class SelectionError(Exception):
    """Selection could not produce a callable tool. Never a reason to guess."""


@dataclass
class Selection:
    tool: str | None
    args: dict[str, Any]
    text: str
    candidates: list[str]
    reason: str = ""

    @property
    def chose_nothing(self) -> bool:
        return self.tool is None


def candidates_for(registry: ToolRegistry, intent: str) -> list[ToolContract]:
    """Intent-filtered, buildable, and capped. This is the context budget's lever."""
    offered = [c for c in registry.for_intent(intent) if c.id in ARG_BUILDERS]
    return offered[:MAX_CANDIDATES]


def _plan_model(candidates: list[ToolContract]) -> type[BaseModel]:
    """Build a schema whose `tool` field is an enum of exactly these ids.

    ADR-0017: Ollama's constrained decoding enforces enums and required fields, and
    ignores `pattern` and `minLength`. So the tool name is made *unspellable* if it is
    not on the list, rather than validated after the fact — the decoder cannot emit a
    tool that does not exist.
    """
    names = {c.id.replace(".", "_"): c.id for c in candidates}
    names["none"] = NO_TOOL
    tool_enum = Enum("ToolName", names)  # type: ignore[misc]

    return create_model(
        "ToolPlan",
        __config__=ConfigDict(extra="ignore"),
        tool=(tool_enum, ...),
        text=(
            str,
            Field(default="", description="A commit message or a test filter. Usually empty."),
        ),
    )


def _describe(candidates: list[ToolContract]) -> str:
    """What the model reads. The summary is the whole basis for its choice, which is
    why the registry refuses a contract without one."""
    return "Tools:\n" + "\n".join(f"- {c.id}: {c.summary}" for c in candidates)


class ToolSelector:
    def __init__(
        self,
        registry: ToolRegistry,
        provider: LLMProvider | None = None,
        *,
        assembler: ContextAssembler | None = None,
        stats: StructuredStats | None = None,
    ) -> None:
        self._registry = registry
        self._provider = provider
        self._assembler = assembler or ContextAssembler()
        self.stats = stats or StructuredStats()

    async def select(self, text: str, intent: str, *, project_path: Path | None) -> Selection:
        candidates = candidates_for(self._registry, intent)
        names = [c.id for c in candidates]
        if not candidates:
            return Selection(None, {}, "", names, reason=f"no tool serves the {intent} intent")
        if self._provider is None:
            return Selection(None, {}, "", names, reason="reasoning is offline")

        plan_model = _plan_model(candidates)
        assembled = self._assembler.assemble(
            CallType.SELECT,
            [
                Item(Band.SYSTEM, _SYSTEM, role="system", provenance="system"),
                Item(Band.SYSTEM, _EXAMPLES, role="system", provenance="system"),
                Item(Band.TOOLS, _describe(candidates), role="system", provenance="system"),
                Item(Band.TASK, f"Request: {text}", role="user", provenance="user"),
            ],
        )
        result = await generate_structured(
            self._provider,
            assembled.messages,
            plan_model,
            stats=self.stats,
            max_tokens=80,
            call_type=CallType.SELECT,
        )

        # `getattr`, not attribute access: the model class was built at call time by
        # `create_model`, so its fields do not exist statically. The enum is what makes
        # the value trustworthy, not the type checker.
        chosen = getattr(result.value, "tool", NO_TOOL)
        tool_id = chosen.value if isinstance(chosen, Enum) else str(chosen)
        supplied = str(getattr(result.value, "text", "") or "").strip()

        if tool_id == NO_TOOL:
            return Selection(None, {}, supplied, names, reason="the model chose no tool")
        if tool_id not in names:  # pragma: no cover - the enum makes this unspellable
            log.warning("selection.off_menu", proposed=tool_id)
            return Selection(None, {}, supplied, names, reason=f"{tool_id} is not on the menu")

        try:
            args = build_args(tool_id, supplied, project_path)
        except SelectionError as exc:
            return Selection(None, {}, supplied, names, reason=str(exc))
        return Selection(tool_id, args, supplied, names)


def build_args(tool_id: str, text: str, project_path: Path | None) -> dict[str, Any]:
    """Construct the call from resolved facts plus at most one model-supplied string.

    Raises rather than improvising. "I know which tool but not where" is a question to
    ask, not a blank to fill in.
    """
    shape = ARG_BUILDERS.get(tool_id)
    if shape is None:
        raise SelectionError(f"{tool_id} cannot be called from a routed turn yet")

    if shape == "none":
        return {}

    if project_path is None:
        raise SelectionError(f"{tool_id} needs to know which project")
    args: dict[str, Any] = {"path": str(project_path)}

    if shape == "path+message":
        message = text.strip()
        if len(message) < 3:
            # Refusing beats committing "update" over somebody's afternoon of work.
            raise SelectionError("a commit needs a message, and none was given")
        args["message"] = message
    elif shape == "path+filter" and text.strip():
        args["filter"] = text.strip()

    return args


__all__ = [
    "ARG_BUILDERS",
    "MAX_CANDIDATES",
    "NO_TOOL",
    "Selection",
    "SelectionError",
    "ToolSelector",
    "build_args",
    "candidates_for",
]
