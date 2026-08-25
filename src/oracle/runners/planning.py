"""Planning: an objective goes out, a plan comes back, a person decides what happens.

This is the only place ORACLE asks a model to decide *what work exists*, so it is also
the place where the most care is spent on not letting the answer become authority. Three
things it does, in order, and none of them may be skipped:

1. **The planning call is an egress**, priced under `ai.delegate` and previewed like any
   other. Its packet is the objective plus the rules the plan must satisfy — no repo
   contents, no tools, no worktree. A planner that browses is a planner whose egress
   cannot be previewed as one packet (PLANNER.md §7), and the P6-T5 spike watched one try:
   given an empty workspace, `agy` reached for the owner's home directory three times out
   of eight and was stopped by the vendor's permission gate, not by our intent.
2. **The answer is validated before anything sees it**, with one repair attempt fed the
   specific errors. One, then the ladder — a second would be an agentic loop wearing a
   budget's clothes.
3. **The graph is approved as a shape** before it runs. One card listing every task, its
   role, its agent and whether it will egress. Each delegation still asks its own question
   later, because an egress approval binds to bytes that do not exist yet.

The repair attempt is inside the *same* approval, and the preview says so ("up to 2
calls"). Stating the bound up front is the pipeline rule; asking again for a retry the
person already sanctioned is how approval fatigue is manufactured.
"""

from __future__ import annotations

import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oracle.core.approvals import ApprovalStore, Resolution
from oracle.core.events import new_id
from oracle.integrations.adapter import ExternalAgentAdapter
from oracle.integrations.types import HandoffPacket, Workspace
from oracle.logsink import get_logger
from oracle.orchestration.graph import TaskGraph
from oracle.orchestration.models import Task
from oracle.orchestration.plan import (
    ExecutionPlan,
    compile_plan,
    elevated_summary,
    parse,
    plan_schema,
    repair_prompt,
    validate,
)
from oracle.orchestration.registry import Registry
from oracle.orchestration.replan import (
    REPLAN_BUDGET,
    Attempt,
    ReplanRequest,
    attach,
    attempts_report,
    consider,
)
from oracle.policy.engine import PolicyEngine
from oracle.policy.model import Capability, Decision, Provenance, Tier

log = get_logger(__name__)

#: The planning call is priced as what it is: an egress to a vendor. It gets no separate
#: tier of its own, because "we are only asking it to think" is exactly the argument that
#: would eventually be used for a planner with tools.
EGRESS_TOOL = "ai.delegate"
#: The graph approval card. Authorises a shape, never an action (config/policy.yaml).
GRAPH_TOOL = "ai.graph"
DESTINATION = "the planner's vendor"
#: One call, plus at most one repair. The bound is in the preview a person approves.
MAX_CALLS = 2


@dataclass
class PlanOutcome:
    """What planning produced, and everything needed to explain it if it produced nothing."""

    plan: ExecutionPlan | None = None
    problems: list[str] = field(default_factory=list)
    attempts: int = 0
    #: ORACLE's own measurements of the call: cost, duration, whether the vendor said it
    #: succeeded. The plan itself is data the vendor authored, and is labelled as such.
    evidence: dict[str, Any] = field(default_factory=dict)
    #: Set when the owner declined the egress, so the caller can say "you said no" rather
    #: than "planning failed".
    refused: bool = False

    @property
    def ok(self) -> bool:
        return self.plan is not None


