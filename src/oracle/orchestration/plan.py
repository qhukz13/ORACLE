"""`ExecutionPlan`: what a planner returns, and what it is allowed to say (PLANNER.md §2).

These models were written and measured in the P6-T5 spike against a real vendor before
they were trusted here (`scripts/verify_agy_planning.py`,
`logs/development/2026-08-24-p6t5-antigravity-planning.md`). Four findings from that run
are structural rather than advisory, and each is a line of code below:

* **A plan may not name a tool.** `extra="forbid"`: `TaskSpec.tool`/`args` exist for the
  supervisor's own TOOL tasks, and a plan that could set them would be a plan with
  execution authority — the one thing ADR-0021 says a plan never has. A planner that
  invents *any* field is rejected rather than quietly trimmed, because "trimmed" is how
  `tool` would have arrived.
* **Check 0 is non-empty.** A vendor returned `status: SUCCESS` with a schema-valid plan
  whose `tasks` array had been silently emptied by its own filter, while the prose beside
  it held six well-formed tasks. A schema-shaped answer is not a validated answer.
* **Enums, not ranges** (ADR-0017): `role` and `expected_outcome` are `Literal`s the
  decoder can enforce; `len(tasks) <= 12` is checked here, in code.
* **Acceptance criteria are a hint for the worker, never the verification contract.**
  Planners write criteria naming files that do not exist yet. They are required for a
  `diff` outcome because an unverifiable coding task is not schedulable — but what
  actually gates the task is ORACLE's own baseline comparison (`runners/verify.py`).

Validation returns *every* problem, not the first: the repair attempt is fed the specific
errors, and one error per round trip would cost a round trip per typo.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from oracle.core.events import new_id
from oracle.logsink import get_logger
from oracle.orchestration.graph import MAX_GRAPH_SIZE, TaskGraph
from oracle.orchestration.models import DEFAULT_MAX_ATTEMPTS, Task, TaskKind, TaskSpec
from oracle.orchestration.registry import Registry

log = get_logger(__name__)

Outcome = Literal["diff", "report", "answer", "verdict"]

#: Which kind of task each outcome becomes. A plan describes *what it wants back*; the
#: supervisor decides *how* that is produced, which is why `expected_outcome` maps to a
#: `TaskKind` here and not in the plan.
OUTCOME_KIND: dict[str, TaskKind] = {
    "diff": TaskKind.DELEGATION,
    "report": TaskKind.DELEGATION,
    "answer": TaskKind.DELEGATION,
    "verdict": TaskKind.VERIFY,
}


class PlannedTask(BaseModel):
    # Forbid, not ignore. See the module docstring: this is the line that keeps a plan
    # from naming a tool.
    model_config = ConfigDict(extra="forbid")

    id: str
    role: str
    objective: str
    project: str | None = None
    acceptance: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    #: What the context engine should fetch — queries, paths, doc names. Hints, not
    #: contents: a plan carrying context would be a plan whose egress was never previewed.
    context_hints: list[str] = Field(default_factory=list)
    #: A recommendation. ORACLE decides (PLANNER.md §5), and this never overrides policy,
    #: the registry, or availability.
    agent_hint: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    expected_outcome: Outcome


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str
    summary: str
    tasks: list[PlannedTask]
    #: What the planner is unsure about. Rendered on the approval card, never acted on.
    risks: list[str] = Field(default_factory=list)


def plan_schema() -> dict[str, Any]:
    """The JSON Schema handed to the vendor. Generated from the models, never
    hand-written (AGENTS.md), so the thing validated and the thing requested cannot drift."""
    return ExecutionPlan.model_json_schema()


# -- validation ----------------------------------------------------------------


def validate(plan: ExecutionPlan, registry: Registry, projects: set[str]) -> list[str]:
    """Every problem with this plan, in PLANNER.md §2's order. Empty means schedulable."""
    problems: list[str] = []

    # 0 — non-empty. First because a silently emptied collection passes every other check.
    if not plan.tasks:
        problems.append("the plan has no tasks")
        return problems

    ids = [t.id for t in plan.tasks]
    if len(ids) != len(set(ids)):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        problems.append(f"duplicate task ids: {', '.join(duplicates)}")
    if len(plan.tasks) > MAX_GRAPH_SIZE:
        problems.append(
            f"{len(plan.tasks)} tasks exceeds the limit of {MAX_GRAPH_SIZE}; "
            "split the objective instead"
        )

    known = set(ids)
    for task in plan.tasks:
        for dep in task.depends_on:
            if dep not in known:
                problems.append(f"{task.id}.depends_on names unknown task {dep!r}")
            if dep == task.id:
                problems.append(f"{task.id} depends on itself")
        if task.role not in registry.roles:
            problems.append(
                f"{task.id}.role {task.role!r} is not a registered role "
                f"(known: {', '.join(sorted(registry.roles))})"
            )
        elif not registry.holders_of(task.role):
            problems.append(f"{task.id}.role {task.role!r} has no agent that can hold it")
        if task.project is not None and task.project not in projects:
            # A hallucinated project name must never become a path.
            problems.append(f"{task.id}.project {task.project!r} is not a known project")
        if task.agent_hint is not None and task.agent_hint not in registry.agents:
            problems.append(f"{task.id}.agent_hint {task.agent_hint!r} is not a registered agent")
        if task.expected_outcome == "diff" and not task.acceptance:
            problems.append(
                f"{task.id} expects a diff with no acceptance criteria; "
                "an unverifiable coding task is not schedulable"
            )

    cycle = _find_cycle(plan.tasks)
    if cycle:
        problems.append("cycle: " + " → ".join(cycle))
    return problems


