"""Pricing a pipeline, asking about it once, and running it (PIPELINES.md §3).

`oracle.pipelines` is pure — it can parse and compile but it cannot find out what a step
would *cost*, because that needs the tool registry and the policy gate. This module is
where the two meet, and it lives beside `runners/planning.py` for the same reason: it is
composition, above the boundary the compiler stays below.

Everything here serves one sentence from PIPELINES.md §3:

> *"Approval is up front, once. Being interrupted at step 3 of 6 to approve something is
> exactly the prompt fatigue the security model tries to avoid."*

That sentence is also the most dangerous idea in Phase 10, because "approve six things at
once" and "rubber-stamp six things at once" are the same gesture. Five constraints keep
them apart, and each is a security test:

1. **Every elevated step is on the card, with its resolved arguments** — not a count, not
   a summary. SECURITY.md §2 rule 5: confirm actions, not intentions.
2. **A grant is bound to the digest the card showed.** `executor.preview()` computes it
   from *canonicalised* arguments, and `execute()` recomputes it, so a mutated argument
   fails `Approval.valid_for` even with a valid id.
3. **A grant is single use and per task.** Two identical steps get two grants.
4. **T3 never reaches a card.** It is refused at validation: T3 needs the desktop and a
   phrase typed for that invocation, and batching one would launder `confirm_strong` into
   `confirm`.
5. **Grants are revoked when the run ends**, whatever ended it. A grant outliving its run
   is a grant nobody is watching.

And one that is about where the file came from rather than what it says: a pipeline
discovered under `<project>/.oracle/pipelines/` is **repository content**, the same trust
class as a checked-in `AGENTS.md`, so it carries `local_foreign` provenance and the gate
escalates it. A cloned repository cannot ship an unattended pipeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oracle.core.approvals import ApprovalStore, Resolution
from oracle.core.events import new_id
from oracle.logsink import get_logger
from oracle.orchestration.graph import TaskGraph
from oracle.orchestration.models import TaskStatus
from oracle.pipelines.compile import Rendered, RenderedStep, compile_pipeline, render
from oracle.pipelines.loader import Loaded, Problem, Source, bind_params
from oracle.pipelines.template import PipelineError
from oracle.policy.engine import PolicyEngine
from oracle.policy.model import Decision, Provenance, Tier
from oracle.tools.executor import Approval, ToolExecutor

log = get_logger(__name__)

#: The policy-only pseudo-tool the run's card is priced against, beside `ai.graph`. It is
#: deliberately **not** in the tool registry: a registered tool that starts a whole graph
#: would be unbounded work behind one tier, and `PlannedTask(extra="forbid")` plus
#: ADR-0021 already stop a plan from naming it.
PIPELINE_TOOL = "pipe.run"

#: The highest tier a pipeline step may hold. T3 is the desktop-and-a-typed-phrase tier
#: (SECURITY.md §5) and cannot be pre-approved from a batch card without becoming
#: something weaker than what it says it is.
MAX_STEP_TIER = Tier.T2

#: Ceiling on how long a run's grants stay valid. `ApprovalStore.DEFAULT_TTL_S` is 180 s,
#: which is right for a card a person is looking at and wrong for a run that takes twenty
#: minutes. Widening an expiry is a real loosening, so it is bounded here and revoked in a
#: `finally` rather than left to lapse.
PIPELINE_MAX_S = 3600.0


@dataclass(frozen=True)
class PricedStep:
    """One step, and what the gate says it would cost."""

    step: RenderedStep
    tier: Tier
    rule: str
    digest: str
    needs_approval: bool


@dataclass
class PipelineRun:
    """Everything a person needs to decide, and everything the run needs to happen."""

    name: str
    source: str
    path: Path
    root_id: str
    project: str | None
    params: dict[str, Any]
    steps: tuple[PricedStep, ...]
    omitted: tuple[tuple[str, str], ...]
    granted: dict[str, str] = field(default_factory=dict)
    #: The file's `artifacts:` block, carried so `summarise` can build the manifest
    #: without reloading the pipeline it already priced.
    artifact_specs: tuple[Any, ...] = ()

    @property
    def tier(self) -> Tier:
        """`tier(pipeline) = max(tier(step))` — PIPELINES.md §3, as arithmetic.

        Expressed as a max rather than as a special case so that a run of nothing but
        T0/T1 steps asks nobody, which is the correct answer and not an exception."""
        return max((s.tier for s in self.steps), default=Tier.T0)


def check(
    rendered: Rendered,
    executor: ToolExecutor,
    *,
    project_root: Path | None = None,
    max_tier: Tier = MAX_STEP_TIER,
) -> tuple[tuple[PricedStep, ...], list[str]]:
    """Price every step, and report **every** problem — not the first.

    Returning all of them is the same choice `plan.validate()` makes: a person fixing one
    error at a time through a parse is a person who stops.

    `executor.preview()` does the heavy lifting, and that is deliberate rather than
    convenient. It resolves every path argument through `PolicyEngine.resolve_path` and
    pins every program, so a traversal string, an out-of-scope path, a `deny_always` hit
    or an unpinnable program **fails here** — before the graph exists — rather than at the
    step that would have run it.
    """
    priced: list[PricedStep] = []
    problems: list[str] = []

    for step in rendered.steps:
        if not executor.registry.has(step.tool):
            problems.append(f"step {step.id!r}: unknown tool {step.tool!r}")
            continue
        try:
            verdict, digest = executor.preview(step.tool, step.args)
        except Exception as exc:
            # A bad argument, a refused path, an unpinnable program. Each is a refusal
            # with a reason, and the reason is what makes it fixable.
            problems.append(f"step {step.id!r} ({step.tool}): {exc}")
            continue

        if verdict.decision is Decision.DENY:
            problems.append(f"step {step.id!r} ({step.tool}) is denied by {verdict.rule}")
            continue
        if verdict.tier > max_tier:
            problems.append(
                # `Tier` is an IntEnum, so `str()` on it is "3" — the name is what a
                # person reading a refusal needs, and "is 3" is not a sentence about tiers.
                f"step {step.id!r} ({step.tool}) is {verdict.tier.name} and a pipeline may "
                f"not exceed {max_tier.name}: a {verdict.tier.name} action needs a phrase "
                "typed for that invocation and cannot be authorised in advance "
                "(SECURITY.md §5)"
            )
            continue

        if project_root is not None:
            outside = _outside(step, executor, project_root)
            if outside:
                problems.append(
                    f"step {step.id!r} resolves {outside} outside {project_root}; a "
                    "pipeline may not reach past the project it belongs to"
                )
                continue

        priced.append(
            PricedStep(
                step=step,
                tier=verdict.tier,
                rule=verdict.rule,
                digest=digest,
                needs_approval=verdict.decision is not Decision.ALLOW,
            )
        )

    return tuple(priced), problems


def _outside(step: RenderedStep, executor: ToolExecutor, root: Path) -> str | None:
    """The scope guard, narrower than the policy scope on purpose.

    The `projects` scope covers every project. A pipeline belongs to *one* of them, so
    this re-checks the already-canonicalised paths against that one root. It narrows;
    it never widens, and it is the pipeline's own guard rather than a policy change.
    """
    contract = executor.registry.get(step.tool)
    args = contract.args_model.model_validate(step.args)
    for name in contract.path_fields:
        value = getattr(args, name, None)
        if value is None:
            continue
        resolved = executor.policy.resolve_path(str(value))
        try:
            resolved.real.relative_to(root)
        except ValueError:
            return f"{name}={resolved.real}"
    return None


async def approve_pipeline(
    approvals: ApprovalStore,
    engine: PolicyEngine,
    run: PipelineRun,
    *,
    trace_id: str,
    session_id: str | None = None,
) -> bool:
    """One card for the whole run. Returns whether it may proceed.

    A run of nothing but T0/T1 steps is `ALLOW` and asks nobody — that is the point of
    pricing it as a max rather than asking on principle.
    """
    verdict = engine.evaluate(
        PIPELINE_TOOL,
        capabilities=frozenset(),
        # Repository content is untrusted, and the gate escalates it. Owner-authored
        # config in `config/pipelines/` is not.
        provenances=(
            frozenset({Provenance.LOCAL_FOREIGN}) if run.source == Source.PROJECT else frozenset()
        ),
        declared_tier=run.tier,
    )
    if verdict.decision is Decision.DENY:
        log.warning("pipeline.denied", name=run.name, rule=verdict.rule)
        return False
    if verdict.decision is Decision.ALLOW:
        return True

    pending = await approvals.request(
        PIPELINE_TOOL,
        {"pipeline": run.name, "steps": len(run.steps)},
        verdict,
        f"pipeline:{run.root_id}",
        trace_id=trace_id,
        session_id=session_id,
        preview={
            "pipeline": run.name,
            "source": run.source,
            "path": str(run.path),
            "project": run.project,
            "params": dict(run.params),
            # Concrete arguments, not a summary of them. This is the whole card.
            "steps": [
                {
                    "step": p.step.id,
                    "tool": p.step.tool,
                    "args": p.step.args,
                    "tier": p.tier.name,
                    "rule": p.rule,
                    "asks": p.needs_approval,
                }
                for p in run.steps
            ],
            # What the parameters removed. A person deciding whether to authorise a run
            # needs to see what will not happen as much as what will.
            "omitted": [{"step": s, "reason": r} for s, r in run.omitted],
            "note": (
                "approving runs every step listed above with exactly these arguments; "
                "nothing will ask again mid-run"
                + (
                    " — this pipeline comes from a repository, not from your own config"
                    if run.source == Source.PROJECT
                    else ""
                )
            ),
        },
    )
    return await approvals.wait(pending) == Resolution.APPROVED


def grant_steps(executor: ToolExecutor, run: PipelineRun, graph: TaskGraph) -> dict[str, str]:
    """Mint one single-use grant per elevated step, bound to the digest the card showed.

    Only reachable after `approve_pipeline` returned True on a card that listed every one
    of these steps with these exact arguments and tiers.
    """
    budget = min(sum(s.step.timeout_s or 120.0 for s in run.steps) + 60.0, PIPELINE_MAX_S)
    by_step = {t.id.rsplit("-", 1)[-1]: t.id for t in graph.tasks}
    granted: dict[str, str] = {}
    for priced in run.steps:
        if not priced.needs_approval:
            continue
        task_id = by_step.get(priced.step.id)
        if task_id is None:  # pragma: no cover — compile and price share one step list
            continue
        approval_id = new_id("ap")
        executor.grant(
            Approval(
                approval_id=approval_id,
                tool=priced.step.tool,
                args_digest=priced.digest,
                tier=priced.tier,
                expires_at=time.time() + budget,
            )
        )
        granted[task_id] = approval_id
    run.granted = granted
    log.info("pipeline.granted", name=run.name, grants=len(granted), budget_s=round(budget))
    return granted


def revoke_steps(executor: ToolExecutor, granted: dict[str, str]) -> None:
    """Hand every grant back, whatever ended the run."""
    for approval_id in granted.values():
        executor.revoke(approval_id)
    if granted:
        log.info("pipeline.revoked", grants=len(granted))


def prepare(
    loaded: Loaded,
    params: dict[str, Any],
    executor: ToolExecutor,
    *,
    project_root: Path | None,
    root_id: str | None = None,
) -> tuple[PipelineRun | None, TaskGraph | None, list[str]]:
    """Everything before the approval: bind, render, price, compile.

    Nothing here executes and nothing here asks. If it returns problems, **no step has
    run and no graph exists** — which is PIPELINES.md §3's fail-fast rule stated as a
    return type.
    """
    pipeline = loaded.pipeline
    try:
        bound = bind_params(pipeline, params)
        rendered = render(pipeline, bound, project_root=str(project_root) if project_root else None)
    except PipelineError as exc:
        return None, None, [str(Problem(loaded.path, None, str(exc)))]

    priced, problems = check(rendered, executor, project_root=project_root)
    if problems:
        return None, None, [f"{loaded.path.name}: {p}" for p in problems]

    root = root_id or new_id("tk")
    graph = compile_pipeline(rendered, pipeline, root_id=root)
    run = PipelineRun(
        name=pipeline.name,
        source=loaded.source,
        path=loaded.path,
        root_id=root,
        project=loaded.project,
        params=bound,
        steps=priced,
        omitted=rendered.omitted,
    )
    return run, graph, []


def summarise(run: PipelineRun, graph: TaskGraph, status: TaskStatus) -> dict[str, Any]:
    """The run record — read off the `tasks` rows, not accumulated as it went.

    PIPELINES.md §3 asks for *"a structured summary + a run record in oracle.db"*, and the
    run record **is** the task rows keyed by `root_id`. There is no `pipeline_runs` table
    and there should not be: a second store of the same facts is a second thing to keep
    consistent, and the first one is already durable, already recovered after a crash and
    already rendered by the task tree.

    Written here, after the scheduler returns, rather than by a final `report` step. A
    step downstream of a failure is `SKIPPED`, so a reporting *step* could never report on
    a failed run — which is the only run whose report anybody needs.
    """
    by_step = {task.id.rsplit("-", 1)[-1]: task for task in graph.tasks}
    steps = []
    for priced in run.steps:
        task = by_step.get(priced.step.id)
        if task is None:
            continue
        steps.append(
            {
                "step": priced.step.id,
                "tool": priced.step.tool,
                "status": str(task.status),
                "tier": priced.tier.name,
                "rule": priced.rule,
                "summary": task.result.summary if task.result else "",
                "evidence": dict(task.result.evidence) if task.result else {},
            }
        )
    return {
        "pipeline": run.name,
        "source": run.source,
        "root_id": run.root_id,
        "project": run.project,
        "params": dict(run.params),
        "status": str(status),
        "steps": steps,
        "omitted": [{"step": s, "reason": r} for s, r in run.omitted],
        "artifacts": _artifacts(run, by_step),
    }


def _artifacts(run: PipelineRun, by_step: dict[str, Any]) -> list[dict[str, Any]]:
    """A manifest pointing at blobs the tools already wrote — never a copy of them.

    A second artifact store is a second thing to back up, and a copy destination built
    from an author-controlled label is a traversal surface bought for nothing. The label
    is validated as a label anyway, so this can safely become a filename later if a real
    need appears.
    """
    out: list[dict[str, Any]] = []
    for spec in run.artifact_specs:
        task = by_step.get(spec.from_)
        if task is None or task.result is None:
            continue
        evidence = task.result.evidence
        result = evidence.get("result") or {}
        pointer = result.get("log_path") if spec.capture == "stdout" else result
        if pointer in (None, {}):
            continue
        out.append(
            {"step": spec.from_, "capture": spec.capture, "as": spec.as_, "pointer": pointer}
        )
    return out


class PipelineService:
    """Load, price, ask once, run, summarise — and hand every grant back afterwards.

    The `finally` is the whole reason this is a class with a method rather than five calls
    at a call site: a grant that outlives its run is a grant nobody is watching, and there
    are four ways a run can end.
    """

    def __init__(
        self,
        graphs: Any,
        executor: ToolExecutor,
        approvals: ApprovalStore,
        engine: PolicyEngine,
        *,
        projects_root: Path,
    ) -> None:
        self._graphs = graphs
        self._executor = executor
        self._approvals = approvals
        self._engine = engine
        self._projects_root = projects_root

    async def run(
        self,
        loaded: Loaded,
        params: dict[str, Any],
        *,
        runners_for: Any,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """One run, start to finish. Returns the run record, or the problems that stopped
        it before anything happened."""
        project_root = self._projects_root / loaded.project if loaded.project else None
        run, graph, problems = prepare(loaded, params, self._executor, project_root=project_root)
        if run is None or graph is None:
            log.info("pipeline.refused", name=loaded.pipeline.name, problems=len(problems))
            return {"pipeline": loaded.pipeline.name, "status": "invalid", "problems": problems}

        run.artifact_specs = loaded.pipeline.artifacts
        approved = await approve_pipeline(
            self._approvals,
            self._engine,
            run,
            trace_id=trace_id or run.root_id,
            session_id=session_id,
        )
        if not approved:
            return {"pipeline": run.name, "status": "refused", "root_id": run.root_id}

        granted = grant_steps(self._executor, run, graph)
        try:
            status = await self._graphs.run(
                graph,
                runners_for(granted),
                session_id=session_id,
                trace_id=trace_id,
            )
        finally:
            revoke_steps(self._executor, granted)
        return summarise(run, graph, status)
