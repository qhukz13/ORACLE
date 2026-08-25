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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oracle.core.approvals import ApprovalStore, Resolution
from oracle.integrations.adapter import ExternalAgentAdapter
from oracle.integrations.types import HandoffPacket, Workspace
from oracle.logsink import get_logger
from oracle.orchestration.graph import TaskGraph
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

    async def plan(self, objective: str, *, trace_id: str) -> PlanOutcome:
        packet = self.packet(objective, task_id=f"plan-{trace_id}")
        verdict = self._engine.evaluate(
            EGRESS_TOOL,
            capabilities=frozenset({Capability.AGENT_DELEGATE, Capability.NET_EGRESS}),
            declared_tier=Tier.T2,
        )
        if verdict.decision is Decision.DENY:
            return PlanOutcome(problems=[f"planning is not allowed here: {verdict.rule}"])

        pending = await self._approvals.request(
            EGRESS_TOOL,
            {"objective": objective},
            verdict,
            f"plan:{trace_id}",
            trace_id=trace_id,
            session_id=self._session_id,
            preview={
                "destination": DESTINATION,
                "adapter": self._adapter.id,
                "purpose": "planning",
                "objective": objective,
                "prompt": packet.render_prompt(),
                # The bound, stated before the click rather than discovered after it.
                "calls": f"up to {MAX_CALLS} (one repair attempt if the plan is invalid)",
                "sends_repo_contents": False,
            },
        )
        if await self._approvals.wait(pending) != Resolution.APPROVED:
            return PlanOutcome(refused=True, problems=["the planning egress was not approved"])

        outcome = PlanOutcome()
        extra = ""
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
                    log.info("plan.valid", attempt=attempt, tasks=len(plan.tasks))
                    return outcome
            outcome.problems = problems
            log.warning("plan.invalid", attempt=attempt, problems=problems[:5])
            if attempt >= MAX_CALLS:
                break
            extra = repair_prompt(problems)
        return outcome

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
            "tasks": elevated_summary(graph),
            "note": (
                "approving runs the graph; each delegation still asks separately before "
                "anything leaves this machine"
            ),
        },
    )
    return await approvals.wait(pending) == Resolution.APPROVED


def compile_and_approve(plan: ExecutionPlan, registry: Registry, *, plan_id: str) -> TaskGraph:
    """Convenience for the caller: a validated plan becomes a graph. Kept separate from
    `approve_graph` so a caller can inspect the graph — or a test can — before anybody is
    asked to approve it."""
    return compile_plan(plan, registry, plan_id=plan_id)
