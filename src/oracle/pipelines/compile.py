"""A pipeline becomes rows (PIPELINES.md §3, ORCHESTRATION.md §2).

> *"A pipeline compiles to a task graph."* — the 2026-08-24 replan

So there is no pipeline executor here, and there is not going to be one. Every step
becomes exactly one `Task` of `kind=TOOL`, which `runners.tool` runs through the same
policy gate as a tool call a person typed. That is what makes the roadmap's *"no second
way to run an agent"* literally true rather than aspirational, and it is why the roadmap's
extra acceptance criterion — *a pipeline run and a hand-written graph of the same steps
produce identical event shapes* — is a property of this file alone.

Two design decisions live here and both are visible in the output rather than in prose.

**`on_failure` is edge construction, not a runtime concept.** The scheduler has exactly
one rule about dependencies (`ready()` requires every dependency `SUCCEEDED`) and it is
fail-closed on purpose. Rather than teach it a second kind of edge, a step's `on_failure`
decides whether anything is allowed to depend on it:

* `abort` — the step becomes the barrier the next step waits on. Its failure cascades,
  because `_cascade_skips()` skips everything downstream of a failure.
* `continue` — nothing depends on it. It is a leaf hanging off the previous barrier, so
  its failure reaches nothing and the run carries on, which is what "record and proceed"
  means.

The visible consequence, stated rather than hidden: **a `continue` step and its successor
may run at the same time**, both being in the `tool` slot class. That is a deliberate v1
acceptance. The alternative is a completion-only edge in `graph.ready()`, which means
touching the fail-closed rule the whole graph algebra rests on — a P7-core change, and not
one to make on speculation about a pipeline nobody has written yet.

**`when: false` omits a step; it does not compile it as `SKIPPED`.** `TaskStatus.SKIPPED`
means *an ancestor failed and this never ran*. Reusing it for *the author's condition was
false* would put a lie in the one field the UI reads to explain a run. An omitted step is
removed before the barrier walk, so its neighbours join up, and it is reported separately
so the approval card can show what the parameters removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oracle.core.events import new_id
from oracle.logsink import get_logger
from oracle.orchestration.graph import TaskGraph
from oracle.orchestration.models import DEFAULT_MAX_ATTEMPTS, Task, TaskKind, TaskSpec
from oracle.pipelines.models import OnFailure, Pipeline
from oracle.pipelines.template import PipelineError, evaluate, scope_for, substitute

log = get_logger(__name__)

#: The role every pipeline step holds. Named in `config/agents.yaml` as a deterministic
#: role that no agent holds, so `resolve_agent` returns `None` and a *planner* cannot
#: author one: a pipeline step is work ORACLE itself performs, not work it delegates.
PIPELINE_ROLE = "operator"


@dataclass(frozen=True)
class RenderedStep:
    """One step with its parameters bound and its references resolved."""

    id: str
    tool: str
    args: dict[str, Any]
    timeout_s: float | None
    on_failure: OnFailure
    max_attempts: int


@dataclass(frozen=True)
class Rendered:
    """What a pipeline plus its parameters actually amounts to, before pricing."""

    steps: tuple[RenderedStep, ...]
    #: `(step_id, reason)` for each step a `when:` removed. Shown on the approval card:
    #: a person deciding whether to authorise a run needs to see what will *not* happen
    #: as much as what will.
    omitted: tuple[tuple[str, str], ...]


def render(pipeline: Pipeline, params: dict[str, Any], *, project_root: str | None) -> Rendered:
    """Bind parameters, evaluate conditions, substitute references.

    Everything that can fail here fails **before the graph exists**, which is
    PIPELINES.md §3's *"a typo in step 5 must not be discovered after step 4 has already
    pushed a branch"*. Raises `PipelineError`; the caller turns that into a `Problem`.
    """
    scope = scope_for(params, pipeline.project, project_root)
    steps: list[RenderedStep] = []
    omitted: list[tuple[str, str]] = []

    for step in pipeline.steps:
        if step.when is not None and not evaluate(step.when, scope):
            omitted.append((step.id, f"when: {step.when}"))
            continue
        args: dict[str, Any] = {}
        for key, value in step.with_.items():
            # Only strings carry references. An int or a bool is already a value, and
            # running `substitute` over `True` would be a type error dressed as a feature.
            # A list is substituted element-wise, so `args: ["run", "{{ params.suite }}"]`
            # works and nothing has to know which position a reference is in.
            if isinstance(value, str):
                args[key] = substitute(value, scope)
            elif isinstance(value, list):
                args[key] = [substitute(v, scope) for v in value]
            else:
                args[key] = value
        steps.append(
            RenderedStep(
                id=step.id,
                tool=step.tool,
                args=args,
                timeout_s=float(step.timeout) if step.timeout else None,
                on_failure=step.on_failure,
                max_attempts=(
                    step.retry.max + 1
                    if step.retry is not None
                    else DEFAULT_MAX_ATTEMPTS.get(TaskKind.TOOL, 1)
                ),
            )
        )

    if not steps:
        raise PipelineError("every step was removed by its condition; there is nothing to run")
    return Rendered(tuple(steps), tuple(omitted))


def compile_pipeline(
    rendered: Rendered,
    pipeline: Pipeline,
    *,
    root_id: str | None = None,
) -> TaskGraph:
    """Rendered steps become a `TaskGraph`. Call `render()` first, and price it after.

    Ids are namespaced by the root for the same reason `compile_plan` namespaces plan-local
    ids: two runs of the same pipeline must not be the same rows.
    """
    root = root_id or new_id("tk")
    tasks: list[Task] = []
    barrier: str | None = None

    for step in rendered.steps:
        task_id = f"{root}-{step.id}".lower()
        tasks.append(
            Task(
                id=task_id,
                root_id=root,
                kind=TaskKind.TOOL,
                spec=TaskSpec(
                    objective=f"{step.tool} ({pipeline.name}/{step.id})",
                    role=PIPELINE_ROLE,
                    project=pipeline.project,
                    # Set by the supervisor from a file a human wrote, which is the one
                    # thing `TaskSpec.tool` is reserved for (ADR-0021). A *plan* still
                    # cannot populate it, and a security test says so.
                    tool=step.tool,
                    args=step.args,
                ),
                depends_on=(barrier,) if barrier else (),
                max_attempts=step.max_attempts,
                timeout_s=step.timeout_s,
            )
        )
        if step.on_failure == "abort":
            barrier = task_id

    log.info(
        "pipeline.compiled",
        name=pipeline.name,
        root_id=root,
        steps=len(tasks),
        omitted=len(rendered.omitted),
    )
    return TaskGraph(tasks)