def _find_cycle(tasks: list[PlannedTask]) -> tuple[str, ...]:
    """The cycle as a path. Iterative, because the input came from a vendor and a vendor
    is an untrusted source of graph depth (ADR-0021)."""
    edges = {t.id: list(t.depends_on) for t in tasks}
    colour: dict[str, int] = {}
    for start in edges:
        if colour.get(start, 0) == 2:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, index = stack.pop()
            if index == 0:
                if colour.get(node, 0) == 2:
                    continue
                colour[node] = 1
                path.append(node)
            if index < len(edges.get(node, ())):
                stack.append((node, index + 1))
                nxt = edges[node][index]
                if colour.get(nxt, 0) == 1:
                    return (*path[path.index(nxt) :], nxt)
                if colour.get(nxt, 0) == 0 and nxt in edges:
                    stack.append((nxt, 0))
            else:
                colour[node] = 2
                if path and path[-1] == node:
                    path.pop()
    return ()


def parse(raw: Any) -> tuple[ExecutionPlan | None, list[str]]:
    """Turn whatever came back into a plan, or into the errors a repair attempt is fed.

    Accepts the structured field or a JSON string, because vendors differ on which they
    fill — but never prose around JSON: a planner that wrapped its answer in commentary
    is a planner whose next answer is worth asking for, not one worth guessing at."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, [f"the response is not JSON: {exc}"]
    if not isinstance(raw, dict):
        return None, ["the response is not a JSON object"]
    try:
        return ExecutionPlan.model_validate(raw), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in error['loc']) or 'plan'}: {error['msg']}"
            for error in exc.errors()[:10]
        ]


def repair_prompt(problems: list[str]) -> str:
    """The one repair attempt, told exactly what was wrong (the ADR-0017 pattern).

    One attempt, then the ladder. A second would be an agentic loop wearing a budget's
    clothes, and unbounded replanning is how an agent burns an afternoon achieving
    nothing."""
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        "Your plan was rejected by validation. Fix exactly these problems and return the "
        "corrected plan as JSON matching the same schema. Change nothing else.\n\n"
        f"{listed}"
    )


# -- compilation ---------------------------------------------------------------


def compile_plan(
    plan: ExecutionPlan,
    registry: Registry,
    *,
    root_id: str | None = None,
    plan_id: str | None = None,
    id_prefix: str | None = None,
) -> TaskGraph:
    """A validated plan becomes rows. Call `validate()` first — this assumes it passed.

    Plan-local ids (`"A"`, `"B"`) become real task ids, and `depends_on` is remapped with
    them: the plan's namespace is the plan's, and letting it leak into the task table
    would make two plans' `"A"` the same row.

    `id_prefix` exists for exactly one caller: a **replan**, whose tasks join a graph that
    already holds `{root}-a`. It changes the id namespace without changing `root_id`,
    because the replacement belongs to the same root — that is what makes it visible in
    the same tree — and only its *name* has to be new.

    The agent is **resolved here, by the registry**, not taken from the plan. `agent_hint`
    breaks ties and nothing more (PLANNER.md §5): a plan that could choose its executor
    would be choosing its own permissions.
    """
    root = root_id or new_id("tk")
    prefix = id_prefix or root
    ids = {task.id: f"{prefix}-{task.id}".lower() for task in plan.tasks}
    tasks: list[Task] = []
    for planned in plan.tasks:
        kind = OUTCOME_KIND.get(planned.expected_outcome, TaskKind.DELEGATION)
        agent = _resolve_agent(planned, registry)
        tasks.append(
            Task(
                id=ids[planned.id],
                root_id=root,
                kind=kind,
                plan_id=plan_id,
                agent=agent,
                spec=TaskSpec(
                    objective=planned.objective,
                    role=planned.role,
                    project=planned.project,
                    acceptance=tuple(planned.acceptance),
                    constraints=tuple(planned.constraints),
                    expected_outcome=planned.expected_outcome,
                    # Never set from a plan. Stated here as an assertion of intent as much
                    # as a default: this is the field ADR-0021 is about.
                    tool=None,
                    args={},
                ),
                depends_on=tuple(ids[d] for d in planned.depends_on if d in ids),
                max_attempts=DEFAULT_MAX_ATTEMPTS.get(kind, 1),
            )
        )
    log.info("plan.compiled", root_id=root, tasks=len(tasks), plan_id=plan_id)
    return TaskGraph(tasks)


def _resolve_agent(planned: PlannedTask, registry: Registry) -> str | None:
    """Deterministic rules first, the hint only as a tiebreak, and never a name the
    registry does not hold."""
    holders = registry.holders_of(planned.role)
    if not holders:
        return None
    if planned.agent_hint and registry.role_can_be_held_by(planned.role, planned.agent_hint):
        return planned.agent_hint
    return holders[0].id


def elevated_summary(tasks: Iterable[Task]) -> list[dict[str, Any]]:
    """What the graph approval card lists: every task whose tier is knowable *now*.

    Delegations are the interesting entry — each is an egress, and each still asks its own
    question later, because an egress approval binds to rendered bytes that do not exist
    yet (SECURITY.md §10). Listing them here is the pipeline rule: the shape of the whole
    thing up front, so a person is not asked twelve questions they cannot compare.

    Takes tasks rather than a graph because a **replan's card lists only the additions**:
    re-showing the whole graph for two new rows is how a person learns to click through
    the card without reading it.
    """
    return [
        {
            "task_id": task.id,
            "kind": str(task.kind),
            "role": task.spec.role,
            "agent": task.agent,
            "objective": task.spec.objective,
            "project": task.spec.project,
            "egresses": task.kind is TaskKind.DELEGATION,
        }
        for task in tasks
    ]
