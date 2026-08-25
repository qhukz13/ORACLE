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
`0002_tasks.sql`. 53 tests across `tests/test_orchestration_graph.py`,
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

### As built — the runners  `P7-T2, 2026-08-25`

`src/oracle/runners/` — `tool.py`, `delegation.py`, `verify.py` — plus
`orchestration/recovery.py`. 74 orchestration tests in total; everything below the vendor
is real (a real `ToolExecutor` over a real policy, a real `DelegationService`, real
worktrees, real git) and only the CLI is a stub replaying recorded output.

**Why `runners/` is not inside `orchestration/`.** The scheduler may not import the layers
that execute, and a security test enforces that against the source. Putting the adapters
in the same package would have forced either a protocol dance or a hole in the ban. They
are a composition layer, like `api/app.py`: allowed to see both sides, which is what
composing means.

| Runner | Wraps | The judgement it adds |
|---|---|---|
| `TOOL` | `ToolExecutor` | Which failures are retryable. A denial never is; `TIMEOUT` and `EXECUTION_FAILED` are. Retrying a denial is how an agent nags a person into approving something |
| `DELEGATION` | `DelegationService`, unchanged | Splitting the lifecycle's single dict into `evidence` (exit code, diff, tests, branch) and `claim` (what the agent said). Only `evidence` sets `ok`, so only `evidence` gates a dependent task |
| `VERIFY` | `dev.run_tests` | Comparing against a baseline instead of a threshold — see below |

**Verification is a delta, not a threshold.** P6-T5 measured 28 failing tests in a
*pristine* worktree of this repo (no `.venv`, so suites that spawn a binary die) and the
same 28 in the delegate's, which had added five passing tests and broken nothing. A
verifier reading "failures > 0" would reject every correct delegation. So `VERIFY` runs
the suite in the worker's workspace, runs it once per graph in a clean one, and reports
`new_failures`, `fixed` and `delta_passed`. **No baseline, no verdict**: if the baseline
cannot be taken, the task fails and says so rather than falling back to a threshold —
a verifier that guesses is worse than one that admits it cannot tell. The workspace it
checks is read from the dependency's *row*, never from a claim.

**Two bugs the tests found, both worth keeping in mind:**

* Harvest was gated on `diff_lines`, which counts *tracked* changes only. A worker whose
  output is new files — a new module, a new test, a recorded fixture — produces none, so
  its work would never have been committed. Harvest is now attempted whenever a worktree
  exists and `harvest()` itself decides whether anything was staged.
* A test that resolved three concurrent egress approvals by calling the "wait for the
  next approval" helper in a loop re-read the *same* request every time (the helper
  restarts the stream from seq 0), while the other two expired. Three concurrent
  delegations need one subscription and three answers.

**`source: "graph"` on scheduler events.** A `DELEGATION` task's own lifecycle emits
`task.*` for the same `task_id` (rendering → awaiting_egress → running → verifying).
Both streams are wanted — one is graph state, the other is the delegation's progress —
so the scheduler stamps its own, and a consumer no longer has to guess from payload keys.

### Crash recovery, as built  `P7-T2`

`orchestration/recovery.py`, awaited at daemon start before any other work begins.

* Every `RUNNING` task becomes `FAILED` with error kind `interrupted`, `retryable: false`.
  Dependents are therefore `SKIPPED` on the next scheduling pass — nothing proceeds on a
  result nobody verified.
* `PENDING`/`READY`/`WAITING` tasks are left exactly as they are and reported, which is
  what lets a person see a half-finished graph rather than a quiet one.
* One `system.degraded` event names everything found; a clean shutdown emits nothing,
  because a recovery event on every start trains everyone to ignore recovery events.
* **Nothing is restarted, ever.**

Two honest limits, stated rather than implied:

1. **No PID, so no liveness check.** ORCHESTRATION.md §3 splits "process alive → gate"
   from "process gone → FAILED(interrupted) → gate". ORACLE records no child PID on the
   task row, so both collapse into the conservative branch. Both gate, so no *decision*
   changes; what is lost is the ability to say which happened. Adding `pid` is a
   migration plus a scheduler hook and buys a diagnostic, so it waits for a task that
   needs the diagnostic.
2. **"Gate" today means an event, not a card.** There is no graph approval UI until P8;
   recovery states facts loudly and stops. Worktrees are deliberately *not* cleaned up —
   an interrupted delegation may have left real work, and `harvest()` exists because such
   work is worth something.

