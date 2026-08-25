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

from oracle.logsink import get_logger
from oracle.runners.delegation import make_delegation_runner
from oracle.runners.planning import Planner, approve_graph, make_replanner
from oracle.runners.report import make_report_runner
from oracle.runners.tool import make_tool_runner
from oracle.runners.verify import BaselineCache, make_verify_runner

log = get_logger(__name__)

__all__ = [
    "BaselineCache",
    "Planner",
    "approve_graph",
    "build_runners",
    "make_delegation_runner",
    "make_replanner",
    "make_report_runner",
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

    from oracle.delegation.service import PacketInputs
    from oracle.memory import as_packet_attempts
    from oracle.memory.attempts import DEFAULT_LIMIT, match, signature
    from oracle.orchestration.models import Task

    async def inputs_for(task: Task) -> Any:
        """What a worker is told beyond its objective. Today: what was already tried.

        Nobody hand-feeds this — a repeated task's packet carries the prior attempt
        because the signature matched, which is the acceptance criterion memory exists
        to meet. A memory outage produces a thinner packet, never a failed delegation:
        the same degradation rule curation already follows."""
        try:
            goal = task.spec.objective
            project = task.spec.project or ""
            found = await state.memory.attempts_for(signature(goal, project), project=project)
            if not found:
                found = match(goal, await state.memory.attempts_in(project), limit=DEFAULT_LIMIT)
            return PacketInputs(attempts=tuple(as_packet_attempts(found[:DEFAULT_LIMIT])))
        except Exception as exc:
            log.info("delegate.attempts_unavailable", task_id=task.id, reason=str(exc))
            return PacketInputs()

    repo = state.settings.projects_root
    delegation = make_delegation_runner(
        state.delegations, repo, allowed_tools=("Read", "Edit", "Write"), inputs_for=inputs_for
    )
    return {
        TaskKind.TOOL: make_tool_runner(state.executor, state.approvals),
        TaskKind.DELEGATION: delegation,
        TaskKind.VERIFY: make_verify_runner(
            state.task_store, run_tests, BaselineCache(repo, run_tests)
        ),
        # The local model owns REPORT now (P8-T3), which is what PLANNER.md §4 always
        # said: a summarizer is never routed to a cloud agent. `compile_plan` routes a
        # role only local agents hold to this kind, so the two halves of that rule are
        # the same decision read off the same registry.
        TaskKind.REPORT: make_report_runner(state.provider, state.task_store),
        TaskKind.PLANNING: delegation,
    }
