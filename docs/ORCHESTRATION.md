# ORACLE — Orchestration: the supervisor and the task graph

> Status: **design, Phase 7–8** (see [ROADMAP.md](ROADMAP.md)). This document specifies the layer
> that turns ORACLE from "one turn, one tool, one delegation at a time" into a supervisor of many
> tasks across several workers. It builds *on* the existing runtime — the event log, the policy
> gate, `DelegationService`, the adapters — and replaces none of it. Planning (how a task graph
> comes to exist) is in [PLANNER.md](PLANNER.md); this file is what happens to it afterwards.
>
> Design sources: the as-built single-delegation lifecycle (`src/oracle/delegation/service.py`),
> the [Asterim reuse audit](ASTERIM_REUSE.md), and ADRs [0019](DECISIONS.md#adr-0019--the-supervisor-completes-the-orchestrator)–[0021](DECISIONS.md#adr-0021--planner-output-is-untrusted-input).

## 1. The shape

```
                              USER
                               │
                               ▼
                    ┌────────────────────┐
                    │  ORACLE SUPERVISOR │   deterministic Python
                    │  (runtime, state,  │   no LLM in the control loop
                    │   gate, schedule)  │
                    └───────┬────────────┘
              ┌─────────────┼──────────────┐
              ▼             ▼              ▼
        Context Engine   Planner        Task Graph
        RAG · git ·      (a worker      (durable, in
        memory · tasks    with a role;   oracle.db)
        = layer L5        see PLANNER)
              │               │
              └───────┬───────┘
                      ▼
               Execution Plan  ──validate──▶  tasks
                                               │
                            ┌──────────────────┼──────────────────┐
                            ▼                  ▼                  ▼
                      Claude worker      AGY worker         Local / tool
                      (delegation        (delegation        (ToolExecutor,
                       runner)            runner)            local model)
                            │                  │                  │
                            └────────┬─────────┴──────────────────┘
                                     ▼
                          collect · verify · replan
                                     │
                                     ▼
                                   USER
```

The division of intelligence, restated from [ARCHITECTURE.md §1](ARCHITECTURE.md#1-what-oracle-is)
and unchanged in spirit:

| Layer | Intelligence | Never does |
|---|---|---|
| **Supervisor** | none — deterministic code | call an LLM where code suffices; execute a side effect itself |
| **Planner** (Antigravity, or a fallback) | decomposition, task authoring, review | execute tools; bypass validation; touch the runtime |
| **Workers** (Claude, AGY, local) | the actual work, inside a scoped task | widen their scope; approve anything; spawn delegations |
| **Local router** | classification, summarisation | multi-step autonomy |

**ORACLE remains authoritative.** The planner proposes data; the supervisor validates, prices,
gates, schedules, observes, verifies, and reports. No plan step executes anything by virtue of
being in a plan.

## 2. Task model

A **task** is a durable, cancellable unit of work with an owner, a specification, dependencies,
and an independently verified result. It generalises what `DelegationService` already manages for
exactly one delegation.

```python
class TaskKind(StrEnum):
    TOOL        = "tool"          # one gated ToolInvocation (existing executor)
    DELEGATION  = "delegation"    # an external agent run (existing DelegationService lifecycle)
    PLANNING    = "planning"      # a planner invocation producing an ExecutionPlan
    VERIFY      = "verify"        # diff + tests + scope check over a worker's output
    REPORT      = "report"        # summarise results for the user (local model or template)

class TaskStatus(StrEnum):
    PENDING    = "pending"        # created, dependencies not yet satisfied
    READY      = "ready"          # all dependencies succeeded; eligible to schedule
    WAITING    = "waiting"        # scheduled but parked: awaiting_approval / awaiting_human
    RUNNING    = "running"
    SUCCEEDED  = "succeeded"
    FAILED     = "failed"
    TIMEOUT    = "timeout"        # NOT folded into failed — a timed-out worker may have done the work
    SKIPPED    = "skipped"        # an ancestor failed; this never ran
    CANCELLED  = "cancelled"      # an operator stopped it
```

`SKIPPED ≠ CANCELLED` and `TIMEOUT ≠ FAILED` are deliberate, taken from Asterim's tested
vocabulary: folding them loses the only facts that distinguish a broken graph from an abandoned
one, and a wasted run from a possibly-salvageable one (a TIMEOUT delegation still gets its
worktree diffed before anything is discarded).

```python
class Task(BaseModel):
    id: str                        # "tk_…"
    root_id: str                   # the root task of this graph
    parent_id: str | None          # replanning lineage, not execution order
    plan_id: str | None            # the ExecutionPlan that authored it, if any
    kind: TaskKind
    agent: str | None              # resolved executor id; None until assignment
    spec: TaskSpec                 # PLANNER.md §3 — carries role and project (as built)
    depends_on: list[str] = []
    status: TaskStatus
    attempt: int = 1
    max_attempts: int              # from policy: 2 for TOOL/VERIFY, 1 for DELEGATION
    supersedes: str | None = None  # the failed task this one replaces (replanning)
    created_at / started_at / finished_at: datetime | None
    result: TaskResult | None

class TaskResult(BaseModel):
    ok: bool
    summary: str
    evidence: dict[str, Any]       # diff stat, test counts, artifact hashes — ORACLE's measurements
    claim: str | None              # what the worker said — kept separate from evidence, always
    cost: Cost | None              # tokens / USD where known
    error: TaskError | None        # mirrors ToolError's fields; see the layer note below
```

`evidence` vs `claim` is the load-bearing distinction, inherited from
[INTEGRATIONS.md §7](INTEGRATIONS.md#7-workspace-isolation-and-result-collection): an agent saying
"tests pass" is a claim; ORACLE running the tests is evidence, and evidence is what gets reported
and what gates dependent tasks.

### Storage

A `tasks` table in `oracle.db` (migration `0002`), written as state changes — **the row is the
record, not the memory** (Asterim's rule): a reconnecting client, a crashed daemon, and a person
six months later all read the same state. Task transitions are also events (`task.created`,
`task.updated`, `task.finished`), so the timeline, the WS stream, and `since_seq` resume work for
task graphs exactly as they do for everything else. The table is a projection the events can
rebuild; the events remain the source of truth (ADR-0010).

## 3. The graph

**A DAG is sufficient.** The question "DAG or general state machine?" was examined and the answer
is: a DAG for *structure*, a state machine per *task*, and **append-only replanning** for
dynamism. General graph rewriting was rejected: every dynamic behaviour ORACLE needs — retry,
replan, escalate, ask — is expressible as *appending* new tasks that `supersede` failed ones,
which preserves history (the event log never lies) and keeps the scheduler trivial.

Semantics (ported from Asterim's pure-function algebra, `pipeline.ts`):

- **Validation**: acyclic (cycle reported *as a path*: `a→b→c→a`), every `depends_on` id exists,
  ≤ `MAX_GRAPH_SIZE` (12 in v1 — a plan larger than that is a planner losing the thread, same
  instinct as the old 8-step cap).
- **Ready set**: `PENDING` ∧ every dependency `SUCCEEDED`. This makes the graph fail-closed with
  no special-casing — one failure and everything downstream becomes `SKIPPED`.
- **Aggregate status** (for the root task and the UI), precedence:
  `CANCELLED > FAILED > TIMEOUT > RUNNING/WAITING > SUCCEEDED`.

### Scheduling

Batched topological dispatch, deliberately boring:

```
loop:
  ready = ready_tasks(graph)
  dispatch up to available slots, per the concurrency limits
  await any completion
  record → re-derive ready set → repeat
until no task is PENDING/READY/RUNNING/WAITING
```

Concurrency limits (config, not code):

| Limit | v1 default | Why |
|---|---|---|
| Concurrent **delegations** (cloud workers) | **2** | each is minutes-long, costs money/quota, and holds a worktree; Asterim runs 4 on a stronger machine |
| Concurrent **tool tasks** | 4 | toolhost dispatch is cheap; the bound exists so a graph cannot fork-bomb the machine |
| Concurrent **local-model tasks** | 1 | one GPU, one resident model (ADR-0004) |
| Delegation **depth** | **0** — a delegate cannot start a delegation | the MCP surface offers no `ai.delegate`, and T2+ is refused to delegates by design; sub-delegation is a Phase-9+ question, not a default |

### Cancellation, timeout, HALT

The existing three-mechanism table ([AGENT_RUNTIME.md §7](AGENT_RUNTIME.md#7-cancellation-timeouts-halt))
extends to graphs without new machinery:

- **Cancel task** — cancels that task (kills its process tree via the Job Object if it owns one);
  dependents become `SKIPPED`. Independent branches continue.
- **Cancel root** — cancels every non-terminal task in the graph.
- **Timeouts** are layered per task kind: tool contract < step < task < graph. A `DELEGATION`
  timeout marks the task `TIMEOUT` and still runs collection over the worktree.
- **HALT** is unchanged and above all of this: every loop cancelled, every job object terminated,
  policy to deny-all, manual resume. A task graph adds zero new HALT paths because tasks execute
  through the same executor and adapters HALT already reaches.

### Crash recovery

Ported from `asterim-pipeline`'s tested rules, because they are the ones that survive contact:

1. On startup, load non-terminal tasks. For each `RUNNING` task with a recorded child PID:
   - process still alive → **do not adopt it; gate.** A supervisor that cannot prove what a child
     did while it was dead does not pretend to.
   - process gone → mark `FAILED(interrupted)` and **gate — never auto-restart** an interrupted
     agent; its side effects are unknown until a human (or a `VERIFY` task they approve) looks.
2. Unreadable/corrupt task state → gate, loudly. Guessing is worse than asking.
3. `WAITING` (approval-parked) tasks re-emit their `approval.requested` with the original
   timestamps, riding the existing expired-approval handling.

### As built  `P7-T1, 2026-08-25`

`src/oracle/orchestration/` — `models.py`, `graph.py`, `store.py`, `scheduler.py` — plus migration
`0002_tasks.sql`. 29 tests across `tests/test_orchestration_graph.py`,
`tests/test_orchestration_scheduler.py` and `tests/security/test_orchestration_boundary.py`, all
offline and deterministic: **every runner in them is a coroutine the test wrote.** That is the
claim P7-T1 exists to make good on — the supervisor's correctness is decidable without running
anything real.

**What matches this document.** The status vocabulary, including both distinctions
(`TIMEOUT ≠ FAILED`, `SKIPPED ≠ CANCELLED`); ready = `PENDING` ∧ every dependency `SUCCEEDED`;
the cycle reported as a path; the graph size cap; the aggregate precedence; batched topological
dispatch with per-slot concurrency limits (delegation 2, tool 4, local 1); retry within
`max_attempts` for retryable errors only.

**What this document underspecified, now decided:**

| Question | As built | Why |
|---|---|---|
| Precedence among non-terminal states | `CANCELLED > FAILED > TIMEOUT > RUNNING > WAITING > READY > PENDING > SKIPPED > SUCCEEDED` | The doc gave the head of the order; the tail matters for the UI's "what is this graph doing" line |
| Aggregate of an empty graph | `SUCCEEDED`, by vacuity | Validation already rejects empty graphs; inventing a tenth state for an unreachable case is worse |
| Who asserts `TIMEOUT` and `CANCELLED` | **The scheduler, never the runner** | A cancelled `agy` run reports `status: ERROR` / "timeout waiting for response" ([OQ-20](OPEN_QUESTIONS.md#oq-20)). If a runner's answer could set these, both distinctions die in practice while surviving in the enum |
| A task whose `kind` has no runner | `FAILED`, visibly, having run nothing | The alternative — falling through to some default — is precisely the second execution path SECURITY.md §10 rule 1 forbids |
| `max_attempts` for `PLANNING`/`REPORT` | 1 / 2 | The doc named TOOL, VERIFY and DELEGATION; a planning call costs ~55k tokens, so it gets a delegation's budget, not a tool's |
| `role` and `project` on `Task` | Moved **into `TaskSpec`** | They describe the work, not the scheduling of it. The sketch above had them in both places; one home each |
| `TaskResult.error` type | `TaskError`, not `ToolError` | The orchestration layer must not import the tool-execution layer (ARCHITECTURE.md). A runner adapts its own errors into this shape; a security test enforces the import ban |

**What is deliberately not built yet**, so nobody reads this section as more than it is:

- **Real runners.** `TOOL`, `DELEGATION`, `VERIFY` and `REPORT` runners are P7-T2; the scheduler
  takes them by injection and imports none of the layers they live in — enforced by a security
  test over the source, not by convention.
- **Startup recovery.** `TaskStore.unfinished()` exists and is tested; the gating rules built on it
  (never auto-restart an interrupted agent) arrive with P7-T2, where there is a child process to
  have an opinion about.
- **`WAITING`.** The state exists in the vocabulary and no task enters it: approval-parking lands
  with the runners that request approvals.
- **Replanning.** `supersedes` and `plan_id` are carried on `Task` and written by the store so the
  audit chain needs no migration later. Nothing populates them until P8.

### Harvest: a result must outlive its workspace  `added P7-T1`

`Worktree.harvest(message)` commits the worker's diff onto the task's own branch, and
`discard(keep_branch=True)` then removes the checkout without removing the work.

This closes a hole that was invisible while there was only ever one delegation: delegates are
forbidden git commands (a delegate that commits has hidden its own diff), so a result lived only
in the working tree — and `discard()` deleted it. Fine when the diff is *evidence to be read*;
fatal in a graph, where task C's output is task D's input, and unhelpful the moment anyone wants
to review or merge a result after the fact.

**ORACLE commits; the delegate still may not.** The commit is made after `diff()` has been read,
so what is recorded is exactly what was judged, under this machine's git identity — a commit
attributed to an agent would be a provenance lie in the one place provenance is checkable, and a
security test asserts the author.

Found the hard way: the P6-T5 spike lost its own plan-authored artifact to this
([dev log](../logs/development/2026-08-24-p6t5-antigravity-planning.md), finding 8).

## 4. Failure and replanning


What happens when things fail, per failure class:

| Failure | Handling |
|---|---|
| Tool task fails | retry within `max_attempts` (2) if the error is `retryable`; else mark `FAILED`, skip dependents, surface |
| Worker (Claude/AGY) errors or times out | collect anyway (diff the worktree — evidence may exist); mark `FAILED`/`TIMEOUT`; eligible for **replan** |
| Worker result fails verification (tests red, out-of-scope writes) | the `VERIFY` task fails, which is the same as any failure — the worker's claim never gates anything |
| Worker touches paths outside its worktree scope | policy violation: task `FAILED`, delegation revoked, audit entry; **never retried automatically** |
| Planner returns an invalid plan | one repair attempt with the specific validation errors (the ADR-0017 pattern), then fall down the planner ladder ([PLANNER.md §6](PLANNER.md#6-fallbacks)) |
| Context insufficient (worker says so in its result) | replan may add a research task; this is the one case where the graph *grows* forward |
| Local model fails / Ollama down | tool tasks and delegations unaffected; PLANNING/REPORT tasks fall back per the degradation table |
| Dependency `SKIPPED` cascade | reported as the graph's shape, not as N separate errors |

**Replanning is bounded and append-only.** A replan:

1. Is triggered by the supervisor on a `FAILED`/`TIMEOUT` task whose root has replan budget left
   (**≤ 2 replans per root**, matching the turn critic's budget — unbounded replanning is how an
   agent burns an afternoon achieving nothing).
2. Invokes the planner *with the failure*: the original objective, the failed task's spec, ORACLE's
   evidence (not the worker's claim), and prior attempts. Never a blank slate.
3. Produces new tasks that `supersede` the failed ones. History is never rewritten; the UI shows
   the failed attempt and its replacement side by side.
4. Exhausted budget → the root task fails with a report of everything tried, and — where a partial
   result exists in a worktree — the keep/discard decision the delegation flow already offers.

Escalation order for a failed worker task, cheapest first: retry (if retryable) → replan →
fallback agent (capability registry, [PLANNER.md §5](PLANNER.md#5-agent-selection)) → human. Every
rung is visible in the task lineage.

## 5. Security posture of the graph

The graph adds surface; the controls extend rather than bend
(detail in [SECURITY.md §10](SECURITY.md#10-the-multi-agent-surface--added-2026-08-24-phases-78)):

- **Every task crosses the same gate.** A `TOOL` task is an ordinary `ToolInvocation`; a
  `DELEGATION` task is priced under `ai.delegate` with its egress preview exactly as today. A plan
  is not a privilege; it is a to-do list awaiting per-item authorisation.
- **Planner output is untrusted** (`external` provenance → the turn that ingests it is tainted →
  tier escalation applies to every task the plan spawns). ADR-0021.
- **One approval, or several, never zero**: a graph's elevated tasks are listed **up front** in a
  single pre-run approval where the tiers are known statically (the pipeline rule), and
  individually where they only resolve at runtime (a delegation's egress preview binds to the
  rendered bytes, so it cannot be pre-approved before it exists).
- **Worker output is data.** A worker's result text can propose, in prose, whatever it wants;
  nothing in it can name a tool that auto-executes. Inter-agent instruction injection lands in the
  same taint machinery as document injection.
- **Everything audited**: task creation names its plan and planner; every gate decision carries
  `task_id`; the audit chain answers "which agent did this, authorised by whom, from which plan".

## 6. Observability

Identifier relationships (extends the existing `trace_id`/`session_id` scheme; `task_run_id` and
`agent_run_id` were considered and rejected — `attempt` on the task and the adapter's own session
id in `evidence` carry the same information without two more id namespaces):

```
session_id ─┬─ turn (events only, as today)
            └─ root task ─┬─ plan_id (planning task's output)
                          └─ task_id ×N ─ attempt ─ tool.* / delegate.event / approval.* events
trace_id: unchanged — stamps every event of one causal chain, across all of the above
```

The execution tree the UI renders **is** a query over this: tasks by `root_id`, joined to their
events. No parallel bookkeeping (see [UI.md §6b](UI.md#6b-the-execution-tree--phase-11)).

## 7. End-to-end example

*"Look at the current Asterim tasks, summarize the state, and continue development."*

```
 1  USER → "look at the Asterim tasks, summarise, continue development"
 2  L4  pre-router: no exact match → intent {continue_project, project: Asterim}
 3  SUPERVISOR: create root task tk_root; emit task.created
 4  CONTEXT ENGINE (L5): assemble the planning context package
      · Asterim docs: AGENTS.md, decisions.md, blueprint/, current task file
      · git: branch, last commits, dirty state           (tools, T0)
      · RAG: hybrid search over the project, scoped      (T0)
      · memory: prior attempts on Asterim tasks          (band 5)
      · redact → budget (30k cap) → provenance labels
 5  PLANNING task tk_plan (agent: antigravity, role: planner)
      · T2 egress → EGRESS PREVIEW #1: the planning packet, previewed, approved   ◀ human
      · agy -p --output-format stream-json --json-schema <ExecutionPlan>
      · returns: {objective, summary, tasks:[
          A {role: coder,     project: Asterim, "implement X",  depends_on: []},
          B {role: tester,    project: Asterim, "cover X",      depends_on: [A]},
          C {role: reviewer,  project: Asterim, "review A+B",   depends_on: [A,B]},
          D {role: summarizer,"digest for the user",            depends_on: [C]} ]}
 6  SUPERVISOR: validate (schema · DAG · projects resolve · roles known · size ≤ 12)
      · plan is external provenance → taint set → tiers escalate
      · agent selection: A,B → claude (coder/tester caps) · C → antigravity · D → local
      · graph approval card: "4 tasks, 3 delegations, est. cost, elevated items listed"  ◀ human
 7  SCHEDULE: A ready → run
      · task A = the existing delegation lifecycle end to end:
        packet rendered → digest → EGRESS PREVIEW #2 (worktree wt/A)               ◀ human
        → claude -p … , events streamed as delegate.event under tk_A
        → collect: diff + ORACLE runs the tests in wt/A          = evidence
 8  A SUCCEEDED → B ready (same lifecycle, wt/B branches from A's result branch)
 9  B SUCCEEDED → C ready → antigravity reviews the combined diff (read-only packet)
10  C SUCCEEDED → D ready → local model writes the digest from ORACLE's evidence
11  SUPERVISOR: root task SUCCEEDED; report = summary + per-task diffs, test counts,
      costs, and merge/keep/discard controls per worktree
12  memory: attempts recorded (task_signature, outcome, what_failed=None)
      events, audit entries and packets already on disk — nothing to add
```

Failure variant: if B's verification fails (tests red), B is `FAILED`, C and D are `SKIPPED`, and
the supervisor replans: the planner receives A's diff, B's failing test names, and the attempts
record; it emits B′ (supersedes B). Two replan failures → the root fails with the full lineage and
the worktrees intact for a human.

Where the humans are: step 5 (planning egress), step 6 (graph approval), step 7 (each delegation's
egress — collapsible into step 6's card only for packets whose bytes are already rendered), and
HALT at any moment. Where the audit lands: every gate decision, every egress, every task
transition, hash-chained.

## 8. What this deliberately is not

- **Not a general agent framework.** The graph executes ORACLE's task kinds through ORACLE's gate.
  There is no plugin API for arbitrary node types.
- **Not LangGraph.** Durable execution and replay already exist here as the event log; adopting a
  graph framework would duplicate the runtime's spine to gain checkpoint semantics it already has
  (ADR-0022).
- **Not an autonomy dial.** More tasks does not mean fewer approvals; the egress and tier rules are
  per-action and survive the graph unchanged.
- **Not v1 of a distributed system.** One machine, one supervisor, in-process scheduling.
  Asterim's LAN dispatch protocol is the reference if that ever changes.
