"""Replanning: a failure buys one more idea, not an afternoon (ORCHESTRATION.md §4).

A task failed. Sometimes that is a fact about the world — the owner said no, HALT
happened, the vendor is gone — and there is nothing to route around. Sometimes it is the
commonest real failure there is: the worker misunderstood, or the context was wrong. That
second case a supervisor can act on, **once**, without a human.

This module is the *decision*, and nothing else. It has no I/O, imports no planner, and
calls nothing: `consider()` looks at a failed task and the rows beside it and answers
"is there a replan here, and if not, why not". The composition layer
(`runners/planning.py`) takes that answer and spends money on it. Keeping the two apart
is what stops `scheduler.py` from growing a planner-shaped hole in it.

Four rules, each of which exists because its absence is a known failure mode:

* **Append-only** (ADR-0020). The failed task keeps its row, its evidence and its place.
  The replacement points at it with `supersedes` and records lineage in `parent_id`.
  Nothing is rewritten, because the event log does not rewrite and a task table that
  disagreed with the event log would be a second, quieter source of truth.
* **A human decision is not a problem to route around.** A refused egress, an expired
  approval, a HALT, a policy denial: replanning any of those is an agent looking for a
  door that was just closed. The budget is spent on misunderstandings, not on refusals.
* **One replan per failure, two per root.** Unbounded replanning is how an agent burns an
  afternoon achieving nothing. The budget is counted in one place — here — from the rows,
  so it cannot drift from what actually happened.
* **Evidence, never the claim.** The planner is told what ORACLE *measured*. A worker's
  prose is untrusted text (ADR-0021), and feeding "I already fixed it" back into the thing
  that authors the next task is how a confident agent talks a supervisor into a loop.

What a replan is *not* allowed to do — resurrect anything. A dependent that was `SKIPPED`
stays `SKIPPED`; if that work is still wanted, the replacement plan has to say so itself.
The skipped rows are named in the failure context precisely so it can.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from oracle.logsink import get_logger
from oracle.orchestration.models import Task, TaskKind, TaskStatus

log = get_logger(__name__)

#: ORCHESTRATION.md §4, matching the turn critic's budget. Two, per root, counted from
#: the rows rather than from a counter somebody has to remember to increment.
REPLAN_BUDGET = 2

#: Statuses a replan may answer. `SKIPPED` is not one of them — a skipped task is the
#: *shape* of a failure that already happened, and replanning both the failure and its
#: cascade would spend the budget several times on one event.
REPLANNABLE: frozenset[TaskStatus] = frozenset({TaskStatus.FAILED, TaskStatus.TIMEOUT})

#: Error kinds that mean **a person, or a policy a person wrote, decided this**. None of
#: them is a misunderstanding, so none of them is replanned. `interrupted` is on the list
#: for a different reason: recovery has already gated it, and a supervisor that could not
#: prove what a child did while it was dead does not get to author its replacement
#: (ORCHESTRATION.md §3, "Crash recovery").
HUMAN_DECISION_KINDS: frozenset[str] = frozenset(
    {
        "denied",
        "refused",
        "expired",
        "halted",
        "cancelled",
        "approval_required",
        "approval_invalid",
        "interrupted",
    }
)

#: Kinds that are never replanned. A failed `PLANNING` task answered by another planning
#: call is the loop this whole module is bounded to prevent.
UNREPLANNABLE_KINDS: frozenset[TaskKind] = frozenset({TaskKind.PLANNING})


class Attempt(BaseModel):
    """One thing that was already tried, as ORACLE saw it. This is what makes the
    planner's second idea different from its first."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    role: str
    objective: str
    status: str
    #: ORACLE's measurements. The worker's `claim` is deliberately absent — see the
    #: module docstring, and `tests/security/test_replan_authority.py`.
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class ReplanRequest(BaseModel):
    """The supervisor asking for one more idea, with the failure attached.

    Carries everything the planner is allowed to know and nothing it is not: the original
    objective, what failed and how ORACLE measured it, what never ran as a result, and
    what has already been tried on this root."""

    model_config = ConfigDict(frozen=True)

    root_id: str
    #: The failed task this replan answers. Its id becomes `supersedes` on every row the
    #: replan produces.
    failed_id: str
    objective: str
    failed: Attempt
    #: Tasks that never ran because this one failed. Named so the replacement plan can
    #: re-author the work; **not** resurrected, because a `SKIPPED` row is a fact.
    skipped: tuple[Attempt, ...] = ()
    #: Everything else that already failed under this root, including earlier replans.
    prior: tuple[Attempt, ...] = ()
    replans_used: int = 0
    budget: int = REPLAN_BUDGET

    @property
    def attempt_number(self) -> int:
        """1 for the first replan on this root, 2 for the second. There is no third."""
        return self.replans_used + 1


