"""The DELEGATION runner: `DelegationService`, wrapped rather than rewritten.

The lifecycle this calls into — render the packet, preflight, price the egress at the
gate, ask the owner, cut a scrubbed worktree, run the adapter, collect, verify with
ORACLE's own diff and tests — has existed and been tested since P6-T1, and ran live
against a real plan-authored task in P6-T5. None of it changes here. This file is an
adapter: a `Task` in, a `TaskResult` out, with two responsibilities of its own.

**Evidence and claim are separated at this boundary.** `DelegationService` returns both in
one dict; a graph must not. `result_text` is what the agent *said* and goes to `claim`;
the diff stat, the test run and the exit code are what ORACLE *measured* and go to
`evidence`. Only `evidence` decides `ok`, and therefore only `evidence` gates a dependent
task. A confident agent's prose gates nothing.

**The result is harvested before the workspace can be discarded.** ORACLE commits the
worker's diff to the task's own branch — after `diff()` has been read, so what is
recorded is exactly what was judged, and under this machine's git identity. The delegate
remains forbidden from running git itself. Without this step a delegation's output lives
only as long as its checkout, which was survivable when a delegation was a whole turn and
is not survivable in a graph, where task C's output is task D's input.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from oracle.delegation.service import DelegationService, Outcome, PacketInputs
from oracle.integrations.types import HandoffPacket
from oracle.logsink import get_logger
from oracle.orchestration.models import Cost, Task, TaskError, TaskResult

log = get_logger(__name__)

#: Keys of `ActiveDelegation.result` that are ORACLE's own measurements. Anything not
#: listed is either the agent's claim or bookkeeping, and neither gates anything.
EVIDENCE_KEYS = (
    "exit_code",
    "diff_lines",
    "untracked",
    "tests",
    "workspace",
    "branch",
    "num_turns",
    "structured",
)


def packet_from(task: Task, *, allowed_tools: tuple[str, ...]) -> HandoffPacket:
    """A `TaskSpec` rendered as the vendor-neutral packet the adapters already speak
    (PLANNER.md §3: the packet is the *rendered form* of a spec, not a rival to it)."""
    return HandoffPacket(
        task_id=task.id,
        task=task.spec.objective,
        acceptance=task.spec.acceptance,
        constraints=task.spec.constraints,
        allowed_tools=allowed_tools,
    )


def make_delegation_runner(
    service: DelegationService,
    source_repo: Path,
    *,
    allowed_tools: tuple[str, ...] = ("Read",),
    harvest: bool = True,
    inputs_for: Any = None,
) -> Any:
    """Bind the delegation lifecycle into a `Runner`.

    `inputs_for` is an optional `async (Task) -> PacketInputs`. Without it a graph task's
    packet is the objective and nothing else, which is what P7-T2 shipped; with it the
    packet carries **prior attempts at this task** (MEMORY.md §4), which is the whole
    reason memory exists for a delegation-oriented agent. Injected rather than imported
    because this file must not know what a `MemoryStore` is."""

    async def run(task: Task) -> TaskResult:
        packet = packet_from(task, allowed_tools=allowed_tools)
        inputs = await inputs_for(task) if inputs_for is not None else PacketInputs()
        active = await service.run(packet, source_repo, inputs)
        raw: dict[str, Any] = dict(active.result or {})
        claim = str(raw.get("result_text") or "") or None
        evidence = {key: raw[key] for key in EVIDENCE_KEYS if key in raw}
        evidence["outcome"] = active.outcome

        # Not gated on `diff_lines`: that counts *tracked* changes, and a worker whose
        # output is new files — a new module, a new test, a recorded fixture — produces
        # none. `harvest()` decides for itself whether anything was staged and returns
        # None when nothing was, which is the check that cannot get this wrong.
        if harvest and active.worktree is not None:
            # Off the loop: git touches the disk, and the P5-T2 watcher taught what a
            # blocking call on the event loop costs.
            sha = await asyncio.to_thread(
                active.worktree.harvest, f"{task.id}: worker output, harvested by ORACLE"
            )
            if sha is not None:
                # The sha is how a dependent task, a reviewer, or a merge finds the work
                # after the checkout is gone.
                evidence["harvest_commit"] = sha
                log.info("task.harvested", task_id=task.id, commit=sha[:12])

        ok = active.outcome == Outcome.SUCCESS
        cost = Cost(usd=raw.get("cost_usd")) if raw.get("cost_usd") is not None else None
        if ok:
            return TaskResult(
                ok=True,
                summary=f"delegation succeeded ({evidence.get('diff_lines', 0)} diff lines)",
                evidence=evidence,
                claim=claim,
                cost=cost,
            )
        return TaskResult(
            ok=False,
            summary=f"delegation {active.outcome}",
            evidence=evidence,
            claim=claim,
            cost=cost,
            error=TaskError(
                kind=str(active.outcome or "failed"),
                message=str(raw.get("explanation") or raw.get("error") or active.outcome or ""),
                # A refused or expired egress is a human decision and a missing vendor is
                # a routing fact; neither becomes true by asking again. A crashed run
                # might, but `max_attempts` for a delegation is 1 by policy anyway — the
                # flag is here so that stays a policy decision rather than an accident.
                retryable=active.outcome == Outcome.FAILED,
            ),
        )

    return run
