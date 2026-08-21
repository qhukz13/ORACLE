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


def build_registry() -> ToolRegistry:
    """The Phase 2 tool set: read-only only.

    Write tools arrive in Phase 3, once the gate proven here has shipped.
    """
    from oracle.tools.readonly import READ_ONLY_TOOLS

    registry = ToolRegistry()
    for contract in READ_ONLY_TOOLS:
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