def _attempt(task: Task) -> Attempt:
    result = task.result
    return Attempt(
        task_id=task.id,
        role=task.spec.role,
        objective=task.spec.objective,
        status=str(task.status),
        evidence=dict(result.evidence) if result else {},
        error=result.error.message
        if result and result.error
        else (result.summary if result else ""),
    )


def budget_used(tasks: list[Task]) -> int:
    """How many replans this root has already spent.

    Counted as **distinct superseded tasks**, not as rows: one replan authoring three
    replacement tasks is one replan. Reading it from the rows rather than from a counter
    means a restarted daemon, a reconnecting client and this function all agree, and
    there is nothing to forget to increment."""
    return len({task.supersedes for task in tasks if task.supersedes})


def already_replanned(failed_id: str, tasks: list[Task]) -> bool:
    """One replan per failure. A second replacement for the same row would be the budget
    being spent twice on one mistake."""
    return any(task.supersedes == failed_id for task in tasks)


def consider(
    failed: Task, tasks: list[Task], *, objective: str
) -> tuple[ReplanRequest | None, str]:
    """Is there a replan here? Returns the request, or the reason there is not.

    Both halves matter. The reason is not decoration: "the budget is spent" and "you
    refused this" are different things for a person to read, and a supervisor that
    reported them as one silence would be unaccountable in exactly the situation where
    accountability is the point."""
    if failed.status not in REPLANNABLE:
        return None, f"{failed.id} is {failed.status}, which is not a failure to replan"
    if failed.kind in UNREPLANNABLE_KINDS:
        return None, f"a {failed.kind} task is not replanned; that is the loop, not the fix"
    error = failed.result.error if failed.result else None
    if error is not None and error.kind in HUMAN_DECISION_KINDS:
        # The load-bearing branch. A refusal is a decision, and a supervisor that
        # replanned around it would be asking the same question with a new face on it.
        return None, f"{failed.id} ended in {error.kind}: a decision, not a problem to route around"
    if already_replanned(failed.id, tasks):
        return None, f"{failed.id} has already been replanned once"
    used = budget_used(tasks)
    if used >= REPLAN_BUDGET:
        return None, f"the replan budget for {failed.root_id} is spent ({used}/{REPLAN_BUDGET})"

    skipped = [t for t in tasks if t.status is TaskStatus.SKIPPED]
    prior = [
        t
        for t in tasks
        if t.id != failed.id and t.status in REPLANNABLE and t.kind not in UNREPLANNABLE_KINDS
    ]
    return (
        ReplanRequest(
            root_id=failed.root_id,
            failed_id=failed.id,
            objective=objective,
            failed=_attempt(failed),
            skipped=tuple(_attempt(t) for t in skipped),
            prior=tuple(_attempt(t) for t in prior),
            replans_used=used,
        ),
        "",
    )


def attach(new_tasks: list[Task], *, failed: Task, plan_id: str) -> list[Task]:
    """Stamp a freshly compiled plan as the replacement for `failed`.

    Every task in the batch carries `supersedes` and `parent_id`, because a replan
    replaces the failed task *collectively* — the planner may answer one bad task with a
    research step and a narrower coding step, and pretending one of them is "the"
    replacement would be a lineage that reads well and is false.

    The ids are already distinct (`compile_plan(..., id_prefix=…)`); this touches lineage
    only, so the graph it joins re-validates exactly as it would for a first plan."""
    return [
        task.model_copy(
            update={
                "supersedes": failed.id,
                # Lineage, not execution order. Nothing schedules on `parent_id`.
                "parent_id": failed.id,
                "plan_id": plan_id,
            }
        )
        for task in new_tasks
    ]


def attempts_report(tasks: list[Task]) -> dict[str, Any]:
    """Everything that was tried under this root, for the moment the budget runs out.

    ORCHESTRATION.md §4: an exhausted budget fails the root *with a report of everything
    tried*, and — where a partial result exists in a worktree — the keep/discard decision
    the delegation flow already offers. So the branches and workspaces are named here:
    a report that says "it failed three times" without saying where the work went is a
    report that throws the work away."""
    attempts = [_attempt(t) for t in tasks if t.status in REPLANNABLE]
    partials = [
        {
            "task_id": task.id,
            "branch": task.result.evidence.get("branch"),
            "workspace": task.result.evidence.get("workspace"),
            "harvest_commit": task.result.evidence.get("harvest_commit"),
        }
        for task in tasks
        if task.result is not None
        and (task.result.evidence.get("branch") or task.result.evidence.get("workspace"))
    ]
    return {
        "replans_used": budget_used(tasks),
        "budget": REPLAN_BUDGET,
        "attempts": [a.model_dump() for a in attempts],
        "skipped": [t.id for t in tasks if t.status is TaskStatus.SKIPPED],
        # Deliberately not cleaned up. An interrupted or failed delegation may have left
        # real work, and `harvest()` exists because such work is worth something.
        "partial_results": partials,
    }
