"""Runners: the adapters between the supervisor and the machinery that does the work.

The scheduler takes runners by injection and imports nothing that executes
(`tests/security/test_orchestration_boundary.py` enforces that against the source). This
package is the other side of that seam — it is *allowed* to import both, because
composing is what it is for, and it is the only place where "a task" and "a tool call"
are both in scope.

Everything here is an adapter over something that already worked before the graph
existed: `ToolExecutor` ran gated tool calls, `DelegationService` ran a complete
delegation lifecycle, `dev.run_tests` ran a suite. None of them is rewritten here.
"""

from pathlib import Path
from typing import Any

from oracle.runners.delegation import make_delegation_runner
from oracle.runners.planning import Planner, approve_graph, make_replanner
from oracle.runners.tool import make_tool_runner
from oracle.runners.verify import BaselineCache, make_verify_runner

__all__ = [
    "BaselineCache",
    "Planner",
    "approve_graph",
    "build_runners",
    "make_delegation_runner",
    "make_replanner",
    "make_tool_runner",
    "make_verify_runner",
]


def build_runners(state: Any) -> dict[Any, Any]:
    """Every runner a graph can need, bound to the daemon's real components.

    This is the composition P7-T2 deliberately left undone — building runners nothing
    called would have been dead code wearing the costume of integration. Now something
    calls them.

    `state` is the daemon's `AppState`, taken loosely on purpose: importing it here would
    make the runners depend on the API layer that assembles them, which is backwards.
    """
    from oracle.orchestration.models import TaskKind
    from oracle.runners.verify import BaselineCache

    async def run_tests(path: Path) -> dict[str, Any] | None:
        """ORACLE's own verification, through the gate — the half a worker cannot
        influence. `ran` means the suite ran, not that the call returned."""
        outcome = await state.executor.execute("dev.run_tests", {"path": str(path)})
        detail = (
            outcome.result.model_dump() if hasattr(outcome.result, "model_dump") else outcome.result
        )
        started = isinstance(detail, dict) and "passed" in detail
        return {"ran": bool(started), "ok": outcome.ok, "detail": detail}

    repo = state.settings.projects_root
    delegation = make_delegation_runner(
        state.delegations, repo, allowed_tools=("Read", "Edit", "Write")
    )
    return {
        TaskKind.TOOL: make_tool_runner(state.executor, state.approvals),
        TaskKind.DELEGATION: delegation,
        TaskKind.VERIFY: make_verify_runner(
            state.task_store, run_tests, BaselineCache(repo, run_tests)
        ),
        # A REPORT task is a delegation with a read-only role until the local model owns
        # it (PLANNER.md §4: summarizer is never routed to a cloud agent — that is a
        # Phase 9 correction, and pretending otherwise here would hide it).
        TaskKind.REPORT: delegation,
        TaskKind.PLANNING: delegation,
    }