**Not wired: the runners are not constructed in the daemon.** `AppState` carries a
`TaskStore` and recovery runs at startup, because both have an effect today. Nothing
creates graphs until P8 routes an intent to one, and constructing runners that nothing
calls would be dead code wearing the costume of integration.

### As built — the human surfaces  `P7-T3, 2026-08-25`

`orchestration/service.py`, `Parked` in the scheduler, `GET /api/v1/tasks`, the `graph.cancel`
command, and a `TaskTree` in the desktop UI. Phase 7 is complete: a graph now schedules real work,
verifies it, survives a crash, **can be stopped, and can be looked at** — with no planner anywhere.

**`GraphService` is an address, not a new authority.** It holds the live schedulers by `root_id`
so the API can say "stop that one", the way `TerminalBridge` holds shells. It does not build
graphs and does not own runners: both are passed in, so the composition stays in the daemon and
this file keeps importing nothing that executes.

#### HALT needed no new path, and now there is a test that says so

Cancelling a graph's coroutine does **not** cancel the runner tasks it spawned — they are
independent asyncio tasks. Without something to close that gap, HALT would have left a vendor
process running while the supervisor watching it was gone: the exact orphan HALT exists to
prevent. The fix belongs to the scheduler, not to HALT: `_abandon()` cancels its own children on
`CancelledError`, and everything downstream already worked — the delegation runner sees the
cancellation, `DelegationService` cancels its adapter, the process dies.

`test_halt_reaches_a_graphs_child_process` asserts on a **real child pid** (the stub CLI, wedged
with `STUB_HANG=1`), because a HALT proven against fake runners is a HALT that has never been
tested. It also asserts the row says `CANCELLED` rather than `RUNNING`: a task left `RUNNING` in
the table is read as an interrupted agent by the next start-up, which is a stronger claim than
the truth.

#### `WAITING`: parking, and what the scheduler is not allowed to know

A `TOOL` task whose call the gate wants confirmed returns `Parked(reason, until)` instead of a
result. The scheduler sets `WAITING`, **frees the slot**, and re-dispatches when `until`
completes. It deliberately knows nothing about approvals — the seam is "wait on this awaitable and
try me again" — so the day something parks on a rate limit or a lock there is no new concept, and
the import ban stays intact.

The runner's own rules, both learned by a test failing:

* **Ask once per task.** The first version re-asked on the resumed attempt, so a *refused* task
  parked, resumed, asked again, parked again — forever, asking the person who said no every few
  milliseconds. Now the second attempt runs into the gate's own `APPROVAL_REQUIRED` and fails
  there, where the refusal is already recorded.
* **A grant belongs to one task.** Two tasks making the identical call are two decisions; the
  second asks for itself. Otherwise a graph of twelve identical calls costs one click
  (`test_one_tasks_approval_does_not_authorise_another_task`).

Cancelling a parked task cancels its watcher too, and the watcher re-checks: an approval answered
*after* the cancellation does not resurrect it.

#### The projection

`GET /api/v1/tasks?root_id=…` is a SELECT and a shape — no cache, no second writer. It answers
identically for a live graph and a finished one; `live` is the single thing the table cannot know.
A graph nobody ran is an empty tree rather than a 404, because the client asking has already seen
a `task.*` event and a 404 would tell it to retry something that will never appear.

Scheduler events carry `source: "graph"`, which the UI store uses as its discriminator: a
`DELEGATION` task emits `task.*` twice over, once as graph state and once as its own lifecycle,
under the same `task_id`. Both are wanted. Guessing which is which from payload keys is what the
stamp prevents.

The `TaskTree` component renders status, dependencies, and the reason a task was skipped —
and keeps **ORACLE measured …** visually and structurally apart from **the worker said "…"**.
A UI that renders a claim as a verdict undoes the entire verification design at the last possible
moment, so a vitest asserts the two are not the same element, and another asserts `SKIPPED` and
`CANCELLED` do not render as the same word.

#### What Phase 7 leaves for Phase 8

- **Nothing creates graphs.** Every graph in the tests is hand-written; routing an intent to one
  is P8's first job, and the runners are constructed there.
- **A "gate" is still an event, not a card.** Recovery and the graph approval both need the UI
  P8 designs.
- **No task row carries a child PID**, so recovery cannot distinguish "process alive" from
  "process gone" (both gate). Worth revisiting only if the diagnostic is ever wanted.

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

### As built — replanning  `P8-T2, 2026-08-25`

`src/oracle/orchestration/replan.py` (the decision), a `replan` hook on the scheduler (the
trigger), `Planner.replan` + `approve_additions` in `runners/planning.py` (the spending), and
`TaskGraph.extend`. 45 tests across `tests/test_replanning.py` and
`tests/security/test_replan_authority.py`; no vendor in any of them except the stub CLI in the
concurrency test.

