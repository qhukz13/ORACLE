"""Tool contracts and the registry.

Every tool the model can call is a **promise about what can happen**. Shell strings are
not a promise, which is why there is no general shell tool (ADR-0015).

A contract is validated at startup, not at call time: a malformed tool is a boot
failure, never a runtime surprise.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from oracle.policy.apps import AppEntry
from oracle.policy.model import WRITING_CAPABILITIES, Capability, Tier


class ToolArgs(BaseModel):
    """Base for tool arguments.

    Constraints must be **decoder-enforceable** (ADR-0017): enums, required fields and
    types. `minimum`, `maximum`, `pattern` and `minLength` are ignored by Ollama's
    constrained decoding — validate them, but never depend on them to shape output.
    """

    model_config = ConfigDict(extra="forbid")


class ToolResult(BaseModel):
    """Structured result. Tools return typed data, never a blob of stdout for the model
    to misread; raw output goes to a blob and is linked (ADR-0015, rule 4)."""

    model_config = ConfigDict(extra="allow")


@dataclass(frozen=True)
class ToolContext:
    """Everything a handler is allowed to know about its invocation.

    Deliberately a value object rather than four keyword arguments: it is exactly what
    crosses the process boundary, so the set of things a tool can reach is reviewable in
    one place. Note what is *not* here — policy, scopes, the audit log, secrets, any
    route back into the runtime (ADR-0003).

    `resolved` and `programs` are already-canonicalised absolute paths. A handler that
    joins its own path or looks up its own program has stepped outside the sandbox, and
    is a review rejection.
    """

    resolved: dict[str, Path] = field(default_factory=dict)
    programs: dict[str, Path] = field(default_factory=dict)
    #: Set only for a tool with an `app_field`. Resolved from the catalogue by the
    #: parent, which is also where such a tool runs (see tools/apps.py).
    app: AppEntry | None = None
    cwd: Path | None = None
    dry_run: bool = False

    def path(self, name: str) -> Path:
        return self.resolved[name]

    def program(self, name: str) -> Path:
        try:
            return self.programs[name]
        except KeyError as exc:  # pragma: no cover - a contract bug, not a runtime one
            raise RuntimeError(
                f"{name!r} was not pinned for this invocation; declare it in the contract"
            ) from exc


@dataclass(frozen=True)
class ToolContract:
    id: str
    summary: str
    args_model: type[ToolArgs]
    result_model: type[ToolResult]
    capabilities: frozenset[Capability]
    scopes: frozenset[str]
    risk: Tier
    reversible: bool
    #: Recipe executed by the undo journal, never by the model.
    undo: str | None
    timeout_s: int
    #: The tool can compute and report its effect WITHOUT performing it.
    #:
    #: Declaring this is a promise the executor relies on: a dry run skips the approval
    #: requirement, because the confirmation card the user answers is rendered from it,
    #: and requiring approval to produce it would be circular. A tool whose dry run has
    #: any side effect — including network egress — may not declare this.
    dry_run: bool
    #: Drives context-budget pre-filtering: sending all tool schemas every turn is the
    #: most common way to waste a small model's context (~730 ms per turn measured).
    intents: frozenset[str]
    side_effects: str
    handler: Callable[..., Awaitable[ToolResult]]

    #: Path-shaped argument names, resolved through the canonicaliser before the gate.
    path_fields: frozenset[str] = field(default_factory=frozenset)
    #: Allowlisted program names this tool always needs, pinned to absolute paths by
    #: the parent and handed over in the context. Fixed here, not chosen at call time.
    programs: frozenset[str] = field(default_factory=frozenset)
    #: The one argument that names a program, for the gated escape hatch
    #: (`dev.execute`). Its argv is checked against the subcommand rules; a tool with a
    #: fixed program is not.
    program_field: str | None = None
    #: The argument naming an entry in `config/apps.yaml`. A tool that declares this
    #: runs in the PARENT and launches detached — the one deliberate exception to
    #: ADR-0003, because an application the user opened must outlive HALT and the
    #: toolhost's Job Object cannot let anything escape it. See tools/apps.py.
    app_field: str | None = None
    #: Never offered to the model. Used for recipes the undo journal executes, which
    #: must exist in the registry (the child dispatches by id) but must not be
    #: selectable — a model that could call `git.uncommit` could undo work unasked.
    hidden: bool = False

    def json_schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()


class ToolRegistryError(Exception):
    """A contract is malformed. Raised at startup so it cannot be a runtime surprise."""


_WRITING = WRITING_CAPABILITIES


class ToolRegistry:
    #: Hard cap. Tool schemas consume the router's context every turn and near-duplicate
    #: tools measurably degrade selection accuracy in small models (TOOLS.md rule 2).
    MAX_TOOLS: ClassVar[int] = 40

    def __init__(self) -> None:
        self._tools: dict[str, ToolContract] = {}

    def register(self, contract: ToolContract) -> None:
        self._validate(contract)
        if contract.id in self._tools:
            raise ToolRegistryError(f"duplicate tool id {contract.id!r}")
        if len(self._tools) >= self.MAX_TOOLS:
            raise ToolRegistryError(
                f"tool cap of {self.MAX_TOOLS} reached; merge a tool before adding one"
            )
        self._tools[contract.id] = contract

    @staticmethod
    def _validate(c: ToolContract) -> None:
        if "." not in c.id:
            raise ToolRegistryError(f"{c.id!r} must be namespaced, e.g. 'fs.read'")
        if not c.summary:
            raise ToolRegistryError(f"{c.id}: summary is required — the model reads it")

        # `proc.spawn` alone does not prove a MUTATION: `git status` spawns and changes
        # nothing. It is only honest at T0 while the argv is FIXED by the tool — the
        # moment the model picks the program (`dev.execute`), what the process does
        # stops being a promise the contract can make.
        mutates = bool(c.capabilities & _WRITING - {Capability.PROC_SPAWN}) or (
            c.program_field is not None
        )
        if mutates and c.risk is Tier.T0:
            raise ToolRegistryError(
                f"{c.id}: declares a writing capability but risk T0. T0 means no side effect."
            )
        if mutates and c.reversible and not c.undo:
            raise ToolRegistryError(f"{c.id}: reversible=True requires an `undo` recipe")
        if c.risk >= Tier.T3 and not c.dry_run:
            raise ToolRegistryError(
                f"{c.id}: tier {c.risk.label} must support dry_run so the confirmation "
                f"card can show a real preview, not a description"
            )
        if c.path_fields and not (
            c.capabilities & {Capability.FS_READ, Capability.FS_WRITE, Capability.FS_DELETE}
        ):
            raise ToolRegistryError(f"{c.id}: declares path_fields but no fs capability")
        unknown = c.path_fields - set(c.args_model.model_fields)
        if unknown:
            raise ToolRegistryError(f"{c.id}: path_fields not in args model: {sorted(unknown)}")

        # `fs.write` means "writes a path this contract names" (see Capability.FS_WRITE).
        # Without a path field there is nothing to back up, so the undo plan a T1 write
        # promises could not exist — which is exactly how a tool ends up running
        # unprompted with no way back.
        if (c.capabilities & {Capability.FS_WRITE, Capability.FS_DELETE}) and not c.path_fields:
            raise ToolRegistryError(
                f"{c.id}: declares fs.write/fs.delete but names no path field. If the "
                f"writes happen inside a spawned program, declare proc.spawn instead."
            )

        spawns = bool(c.programs or c.program_field or c.app_field)
        if spawns and Capability.PROC_SPAWN not in c.capabilities:
            raise ToolRegistryError(
                f"{c.id}: names a program but does not declare proc.spawn. The capability "
                f"is what the gate reads; a mismatch here is a silent privilege gap."
            )
        if Capability.PROC_SPAWN in c.capabilities and not spawns:
            raise ToolRegistryError(
                f"{c.id}: declares proc.spawn but names no program. Every spawned "
                f"program must come from the allowlist (docs/SECURITY.md#4b)."
            )
        if c.program_field is not None and c.program_field not in c.args_model.model_fields:
            raise ToolRegistryError(f"{c.id}: program_field {c.program_field!r} not in args model")

        if c.app_field is not None:
            # This is the only escape from the Job Object, so the shape of a tool that
            # takes it is pinned down here rather than trusted to review.
            if c.app_field not in c.args_model.model_fields:
                raise ToolRegistryError(f"{c.id}: app_field {c.app_field!r} not in args model")
            if c.programs or c.program_field:
                raise ToolRegistryError(
                    f"{c.id}: an app launcher runs in the parent and cannot also spawn "
                    f"an allowlisted program there"
                )
            if Capability.PROC_SPAWN not in c.capabilities:
                raise ToolRegistryError(f"{c.id}: declares app_field but not proc.spawn")

    def get(self, tool_id: str) -> ToolContract:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise ToolRegistryError(f"unknown tool {tool_id!r}") from exc

    def has(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def all(self) -> list[ToolContract]:
        return sorted(self._tools.values(), key=lambda c: c.id)

    def offerable(self) -> list[ToolContract]:
        """Everything the model may ever see. Hidden tools are not part of it."""
        return [c for c in self.all() if not c.hidden]

    def for_intent(self, intent: str) -> list[ToolContract]:
        """Pre-filter for the context budget. Load-bearing, not hygiene."""
        return [c for c in self.offerable() if not c.intents or intent in c.intents]

    def __len__(self) -> int:
        return len(self._tools)


def tool(
    *,
    id: str,  # mirrors the contract field name
    summary: str,
    args: type[ToolArgs],
    result: type[ToolResult],
    capabilities: set[Capability] | frozenset[Capability] = frozenset(),
    scopes: set[str] | frozenset[str] = frozenset(),
    risk: Tier = Tier.T4,
    reversible: bool = False,
    undo: str | None = None,
    timeout_s: int = 30,
    dry_run: bool = False,
    intents: set[str] | frozenset[str] = frozenset(),
    side_effects: str = "",
    path_fields: set[str] | frozenset[str] = frozenset(),
    programs: set[str] | frozenset[str] = frozenset(),
    program_field: str | None = None,
    app_field: str | None = None,
    hidden: bool = False,
) -> Callable[[Callable[..., Awaitable[ToolResult]]], ToolContract]:
    """Declare a tool. Returns the contract, so the module-level name *is* the contract."""

    def wrap(fn: Callable[..., Awaitable[ToolResult]]) -> ToolContract:
        return ToolContract(
            id=id,
            summary=summary,
            args_model=args,
            result_model=result,
            capabilities=frozenset(capabilities),
            scopes=frozenset(scopes),
            risk=risk,
            reversible=reversible,
            undo=undo,
            timeout_s=timeout_s,
            dry_run=dry_run,
            intents=frozenset(intents),
            side_effects=side_effects,
            handler=fn,
            path_fields=frozenset(path_fields),
            programs=frozenset(programs),
            program_field=program_field,
            app_field=app_field,
            hidden=hidden,
        )

    return wrap