class Planner:
    """Turns an objective into a validated plan, asking before it spends anything."""

    def __init__(
        self,
        adapter: ExternalAgentAdapter,
        approvals: ApprovalStore,
        engine: PolicyEngine,
        registry: Registry,
        *,
        projects: set[str] | None = None,
        session_id: str | None = None,
    ) -> None:
        self._adapter = adapter
        self._approvals = approvals
        self._engine = engine
        self._registry = registry
        self._projects = projects or set()
        self._session_id = session_id

    # -- the prompt ----------------------------------------------------------

    def rules(self) -> str:
        """What the plan must satisfy, stated in the prompt because a planner told the
        rules produces fewer invalid plans than one corrected afterwards — and because
        every rule here is one ORACLE enforces anyway."""
        roles = ", ".join(sorted(r for r in self._registry.roles if self._registry.holders_of(r)))
        projects = ", ".join(sorted(self._projects)) or "none"
        agents = ", ".join(sorted(self._registry.agents))
        return (
            "You are producing an execution plan for ORACLE, a local-first supervisor that "
            "will execute it by delegating each task to a coding agent in an isolated git "
            "worktree and verifying the result itself.\n"
            "Return ONLY a JSON object matching the provided schema. No prose, no code fences.\n"
            "Rules ORACLE validates and rejects the plan for:\n"
            '- at most 12 tasks; each `id` unique and plan-local (e.g. "A", "B").\n'
            "- every `depends_on` entry names another task in this plan; no cycles.\n"
            f"- `role` is one of: {roles}.\n"
            f"- `project`, if set, is one of: {projects}; otherwise null.\n"
            f"- `agent_hint`, if set, is one of: {agents}. It is a recommendation only.\n"
            '- every task whose `expected_outcome` is "diff" has non-empty `acceptance`: '
            "criteria a machine can check.\n"
            "- `context_hints` are queries or file paths for ORACLE to fetch. Hints, not "
            "contents.\n"
            "- do NOT invent fields. A plan carrying a field outside the schema is rejected "
            "whole, and a plan cannot name a tool or a command.\n"
            "State real dependencies: tasks that must happen in order must say so.\n"
            "You will not execute any of this and you have no tools. Return the plan."
        )

    def packet(self, objective: str, task_id: str, extra: str = "") -> HandoffPacket:
        return HandoffPacket(
            task_id=task_id,
            task=f"{self.rules()}\n\nOBJECTIVE:\n{objective}" + (f"\n\n{extra}" if extra else ""),
            # No tools and no MCP surface. The planner receives a package and returns data.
            allowed_tools=("Read",),
            result_schema=plan_schema(),
        )

    # -- the run -------------------------------------------------------------

    async def plan(
        self,
        objective: str,
        *,
        trace_id: str,
        failure: str = "",
        purpose: str = "planning",
        preview_extra: dict[str, Any] | None = None,
    ) -> PlanOutcome:
        """One approved egress, up to `MAX_CALLS` calls inside it, one validated plan out.

        `failure` is what makes this the same method for planning and **replanning**: a
        replan is the identical call with the failure attached to the prompt and named on
        the card. Sharing the path is deliberate — a second, nearly-identical planning
        route is how one of them quietly stops validating."""
        packet = self.packet(objective, task_id=f"plan-{trace_id}", extra=failure)
        verdict = self._engine.evaluate(
            EGRESS_TOOL,
            capabilities=frozenset({Capability.AGENT_DELEGATE, Capability.NET_EGRESS}),
            declared_tier=Tier.T2,
        )
        if verdict.decision is Decision.DENY:
            return PlanOutcome(problems=[f"planning is not allowed here: {verdict.rule}"])

        pending = await self._approvals.request(
            EGRESS_TOOL,
            {"objective": objective, "purpose": purpose},
            verdict,
            f"{purpose}:{trace_id}",
            trace_id=trace_id,
            session_id=self._session_id,
            preview={
                "destination": DESTINATION,
                "adapter": self._adapter.id,
                "purpose": purpose,
                "objective": objective,
                # The whole prompt, failure context included: a person approving a replan
                # is approving the sending of ORACLE's evidence about a failed run, and
                # summarising that away would hide the only new thing in it.
                "prompt": packet.render_prompt(),
                # The bound, stated before the click rather than discovered after it.
                "calls": f"up to {MAX_CALLS} (one repair attempt if the plan is invalid)",
                "sends_repo_contents": False,
                **(preview_extra or {}),
            },
        )
        if await self._approvals.wait(pending) != Resolution.APPROVED:
            return PlanOutcome(refused=True, problems=[f"the {purpose} egress was not approved"])

        outcome = PlanOutcome()
        extra = failure
        for attempt in range(1, MAX_CALLS + 1):
            outcome.attempts = attempt
            raw, evidence = await self._call(objective, packet.task_id, extra)
            outcome.evidence = evidence
            plan, problems = parse(raw)
            if plan is not None:
                problems = validate(plan, self._registry, self._projects)
                if not problems:
                    outcome.plan = plan
                    outcome.problems = []
                    log.info("plan.valid", attempt=attempt, tasks=len(plan.tasks), purpose=purpose)
                    return outcome
            outcome.problems = problems
            log.warning("plan.invalid", attempt=attempt, problems=problems[:5], purpose=purpose)
            if attempt >= MAX_CALLS:
                break
            # The repair rides *with* the failure context, never instead of it: a repair
            # prompt that dropped the failure would be asking for a plan for a different
            # problem.
            extra = (
                f"{failure}\n\n{repair_prompt(problems)}" if failure else repair_prompt(problems)
            )
        return outcome

    async def replan(self, request: ReplanRequest, *, trace_id: str) -> PlanOutcome:
        """One more idea, told what went wrong. Identical machinery to `plan()`, with the
        failure in the prompt and the budget on the card."""
        return await self.plan(
            request.objective,
            trace_id=trace_id,
            failure=failure_context(request),
            purpose="replanning",
            preview_extra={
                "replaces": request.failed_id,
                "replan": f"{request.attempt_number} of {request.budget} for {request.root_id}",
                # Named because it is the difference between this and a first plan: what
                # goes out is ORACLE's measurements of a failed run, not the worker's
                # account of it.
                "sends": "ORACLE's own evidence about the failed task, not the worker's claim",
            },
        )

    async def _call(self, objective: str, task_id: str, extra: str) -> tuple[Any, dict[str, Any]]:
        """One vendor call in a workspace that holds nothing. The temp directory exists
        because adapters need a cwd, not because the planner has anything to read there."""
        workdir = Path(tempfile.mkdtemp(prefix="oracle-plan-"))
        packet = self.packet(objective, task_id, extra)
        handle = await self._adapter.submit(packet, Workspace(path=workdir))
        async for _ in self._adapter.events(handle):
            pass
        result = await self._adapter.collect(handle)
        evidence = {
            "ok": result.success,
            "exit_code": result.exit_code,
            "cost_usd": result.cost_usd,
            "duration_ms": result.duration_ms,
            "num_turns": result.num_turns,
        }
        # `structured` first: it is the vendor's own filtered field. The prose is the
        # fallback for adapters that fill only that, and is never trusted further than
        # `parse()` — which refuses anything wrapped in commentary.
        return (
            result.structured if result.structured is not None else result.result_text
        ), evidence


