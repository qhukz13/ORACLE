"""What `tools/list` shows a delegate.

Built from the same `ToolContract`s the model's own selector sees, so a tool cannot
drift into having two descriptions. Two differences from the internal view, both
deliberate:

* **Only what the capability lends**, and only within this delegation's workspace. A
  tool the owner could run is not thereby a tool the delegate may.
* **The description says where it runs.** A delegate that does not know its `path`
  arguments must sit inside the worktree will spend turns discovering that by refusal,
  and turns cost the owner money.
"""

from __future__ import annotations

from typing import Any

from oracle.mcp.tokens import Capability
from oracle.tools.contract import ToolRegistry, ToolRegistryError


def describe(registry: ToolRegistry, cap: Capability) -> list[dict[str, Any]]:
    """MCP tool descriptors for everything this capability lends."""
    out: list[dict[str, Any]] = []
    for tool_id in cap.tools:
        try:
            contract = registry.get(tool_id)
        except ToolRegistryError:
            # A capability naming a tool this build does not have is a version skew,
            # not an error: show what exists and let the delegate work with it.
            continue
        schema = contract.args_model.model_json_schema()
        out.append(
            {
                # MCP names cannot carry a dot in every client, and the delegate sees
                # these prefixed as `mcp__oracle__*` anyway.
                "name": tool_id.replace(".", "_"),
                "description": (
                    f"{contract.summary} Runs inside this delegation's workspace "
                    f"({cap.root}); paths outside it are refused."
                ),
                "inputSchema": schema,
            }
        )
    return out


def resolve(name: str, cap: Capability) -> str | None:
    """MCP tool name → ORACLE tool id, if this capability lends it.

    Returns None rather than raising: an unknown name from a delegate is a refusal it
    can read and correct, not an exception in the daemon.
    """
    candidate = name.replace("_", ".", 1) if "." not in name else name
    for tool_id in cap.tools:
        if tool_id == name or tool_id.replace(".", "_") == name or tool_id == candidate:
            return tool_id
    return None
