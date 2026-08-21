"""Tool system: contracts, registry, execution through the policy gate."""

from collections.abc import Awaitable, Callable
from typing import Any

from oracle.tools.contract import (
    ToolArgs,
    ToolContext,
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
from oracle.tools.undo import UndoError, UndoPlan


def build_registry(*, writes: bool = True) -> ToolRegistry:
    """Build the tool registry.

    `writes=False` yields the Phase 2 read-only set, which the security suite uses to
    assert that a read-only deployment really cannot mutate anything.
    """
    from oracle.tools.dev import DEV_TOOLS
    from oracle.tools.filesystem import WRITE_TOOLS
    from oracle.tools.git import GIT_TOOLS
    from oracle.tools.readonly import READ_ONLY_TOOLS, SPAWNING_READ_TOOLS

    registry = ToolRegistry()
    for contract in READ_ONLY_TOOLS:
        registry.register(contract)
    if writes:
        # Spawning tools are excluded from the read-only set even when they only read:
        # the gate denies `proc.spawn` in lockdown, so a read-only build that listed
        # them would be advertising something that can never run.
        for contract in (*SPAWNING_READ_TOOLS, *WRITE_TOOLS, *GIT_TOOLS, *DEV_TOOLS):
            registry.register(contract)
    return registry


def git_undo_runner(executor: ToolExecutor) -> Callable[[UndoPlan], Awaitable[dict[str, Any]]]:
    """Let the undo journal reverse a git mutation — through the gate, like anything else.

    The journal cannot run `git` itself: it lives in the process that holds the API key
    (ADR-0003). So a git-shaped undo becomes an ordinary tool call to the hidden
    `git.undo`, which means it is tiered, audited and scope-checked exactly like the
    commit it is reversing. An undo path that bypassed the gate would be a second way
    to make things happen, and there is only ever supposed to be one.
    """

    async def run_plan(plan: UndoPlan) -> dict[str, Any]:
        outcome = await executor.execute(
            "git.undo",
            {
                "path": plan.target,
                "kind": str(plan.kind),
                "ref": plan.origin or "",
                "extra": plan.backup or "",
            },
        )
        if not outcome.ok or outcome.result is None:
            message = outcome.error.message if outcome.error else "git.undo failed"
            raise UndoError(message)
        return outcome.result.model_dump(mode="json")

    return run_plan


__all__ = [
    "Approval",
    "ToolArgs",
    "ToolContext",
    "ToolContract",
    "ToolError",
    "ToolErrorKind",
    "ToolExecutor",
    "ToolOutcome",
    "ToolRegistry",
    "ToolRegistryError",
    "ToolResult",
    "build_registry",
    "git_undo_runner",
    "tool",
]
