"""The pipeline file format (PIPELINES.md §2), as the models it is generated from.

A pipeline is **a named sequence of registered tool calls with conditions and artifacts.**
That is the whole of it, and PIPELINES.md §1 defends the boundary with a litmus: *if a
pipeline needs branching logic and variables, it wants to be a script — and `dev.execute`
can run that script as a single step.*

This file is where that litmus is enforced, because a scope cut argued in prose is a scope
cut somebody adds a field to next month. Every refusal below is `extra="forbid"` or a
missing enum member, so the feature cannot be smuggled in by writing YAML — it has to be
argued here first.

**Five things a v1 pipeline deliberately cannot say**, each with the reason it costs
nothing to refuse:

* **`{{ steps.<id>.<field> }}`** — an argument that depends on a previous step's *output*
  cannot be priced, resolved or digest-bound before the run, and PIPELINES.md §3 requires
  exactly one approval up front listing every elevated step with its concrete arguments.
  Steps share a filesystem: a step that needs the previous one's output reads the file it
  wrote.
* **`when:` over a step result** — the same reason. `when` is evaluated at compile time
  against bound parameters, so the approval card shows precisely the steps that will run.
* **`on_failure: ask`** — PIPELINES.md §3 says "never a prompt mid-run" nine lines above
  where it offers `ask`. The document contradicts itself; this resolves it the way the
  security model requires.
* **`retry: { on: [...] }`** — §3 also says retries are for steps "declared retryable in
  their tool contract … the tool decides, not the pipeline author". Retrying a
  non-idempotent step is a data-loss bug; `retry.max` sets how many attempts, and
  `runners/tool.py` still decides whether a given failure earns one.
* **A T3 step** — refused during validation rather than here, because it needs the policy
  gate to know the tier. Noted in this list because it belongs to the same set of noes:
  T3 requires the desktop and a phrase typed *for that invocation* (SECURITY.md §5), and
  pre-approving one from a batch card would launder `confirm_strong` into `confirm`.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The graph's own ceiling, reused rather than restated. A pipeline compiles to a task
#: graph and would be refused by `orchestration.graph.validate` anyway; catching it here
#: buys a line number.
from oracle.orchestration.graph import MAX_GRAPH_SIZE

ParamType = Literal["bool", "string", "int", "enum"]

#: `ask` is absent, and its absence is the refusal. See the module docstring.
OnFailure = Literal["abort", "continue"]

#: `junit` is absent: `dev.run_tests` parses results in-process and writes a text blob,
#: so there is no junit file on disk to capture. `result` is the structured `ToolResult`.
Capture = Literal["stdout", "result"]

_NAME = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
_STEP_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
#: An artifact label is a *label*, not a path. No separators, no drive letters, no `..` —
#: it is validated here so that if a later version writes it to disk, the traversal
#: question was already settled at parse time rather than at the point of the write.
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class Frozen(BaseModel):
    """`extra="forbid"` everywhere, for the reason `PlannedTask` has it: a field nobody
    wrote down is a field somebody is trying to smuggle."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ParamSpec(Frozen):
    """One parameter, and its default.

    The default is **required**. A pipeline must be runnable with no arguments — it is
    named, discovered and offered from a palette, and a run that cannot start without
    someone remembering a value is a run nobody starts.
    """

    type: ParamType
    default: bool | str | int
    #: `enum` only. Empty for every other type, and required non-empty for `enum`.
    choices: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _choices_match_type(self) -> ParamSpec:
        if self.type == "enum":
            if not self.choices:
                raise ValueError("an enum parameter needs choices")
            if str(self.default) not in self.choices:
                raise ValueError(f"default {self.default!r} is not one of {list(self.choices)}")
        elif self.choices:
            raise ValueError(f"choices are only meaningful for an enum, not {self.type}")
        return self


