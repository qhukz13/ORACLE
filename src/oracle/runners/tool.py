"""The TOOL runner: one task, one gated `ToolInvocation`.

There is nothing new here and that is the design. A `TOOL` task is an ordinary tool call
through the existing `ToolExecutor` — same registry, same policy engine, same audit
entry, same undo journal. The graph is a new way to *ask*, not a new way to *execute*
(SECURITY.md §10, rule 1: the scheduler feeds the gate and is not a second one).

What this adapter adds is the translation, and one judgement: a policy denial is not
retryable, so it must not be dressed up as a transient failure that the scheduler will
cheerfully try again. Retrying a denial is how an agent nags a person into approving
something.
"""

from __future__ import annotations

from typing import Any

from oracle.logsink import get_logger
from oracle.orchestration.models import Task, TaskError, TaskResult
from oracle.tools.executor import ToolErrorKind, ToolExecutor, ToolOutcome

log = get_logger(__name__)

#: Errors worth a second attempt: the machine was busy, not the request wrong. Everything
#: else — denied, invalid args, unknown tool, approval required — fails the task, because
#: repeating the identical call cannot change any of those answers.
RETRYABLE_KINDS = frozenset({ToolErrorKind.TIMEOUT, ToolErrorKind.EXECUTION_FAILED})


def _evidence(outcome: ToolOutcome) -> dict[str, Any]:
    """What ORACLE measured about the call. The tool's own structured result is evidence
    (a tool is code, not a narrator), but the verdict and duration ride beside it so a
    reader can see *why it was allowed* as well as what it returned."""
    evidence: dict[str, Any] = {
        "tool": outcome.tool,
        "duration_ms": outcome.duration_ms,
        "rule": outcome.verdict.rule,
        "tier": str(outcome.verdict.tier),
    }
    if outcome.result is not None:
        evidence["result"] = outcome.result.model_dump(mode="json")
    if outcome.undo_id is not None:
        evidence["undo_id"] = outcome.undo_id
    return evidence


def make_tool_runner(executor: ToolExecutor) -> Any:
    """Bind an executor into a `Runner`. Constructed in the daemon and injected, so the
    scheduler still imports nothing that can execute."""

    async def run(task: Task) -> TaskResult:
        tool_id = task.spec.tool
        if not tool_id:
            # A TOOL task with no tool is a construction bug, not a runtime negotiation.
            return TaskResult(
                ok=False,
                summary="the task carries no tool to run",
                error=TaskError(
                    kind="invalid_args",
                    message="TaskSpec.tool is unset on a TOOL task",
                ),
            )
        outcome = await executor.execute(tool_id, dict(task.spec.args))
        if outcome.ok:
            return TaskResult(
                ok=True,
                summary=f"{tool_id} ok",
                evidence=_evidence(outcome),
            )
        error = outcome.error
        log.info(
            "task.tool_failed",
            task_id=task.id,
            tool=tool_id,
            kind=getattr(error, "kind", "unknown"),
        )
        return TaskResult(
            ok=False,
            summary=f"{tool_id}: {getattr(error, 'message', 'failed')}",
            evidence=_evidence(outcome),
            error=TaskError(
                kind=getattr(error, "kind", "execution_failed"),
                message=getattr(error, "message", "the tool failed"),
                detail=getattr(error, "detail", ""),
                # The executor's own `retryable` flag is authoritative where it exists;
                # the kind list is the fallback for outcomes built without one.
                retryable=bool(getattr(error, "retryable", False))
                or getattr(error, "kind", "") in RETRYABLE_KINDS,
            ),
        )

    return run
