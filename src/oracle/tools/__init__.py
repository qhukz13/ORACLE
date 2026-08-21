"""Tool system: contracts, registry, execution through the policy gate."""

from oracle.tools.contract import (
    ToolArgs,
    ToolContract,
    ToolRegistry,
    ToolRegistryError,
    ToolResult,
    tool,
)
from oracle.tools.executor import (
    Approval,
    ToolError,
    ToolErrorKind,
    ToolExecutor,
    ToolOutcome,
)


def build_registry(*, writes: bool = True) -> ToolRegistry:
    """Build the tool registry.

    `writes=False` yields the Phase 2 read-only set, which the security suite uses to
    assert that a read-only deployment really cannot mutate anything.
    """
    from oracle.tools.filesystem import WRITE_TOOLS
    from oracle.tools.readonly import READ_ONLY_TOOLS

    registry = ToolRegistry()
    for contract in READ_ONLY_TOOLS:
        registry.register(contract)
    if writes:
        for contract in WRITE_TOOLS:
            registry.register(contract)
    return registry


__all__ = [
    "Approval",
    "ToolArgs",
    "ToolContract",
    "ToolError",
    "ToolErrorKind",
    "ToolExecutor",
    "ToolOutcome",
    "ToolRegistry",
    "ToolRegistryError",
    "ToolResult",
    "build_registry",
    "tool",
]