**Three layers, and the seam between them is the point.** The scheduler hands a failed *task* to
an injected hook and takes back a list of tasks to append. It does not know what a planner is,
what a budget is, or that anybody was asked to approve anything — so `scheduler.py` still imports
neither `plan.py` nor `replan.py`, and a security test asserts that against the source. The
decision lives in `replan.py` (pure, no I/O, reaches nothing); the money is spent in
`runners/planning.py`, which is already the layer allowed to see both sides.

| Question the design left open | As built | Why |
|---|---|---|
| Where the budget is counted | `budget_used(tasks)` = **distinct superseded task ids**, read from the rows | One replan authoring three tasks is one replan. Reading it from the table means a restarted daemon, a reconnecting client and the scheduler all agree, and there is no counter to forget to increment |
| Which failures are "a decision, not a problem" | error kind in `denied · refused · expired · halted · cancelled · approval_required · approval_invalid · interrupted` | The first five are a person (or a policy a person wrote) saying no. `interrupted` is on the list because recovery has already gated it: a supervisor that cannot prove what a child did while it was dead does not get to author its replacement |
| What `supersedes` means when a replan returns several tasks | **all of them** carry it, plus `parent_id` | A replan may answer one bad task with a research step and a narrower coding step. Nominating one of them as "the" replacement would be a lineage that reads well and is false |
| Whether a replan blocks the loop | No — it runs as a tracked child of the scheduler, like a parked task | A replan is a vendor call *and* a human decision. Inline, a graph would go silent for the length of an approval and its concurrency limit would be a lie. `graph.done()` is therefore `no active tasks **and** no replan outstanding` |
| Id collisions with the graph being joined | `compile_plan(..., id_prefix=f"{root}-r{n}")` → `tk_x-r1-a` | Same `root_id`, new namespace. The root is what makes the replacement visible in the same tree; only the name has to be new |
| The ceiling on a replanned graph | `MAX_GRAPH_TOTAL = 3 × MAX_GRAPH_SIZE` | Every plan, first or replacement, is still capped at 12. The total is what the per-plan cap and the ≤2 budget already imply, written down so nothing has to multiply it in its head |
| `PLANNING` tasks | Never replanned | A failed planning call answered by another planning call is the loop the budget exists to prevent |

**`extend()` is all-or-nothing and fully re-validated.** A replacement batch is checked as one
graph with everything already there: no duplicate ids, no dangling dependency, no cycle, one root,
under the ceiling. A batch that fails is refused whole and the failed task simply stays failed —
half a replan is a graph nobody designed.

**Evidence goes out; the claim does not, and cannot.** The planner is told the objective, what
failed, ORACLE's measurements of it, what never ran, and what else has already failed under this
root. The worker's `claim` is absent from the carrier — `Attempt` has no field for it — so the
separation is a missing field rather than a filter somebody has to remember to apply. Two tests
check it: one on the model, one on the bytes the adapter actually received.

**A `SKIPPED` dependent is named, never resurrected.** The failure context lists what did not run
and says in as many words that the plan must ask for it again if it is still wanted. Nothing flips
a terminal status back to eligible.

**Two questions, reusing both existing cards.** The replan egress is `ai.delegate` with the same
"up to 2 calls" bound and the same `sends_repo_contents: false`; its preview names which task is
being replaced and which of the two budgeted attempts this is. The additions card is `ai.graph`,
same tier, same `external` provenance — with `addition: true` and **only the new tasks** on it.
Re-showing the whole graph for two new rows is how a person is trained to click through a card
without reading it.

**An exhausted budget reports rather than shrugs.** `attempts_report()` names every attempt with
ORACLE's evidence, everything that was skipped, and the **branches and workspaces** the partial
work was harvested onto — `graph.replan_exhausted` on the event log. Worktrees are still not
cleaned up, so the keep/discard decision the delegation flow already offers has something to point
at. A report that says "it failed three times" without saying where the work went is a report that
throws the work away.

**Two workers, for real.** A plan authoring three independent `coder` tasks now runs two
delegations concurrently against real worktrees, each harvested to its own branch with a distinct
commit, while the third queues on the limit of 2 — the P7-T2 property re-proved on a graph nobody
hand-wrote.

**Not measured, and marked as such:** whether a real planner given the failure produces a
*materially different* plan rather than a rephrased one ([OQ-23](OPEN_QUESTIONS.md#oq-23)). The
prompt is a design decision; the budget makes a bad one cheap rather than correct.

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