def _attempt_lines(attempt: Attempt) -> str:
    """One attempt rendered for the planner. `evidence` only — `Attempt` has no field for
    the worker's claim, which is the enforcement rather than the convention."""
    body = [
        f"- task {attempt.task_id} ({attempt.role}) — {attempt.status}",
        f"  objective: {attempt.objective}",
    ]
    if attempt.error:
        body.append(f"  error: {attempt.error}")
    if attempt.evidence:
        measured = ", ".join(f"{k}={v!r}" for k, v in sorted(attempt.evidence.items()))
        body.append(f"  ORACLE measured: {measured}")
    return "\n".join(body)


def failure_context(request: ReplanRequest) -> str:
    """What the planner is told about the failure, and the only thing that makes its
    second answer different from its first (ORCHESTRATION.md §4, rule 2).

    Everything here is ORACLE's own record. There is no path by which a worker's prose
    reaches this string, because `Attempt` does not carry one — the separation is a
    missing field, not a filter somebody has to remember to apply."""
    parts = [
        "This is a REPLAN. A previous attempt at this objective failed and you are being "
        f"asked for one more approach (attempt {request.attempt_number} of {request.budget} "
        "for this objective; there will not be another).",
        "WHAT FAILED:\n" + _attempt_lines(request.failed),
    ]
    if request.prior:
        parts.append("ALSO FAILED EARLIER:\n" + "\n".join(_attempt_lines(a) for a in request.prior))
    if request.skipped:
        parts.append(
            "NEVER RAN, because it depended on the failed task. This work is NOT resumed: "
            "if it is still wanted, your plan must ask for it again.\n"
            + "\n".join(f"- {a.task_id} ({a.role}): {a.objective}" for a in request.skipped)
        )
    parts.append(
        "Everything above is ORACLE's own measurement of what happened. It is not the "
        "worker's account of its own work, and you are not being asked to trust one.\n"
        "Plan the remaining work differently. Do not repeat the failed approach, and do "
        "not assume the failed task's output exists."
    )
    return "\n\n".join(parts)


