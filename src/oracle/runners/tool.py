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

import asyncio
from typing import Any

from oracle.core.approvals import ApprovalStore, Resolution
from oracle.logsink import get_logger
from oracle.orchestration.models import Task, TaskError, TaskResult
from oracle.orchestration.scheduler import Parked
from oracle.policy.model import Decision
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


def make_tool_runner(
    executor: ToolExecutor,
    approvals: ApprovalStore | None = None,
    *,
    session_id: str | None = None,
    pre_granted: dict[str, str] | None = None,
) -> Any:
    """Bind an executor into a `Runner`. Constructed in the daemon and injected, so the
    scheduler still imports nothing that can execute.

    With an `ApprovalStore`, a call the gate wants confirmed **parks** the task rather
    than blocking a slot on a human: the runner asks, returns `Parked`, and is called
    again once the answer lands. Without one it fails the task cleanly — a graph that
    silently skipped approvals would be the worst possible reading of "unattended"."""

    #: What the first attempt learned, so the second can present it. Keyed by task id and
    #: cleared on use: an approval binds to one task and one argument digest, and a
    #: leftover id is exactly the replay the digest exists to prevent.
    #:
    #: `pre_granted` seeds it, and there is exactly one caller: a pipeline, whose single
    #: up-front card already asked about every elevated step and minted a grant for each
    #: (PIPELINES.md §3). A seeded task therefore finds its `approval_id` and never parks
    #: — which is the whole of "never a prompt mid-run". It is not a bypass: the grant is
    #: bound to the argument digest the card displayed, `execute()` recomputes that digest
    #: from the resolved arguments, and a mutated argument fails `Approval.valid_for` with
    #: a valid id in hand.
    granted: dict[str, str] = dict(pre_granted or {})
    #: Tasks that have already been through the asking. Without this, a *refused* task
    #: parks, resumes with no grant, asks again, parks again - forever, and the human who
    #: said no is asked again every few milliseconds. Asking once per task is the rule;
    #: the second attempt runs into the gate's own APPROVAL_REQUIRED and fails there,
    #: which is where the refusal is already recorded.
    asked: set[str] = set()

    async def run(task: Task) -> TaskResult | Parked:
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
        approval_id = granted.pop(task.id, None)
        if approval_id is None and approvals is not None and task.id not in asked:
            asked.add(task.id)
            parked = await _ask_for_approval(
                executor, approvals, task, tool_id, granted, session_id
            )
            if parked is not None:
                return parked
            # No approval was needed after all; nothing to remember.
            asked.discard(task.id)
        asked.discard(task.id)
        outcome = await executor.execute(tool_id, dict(task.spec.args), approval_id=approval_id)
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


async def _ask_for_approval(
    executor: ToolExecutor,
    approvals: ApprovalStore,
    task: Task,
    tool_id: str,
    granted: dict[str, str],
    session_id: str | None,
) -> Parked | None:
    """Price the call, and if the gate wants a human, ask and park.

    `preview()` is the same call the Confirmation Center makes, and it produces the digest
    the approval binds to — so what a person approves is exactly what later executes, not
    a re-render of it. Returns `None` when no approval is needed, which is the common case
    and costs one policy evaluation."""
    verdict, digest = executor.preview(tool_id, dict(task.spec.args))
    if verdict.decision is Decision.DENY or not verdict.needs_approval:
        # A denial is the executor's to report, with its audit entry: this function only
        # decides whether to wait for a person.
        return None

    pending = await approvals.request(
        tool_id,
        dict(task.spec.args),
        verdict,
        digest,
        trace_id=task.root_id,
        session_id=session_id,
        preview={"task_id": task.id, "root_id": task.root_id, "tool": tool_id},
    )

    async def wait() -> None:
        resolution = await approvals.wait(pending)
        if resolution == Resolution.APPROVED:
            granted[task.id] = pending.id
        # Anything else — refused, expired, halted — leaves `granted` empty, and the
        # second attempt runs into the gate's own APPROVAL_REQUIRED failure with the
        # reason already in the event log. The runner does not re-narrate it.

    log.info("task.awaiting_approval", task_id=task.id, tool=tool_id, approval=pending.id)
    return Parked(
        reason=f"{tool_id} needs approval",
        until=asyncio.create_task(wait(), name=f"approval:{task.id}"),
        evidence={"approval_id": pending.id, "tool": tool_id},
    )