class RetrySpec(Frozen):
    """How many attempts, and nothing about which failures earn one."""

    #: 0 means "one attempt, no retry". Capped at 2 because `DEFAULT_MAX_ATTEMPTS` is the
    #: graph's judgement and a YAML author raising it indefinitely is a fork bomb with a
    #: schedule.
    max: int = Field(ge=0, le=2)


class StepSpec(Frozen):
    """One registered tool call."""

    id: str
    #: A tool id from the `ToolRegistry`. Existence, argument shape, tier and scope are
    #: all checked later, where the registry and the policy gate are — this model only
    #: knows that a tool was named.
    tool: str
    #: A list is allowed because tools take them: `dev.execute` has `args: list[str]`,
    #: and without this the one tool a self-check pipeline most needs cannot be called
    #: at all. Nested structures are not allowed — a tool argument that needs a shape
    #: this cannot express is a tool a pipeline should be calling through a script.
    with_: dict[str, str | int | bool | list[str]] = Field(default_factory=dict, alias="with")
    #: A boolean expression over `params.*` and `project.*`. Never over `steps.*`.
    when: str | None = None
    #: Seconds. Overrides the scheduler's per-kind ceiling for this one task, which is
    #: what lets a ten-minute test run live in a graph whose default is two minutes.
    timeout: int | None = Field(default=None, gt=0, le=3600)
    on_failure: OnFailure = "abort"
    retry: RetrySpec | None = None

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not _STEP_ID.match(v):
            raise ValueError(f"{v!r} is not a step id (lowercase, digits, underscore)")
        return v

    @field_validator("tool")
    @classmethod
    def _tool_shape(cls, v: str) -> str:
        # Shape only. A tool id is `area.verb`; whether it exists is the registry's word.
        if not re.match(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$", v):
            raise ValueError(f"{v!r} is not a tool id of the form 'area.verb'")
        return v


class ArtifactSpec(Frozen):
    """A pointer to something a step already wrote — not a copy of it.

    A second artifact store is a second thing to back up and a second place for a path
    built from author-controlled text to land. What a run records is a manifest: which
    step, what was captured, and the blob the tool itself produced.
    """

    from_: str = Field(alias="from")
    capture: Capture
    as_: str = Field(alias="as")

    @field_validator("as_")
    @classmethod
    def _label_not_a_path(cls, v: str) -> str:
        if not _LABEL.match(v):
            raise ValueError(f"{v!r} is a label, not a path — no separators, no '..'")
        return v


class Pipeline(Frozen):
    """One pipeline file.

    `Pipeline.model_json_schema()` is the only schema for this format. Nothing
    hand-writes one (AGENTS.md), which is why the refusals above are models rather than
    documentation.
    """

    version: Literal[1]
    name: str
    description: str = ""
    #: A project name from `discover_projects`. For a pipeline discovered *inside* a
    #: project this is pinned by the loader and may not disagree with where it was found.
    project: str | None = None
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    steps: tuple[StepSpec, ...]
    artifacts: tuple[ArtifactSpec, ...] = ()

    @field_validator("name")
    @classmethod
    def _name_shape(cls, v: str) -> str:
        if not _NAME.match(v):
            raise ValueError(f"{v!r} is not a pipeline name (lowercase, digits, hyphen)")
        return v

    @field_validator("steps")
    @classmethod
    def _steps_bounded(cls, v: tuple[StepSpec, ...]) -> tuple[StepSpec, ...]:
        if not v:
            raise ValueError("a pipeline with no steps is not a pipeline")
        if len(v) > MAX_GRAPH_SIZE:
            raise ValueError(f"{len(v)} steps exceeds the graph ceiling of {MAX_GRAPH_SIZE}")
        return v

    @model_validator(mode="after")
    def _ids_and_references_line_up(self) -> Pipeline:
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"duplicate step id {step.id!r}")
            seen.add(step.id)
        for art in self.artifacts:
            if art.from_ not in seen:
                raise ValueError(f"artifact captures from {art.from_!r}, which is not a step")
        return self