async def approve_graph(
    approvals: ApprovalStore,
    engine: PolicyEngine,
    graph: TaskGraph,
    plan: ExecutionPlan,
    *,
    trace_id: str,
    session_id: str | None = None,
) -> bool:
    """The graph approval card: one decision over the whole shape (SECURITY.md §10).

    The plan arrives as `external` provenance — it is a vendor's text — so the gate
    escalates the tier before anyone is asked. Approving this authorises the graph to
    *exist and run*; it authorises no egress, and every delegation inside it still asks
    its own question with its rendered bytes attached."""
    verdict = engine.evaluate(
        GRAPH_TOOL,
        capabilities=frozenset({Capability.AGENT_DELEGATE}),
        # ADR-0021: the plan is untrusted input, and the tasks it spawns start escalated.
        provenances=frozenset({Provenance.EXTERNAL}),
        declared_tier=Tier.T2,
    )
    if verdict.decision is Decision.DENY:
        log.warning("graph.denied", rule=verdict.rule)
        return False
    pending = await approvals.request(
        GRAPH_TOOL,
        {"root_id": graph.root_id, "tasks": len(graph)},
        verdict,
        f"graph:{graph.root_id}",
        trace_id=trace_id,
        session_id=session_id,
        preview={
            "objective": plan.objective,
            "summary": plan.summary,
            # Shown, never acted on: what the planner said it was unsure about.
            "risks": list(plan.risks),
            "tasks": elevated_summary(graph.tasks),
            "addition": False,
            "note": (
                "approving runs the graph; each delegation still asks separately before "
                "anything leaves this machine"
            ),
        },
    )
    return await approvals.wait(pending) == Resolution.APPROVED


async def approve_additions(
    approvals: ApprovalStore,
    engine: PolicyEngine,
    added: list[Task],
    plan: ExecutionPlan,
    *,
    request: ReplanRequest,
    trace_id: str,
    session_id: str | None = None,
) -> bool:
    """The same card, for a replan's *added* tasks (ORCHESTRATION.md §4, rule 3).

    Deliberately **not** a new approval type: a replan is new work and gets a new
    decision, but a second kind of card would be a second thing to learn to read, and the
    tier, the provenance and the meaning of "yes" are all identical.

    It lists only the additions. Re-showing the whole graph for two new rows is how a
    person is trained to click through a card without reading it — and the one thing a
    replan card must communicate is *what is new*, plus which failure it answers and how
    much of the budget it spends."""
    verdict = engine.evaluate(
        GRAPH_TOOL,
        capabilities=frozenset({Capability.AGENT_DELEGATE}),
        provenances=frozenset({Provenance.EXTERNAL}),
        declared_tier=Tier.T2,
    )
    if verdict.decision is Decision.DENY:
        log.warning("replan.denied", rule=verdict.rule)
        return False
    pending = await approvals.request(
        GRAPH_TOOL,
        {"root_id": request.root_id, "tasks": len(added), "supersedes": request.failed_id},
        verdict,
        f"replan:{request.failed_id}",
        trace_id=trace_id,
        session_id=session_id,
        preview={
            "objective": plan.objective,
            "summary": plan.summary,
            "risks": list(plan.risks),
            "tasks": elevated_summary(added),
            # The discriminator a UI needs to say "added to the graph" rather than
            # re-rendering it as though it were the whole thing.
            "addition": True,
            "replaces": request.failed_id,
            "replaced_because": request.failed.error or request.failed.status,
            "replan": f"{request.attempt_number} of {request.budget}",
            "note": (
                f"these {len(added)} tasks are ADDED to graph {request.root_id}; "
                f"{request.failed_id} stays failed and is not re-run"
            ),
        },
    )
    return await approvals.wait(pending) == Resolution.APPROVED


