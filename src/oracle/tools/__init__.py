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
    from oracle.tools.readonly import READ_ONLY_TOOLS, SPAWNING_READ_TOOLS

    registry = ToolRegistry()
    for contract in READ_ONLY_TOOLS:
        registry.register(contract)
    if writes:
        # Spawning tools are excluded from the read-only set even when they only read:
        # the gate denies `proc.spawn` in lockdown, so a read-only build that listed
        # them would be advertising something that can never run.
        for contract in (*SPAWNING_READ_TOOLS, *WRITE_TOOLS):
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
