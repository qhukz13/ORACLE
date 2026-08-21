"""Tool contracts and the registry.

Every tool the model can call is a **promise about what can happen**. Shell strings are
not a promise, which is why there is no general shell tool (ADR-0015).

A contract is validated at startup, not at call time: a malformed tool is a boot
failure, never a runtime surprise.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from oracle.policy.model import Capability, Tier


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
    dry_run: bool
    #: Drives context-budget pre-filtering: sending all tool schemas every turn is the
    #: most common way to waste a small model's context (~730 ms per turn measured).
    intents: frozenset[str]
    side_effects: str
    handler: Callable[..., Awaitable[ToolResult]]

    #: Path-shaped argument names, resolved through the canonicaliser before the gate.
    path_fields: frozenset[str] = field(default_factory=frozenset)

    def json_schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()


class ToolRegistryError(Exception):
    """A contract is malformed. Raised at startup so it cannot be a runtime surprise."""


_WRITING = frozenset(
    {
        Capability.FS_WRITE,
        Capability.FS_DELETE,
        Capability.PROC_SPAWN,
        Capability.NET_EGRESS,
        Capability.GIT_WRITE,
        Capability.INPUT_SYNTH,
        Capability.SYS_SETTINGS,
    }
)


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

        writes = bool(c.capabilities & _WRITING)
        if writes and c.risk is Tier.T0:
            raise ToolRegistryError(
                f"{c.id}: declares a writing capability but risk T0. T0 means no side effect."
            )
        if writes and c.reversible and not c.undo:
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

    def get(self, tool_id: str) -> ToolContract:
        try:
            return self._tools[tool_id]
        except KeyError as exc:
            raise ToolRegistryError(f"unknown tool {tool_id!r}") from exc

    def has(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def all(self) -> list[ToolContract]:
        return sorted(self._tools.values(), key=lambda c: c.id)

    def for_intent(self, intent: str) -> list[ToolContract]:
        """Pre-filter for the context budget. Load-bearing, not hygiene."""
        return [c for c in self.all() if not c.intents or intent in c.intents]

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
        )

    return wrap