def compile_and_approve(plan: ExecutionPlan, registry: Registry, *, plan_id: str) -> TaskGraph:
    """Convenience for the caller: a validated plan becomes a graph. Kept separate from
    `approve_graph` so a caller can inspect the graph — or a test can — before anybody is
    asked to approve it."""
    return compile_plan(plan, registry, plan_id=plan_id)


# -- composition ---------------------------------------------------------------


def make_replanner(
    planner: Planner,
    approvals: ApprovalStore,
    engine: PolicyEngine,
    registry: Registry,
    tasks_of: Callable[[], Awaitable[list[Task]]],
    *,
    objective: str,
    trace_id: str,
    session_id: str | None = None,
    on_exhausted: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> Callable[[Task], Awaitable[list[Task]]]:
    """Bind the replan decision to the planner that can act on it.

    This is the whole of the composition the scheduler is kept away from: it decides
    (`orchestration.replan.consider`), it spends (an egress, previewed and approved), it
    asks again (the additions card), and only then does it hand rows back. The scheduler
    receives a list of tasks and has no idea any of that happened.

    `tasks_of` reads the root's rows — from the store in the daemon, so the budget is
    counted from the durable record rather than from anything held in memory by the loop
    that is asking."""

    async def replan_for(failed: Task) -> list[Task]:
        tasks = await tasks_of()
        request, reason = consider(failed, tasks, objective=objective)
        if request is None:
            log.info("replan.declined", task_id=failed.id, reason=reason)
            if on_exhausted is not None and "budget" in reason:
                # The budget running out is the one refusal that owes the person a
                # report: ORCHESTRATION.md §4 says the root fails *with everything that
                # was tried*, and the worktrees are still there to keep or discard.
                await on_exhausted({"reason": reason, **attempts_report(tasks)})
            return []

        outcome = await planner.replan(request, trace_id=trace_id)
        if outcome.plan is None:
            log.info(
                "replan.no_plan",
                task_id=failed.id,
                refused=outcome.refused,
                problems=outcome.problems[:3],
            )
            return []

        plan_id = new_id("pl")
        compiled = compile_plan(
            outcome.plan,
            registry,
            root_id=failed.root_id,
            plan_id=plan_id,
            # Same root, new namespace: `{root}-r1-a` cannot collide with `{root}-a`.
            id_prefix=f"{failed.root_id}-r{request.attempt_number}",
        )
        added = attach(compiled.tasks, failed=failed, plan_id=plan_id)

        if not await approve_additions(
            approvals,
            engine,
            added,
            outcome.plan,
            request=request,
            trace_id=trace_id,
            session_id=session_id,
        ):
            log.info("replan.not_approved", task_id=failed.id, plan_id=plan_id)
            return []
        return added

    return replan_for


__all__ = [
    "EGRESS_TOOL",
    "GRAPH_TOOL",
    "MAX_CALLS",
    "REPLAN_BUDGET",
    "PlanOutcome",
    "Planner",
    "approve_additions",
    "approve_graph",
    "compile_and_approve",
    "failure_context",
    "make_replanner",
]
