"""The task model (ORCHESTRATION.md §2).

A task is a durable, cancellable unit of work with dependencies and an independently
verified result. It generalises what `DelegationService` already manages for exactly one
delegation — which is why `TaskResult` splits **evidence** from **claim**: an agent
saying "tests pass" is a claim, ORACLE running the tests is evidence, and only evidence
gates a dependent task (INTEGRATIONS.md §7).

Two vocabulary distinctions are load-bearing and are not to be collapsed under
implementation pressure. Each has a test that fails if it is:

* `TIMEOUT ≠ FAILED` — a timed-out worker may well have done the work, so its worktree is
  still diffed before anything is thrown away.
* `SKIPPED ≠ CANCELLED` — "an ancestor failed, so this never ran" and "a person stopped
  this" are different facts, and a graph that reports them as one loses the only signal
  that tells an abandoned run from a broken one.

`TaskSpec` here is the P7 subset of [PLANNER.md §3](../../docs/PLANNER.md): objective,
role, acceptance, constraints, outcome. `context`, `attempts` and `security` are named in
that document and deliberately absent from this file — they arrive with the context
engine (P9) and the planner (P8), and a field nothing populates is a promise, not a model.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from oracle.core.events import now_iso


class TaskKind(StrEnum):
    TOOL = "tool"
    DELEGATION = "delegation"
    PLANNING = "planning"
    VERIFY = "verify"
    REPORT = "report"


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    WAITING = "waiting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


#: A task in one of these is finished: the scheduler will not touch it again, and the
#: graph is complete when every task is in one of them.
TERMINAL: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.TIMEOUT,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
    }
)
#: Aggregate precedence for the root task and the UI (ORCHESTRATION.md §3). Read as
#: "the worst thing that happened to this graph": one cancelled task makes the graph
#: cancelled, however many others succeeded.
AGGREGATE_ORDER: tuple[TaskStatus, ...] = (
    TaskStatus.CANCELLED,
    TaskStatus.FAILED,
    TaskStatus.TIMEOUT,
    TaskStatus.RUNNING,
    TaskStatus.WAITING,
    TaskStatus.READY,
    TaskStatus.PENDING,
    TaskStatus.SKIPPED,
    TaskStatus.SUCCEEDED,
)
#: From policy, not from a caller: a delegation is minutes long and costs quota, so it
#: gets one attempt; cheap deterministic work gets two.
DEFAULT_MAX_ATTEMPTS: dict[TaskKind, int] = {
    TaskKind.TOOL: 2,
    TaskKind.VERIFY: 2,
    TaskKind.REPORT: 2,
    TaskKind.PLANNING: 1,
    TaskKind.DELEGATION: 1,
}


class Cost(BaseModel):
    model_config = ConfigDict(frozen=True)
    tokens: int | None = None
    usd: float | None = None


class TaskError(BaseModel):
    """The persisted shape of a failure. Mirrors `ToolError`'s fields rather than
    importing it: the orchestration layer must not depend on the tool-execution layer
    (ARCHITECTURE.md), and a runner adapts its own errors into this."""

    model_config = ConfigDict(frozen=True)
    kind: str
    message: str
    detail: str = ""
    #: Whether *this* error may be retried. A policy denial never is — retrying a denial
    #: is how an agent nags a person into approving something.
    retryable: bool = False


class TaskSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    objective: str
    role: str
    project: str | None = None
    acceptance: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    expected_outcome: str = "report"
    #: `TOOL` tasks only: the invocation, exactly as `ToolExecutor` expects it.
    #:
    #: **Set by the supervisor, never by a plan.** A plan that could name a tool and its
    #: arguments would be a plan with execution authority, which is the one thing
    #: ADR-0021 says a plan must never have. Plans author worker tasks; tool tasks come
    #: from templates and from ORACLE's own decisions. P8's plan validation rejects a
    #: `PlannedTask` that tries to set these — noted here because the field exists before
    #: the validation that guards it.
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    """`evidence` is what ORACLE measured; `claim` is what the worker said. They are
    separate fields and never merged, because the moment they are, a confident agent's
    prose starts gating dependent tasks."""

    model_config = ConfigDict(frozen=True)
    ok: bool
    summary: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    claim: str | None = None
    cost: Cost | None = None
    error: TaskError | None = None


class Task(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    root_id: str
    kind: TaskKind
    spec: TaskSpec
    status: TaskStatus = TaskStatus.PENDING
    parent_id: str | None = None
    #: The ExecutionPlan that authored this task, once plans exist (P8). Carried now so
    #: the audit chain can answer "which plan asked for this" without a migration later.
    plan_id: str | None = None
    #: Resolved executor id; None until assignment (PLANNER.md §5).
    agent: str | None = None
    depends_on: tuple[str, ...] = ()
    attempt: int = 1
    max_attempts: int = 1
    #: The failed task this one replaces. Replanning is append-only: nothing is rewritten,
    #: and the UI shows the failed attempt beside its replacement.
    supersedes: str | None = None
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    result: TaskResult | None = None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    def with_status(self, status: TaskStatus, **fields: Any) -> Task:
        """Transitions are copies, because `Task` is frozen: the row is the record, and
        a mutated object in someone's local variable is not a record of anything."""
        stamp: dict[str, Any] = {"status": status}
        if status is TaskStatus.RUNNING and self.started_at is None:
            stamp["started_at"] = now_iso()
        if status in TERMINAL:
            stamp["finished_at"] = now_iso()
        return self.model_copy(update={**stamp, **fields})


def aggregate(statuses: list[TaskStatus]) -> TaskStatus:
    """The graph's status, by precedence. An empty graph is `SUCCEEDED` by vacuity —
    the alternative is inventing a tenth state for a case the scheduler already rejects
    at validation."""
    for candidate in AGGREGATE_ORDER:
        if candidate in statuses:
            return candidate
    return TaskStatus.SUCCEEDED
