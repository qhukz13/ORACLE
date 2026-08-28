# ORACLE — Pipelines

Declarative, repeatable local workflows. **Phase 10** (renumbered 2026-08-24; was Phase 7).

> **Replan note 2026-08-24:** the executor is no longer a subsystem of its own — a pipeline
> **compiles to a task graph** and runs on the Phase 7 scheduler
> ([ORCHESTRATION.md](ORCHESTRATION.md)). Everything user-facing in this document — the YAML
> schema, validation-before-execution, one up-front approval, the scope guard, the litmus test —
> stands unchanged. "Steps → tool invocations through the same policy gate" now reads "steps →
> tasks", which is the same sentence with better machinery under it.
>
> **As-built note 2026-08-26 (P10):** built, and this document is corrected below rather than
> left as the design. §2's worked example named three things that do not exist — a `project`
> tool argument, `retry: { on: [...] }`, and an `oracle.report` step — and §3 offered
> `on_failure: ask` nine lines after saying "never a prompt mid-run". Each is corrected **in
> place**, with the reason, because a spec that disagrees with the code is worse than no spec:
> the next reader cannot tell which half is wrong. The shipped files are
> [`config/pipelines/`](../config/pipelines/).

## 1. Scope, fixed up front

A pipeline is **a named sequence of registered tool calls with conditions and artifacts.** That is
all it is, and the boundary is defended deliberately: pipeline DSLs grow into programming languages,
and a bad programming language embedded in YAML is worse than a shell script.

| In scope | Out of scope |
|---|---|
| Linear steps with `when` conditions | Matrix builds, parallel fan-out (v1) |
| Timeouts, retries, `on_failure` | Remote runners, distributed execution |
| Parameters with defaults | Loops, functions, arbitrary expressions |
| Artifact capture, structured reports | Caching layers, dependency graphs across runs |
| Per-project discovery | Replacing GitHub Actions |

**Litmus test:** if a pipeline needs branching logic and variables, it wants to be a script — and
`dev.execute` can run that script as a single step. Say no to the DSL feature.

## 2. Definition

Discovered from `.oracle/pipelines/*.yaml` inside each project, plus a global
`config/pipelines/*.yaml`.

```yaml
version: 1
name: asterim-check
description: Full health check before pushing Asterim.
project: Asterim

params:
  skip_frontend: { type: bool, default: false }

steps:
  - id: status
    tool: git.status
    with: { path: "{{ project.root }}" }

  - id: backend_tests
    tool: dev.run_tests
    with: { path: "{{ project.root }}", filter: "apps/server" }
    timeout: 600
    on_failure: abort

  - id: frontend_tests
    tool: dev.run_tests
    with: { path: "{{ project.root }}", filter: "apps/web" }
    when: "not params.skip_frontend"
    timeout: 600
    on_failure: continue          # report, but keep going

  - id: build
    tool: dev.build
    with: { path: "{{ project.root }}" }
    timeout: 900
    retry: { max: 1 }

artifacts:
  - { from: backend_tests, capture: stdout, as: backend.log }
  - { from: build,         capture: stdout, as: build.log }
```

**Four corrections to the example above**, each recorded because getting them wrong is how a
spec becomes fiction:  `AS BUILT 2026-08-26`

1. **`path:`, not `project:`.** No tool takes a `project` argument — `git.status`,
   `dev.run_tests`, `dev.build` and `dev.execute` all take a `ScopedPath`. And `{{ project }}`
   was ambiguous: the header's `project:` is a **name**, every tool argument needs a **path**.
   Split into `{{ project.name }}` and `{{ project.root }}`.
2. **No `retry: { on: [...] }`.** §3 below says retries are the tool contract's judgement, not
   the author's — `runners/tool.py::RETRYABLE_KINDS` is where it lives. `retry.max` says how
   many attempts; it does not say which failures earn one.
3. **No `report` step.** `oracle.report` is not a registered tool and none is planned. More
   fundamentally, a step downstream of a failure is `SKIPPED`, so a reporting *step* could
   never report on a failed run — the only run whose report anybody needs. The run record is
   written by `PipelineService` from the task rows after the scheduler returns.
4. **`capture: stdout | result`, not `junit`.** `dev.run_tests` parses results in-process and
   writes a text blob; there is no junit file on disk to capture.

The **timeouts got longer**, and that is a fix rather than a preference: `dev.run_tests`
declares 630 s in its own contract and the scheduler's per-kind default for a TOOL task is
120 s, so a test step was being killed at two minutes and recorded as `TIMEOUT`. P10 added
`Task.timeout_s` — the `task` level of the layering ORCHESTRATION.md §3 already specified —
and a step's `timeout:` sets it.

### Template expressions — intentionally tiny

Only `{{ params.x }}`, `{{ project.name }}`, `{{ project.root }}`, and `when` conditions limited to
boolean operators over those values. No arbitrary expressions, no function calls, no arithmetic. The
evaluator is a small, auditable, non-Turing-complete interpreter — **never `eval`**, because a
pipeline file is a place where injected content could otherwise become code execution.
`src/oracle/pipelines/template.py` is a whitelist tokeniser and a ~90-line recursive-descent
parser, depth-capped; `test_it_never_evals` reads the module's own AST to assert that neither
`eval`, `exec`, `compile` nor `literal_eval` is called and that `ast` is not even imported.

**`{{ steps.<id>.<field> }}` is refused in v1.**  `AS BUILT 2026-08-26`  A value that only
exists once the run has started cannot be resolved, priced or digest-bound *before* the run —
so the card that authorises the run could not show it, and §3's "ONE approval up front, listing
every step" would be false. It is also the *variables* half of §1's litmus. Steps share a
filesystem: a step that needs the previous one's output reads the file it wrote, and a step
that needs real branching is a script `dev.execute` runs in one step. The refusal names the
reason, because a bare "unknown namespace" would read as a bug.

## 3. Execution

```
validate  → schema · every tool exists · args match each tool's schema ·
            template refs resolve · no cycles  → FAIL FAST, with line numbers
   ↓
authorise → tier(pipeline) = max(tier(step) for step in steps)
            ONE approval up front for the whole run, listing every step
            that needs it — never a prompt mid-run
   ↓
compile   → each step becomes ONE Task(kind=TOOL); `on_failure` is edge construction
            and `when: false` omits a step rather than compiling it SKIPPED
   ↓
execute   → the Phase 7 scheduler; `runners/tool.py` makes each task an ordinary
            ToolInvocation through the POLICY GATE
            (a pipeline is not a privilege escalation path)
   ↓
observe   → ordinary `task.*` events — a pipeline is a task graph, so there is no
            second per-step event type and `TaskTree` renders a run with no new code
   ↓
report    → structured summary; **the run record IS the `tasks` rows keyed by root_id**
```

Two rules that matter:

- **Validation happens before anything runs.** A typo in step 5 must not be discovered after step 4
  has already pushed a branch.
- **Approval is up front, once.** Being interrupted at step 3 of 6 to approve something is exactly the
  prompt fatigue the security model tries to avoid
  ([SECURITY.md §2](SECURITY.md#2-design-principles)). The pre-run approval card lists every elevated
  step so the decision is informed.

`on_failure`: `abort` (default) · `continue` (record and proceed). Retries apply only to steps
declared retryable in their tool contract — retrying a non-idempotent step is a data-loss bug, so
the tool decides, not the pipeline author; `retry: { max: N }` sets how many attempts and nothing
about which failures earn one.

**`ask` is refused at validation.**  `AS BUILT 2026-08-26`  It contradicted the rule three lines
above it — *"Approval is up front, once … never a prompt mid-run"* — and `waiting` now names
`TaskStatus.WAITING`, a per-task scheduler state meaning "scheduled but parked", not a run state.
The document contradicted itself and the security model breaks the tie.

**No new table.**  `AS BUILT 2026-08-26`  "A run record in oracle.db" is the `tasks` rows keyed by
`root_id`, which are already durable, already recovered after a crash and already rendered by the
task tree. A second store of the same facts is a second thing to keep consistent.

**The tier rule is arithmetic, and `pipe.run`'s policy entry is a FLOOR.** `evaluate()` raises a
tool's tier to its `declared_tier` and never lowers it, so `config/policy.yaml` prices `pipe.run`
at **T0** and the run declares `max(tier(step))`. A run containing a T2 step is priced T2 and asks;
a run of nothing but reads asks nobody. A floor above T0 would make this section's own rule
unimplementable, which is why the entry carries a comment saying so.

**A T3 step is refused at validation.**  `AS BUILT 2026-08-26`  T3 needs the desktop and a phrase
typed *for that invocation* (SECURITY.md §5). Pre-approving one from a batch card would launder
`confirm_strong` into `confirm` while leaving the label alone, which is worse than not having the
tier. §8's criteria only ever promised T2.

**A pipeline from a repository is tainted.** `<project>/.oracle/pipelines/*.yaml` is repository
content — the same trust class as a checked-in `AGENTS.md` — so it carries `local_foreign`
provenance and the gate escalates it. `config/pipelines/*.yaml` is owner-authored configuration,
beside `policy.yaml`, and is not. The card says which, because the tier alone does not.

## 4. Cancellation

`graph.cancel`, with the run's `root_id`, cancels the running step (killing its process tree via the
job object) and marks remaining steps **`cancelled`**.  `AS BUILT 2026-08-26 — this section said
`skipped`, and that predates the status vocabulary P7 settled: `SKIPPED` means an ancestor failed
and this never ran, `CANCELLED` means a person stopped it. Collapsing them would lose the one
distinction somebody reading a stopped run needs — whether something broke or they pressed the
button.`  `AS BUILT 2026-08-26 — there is no `pipe.cancel`:
`Scheduler.cancel_root()` already does exactly this and is already proved against a real child pid,
and a second cancel command would be a second thing to keep correct.` Already-completed steps stay `completed` — a pipeline is not a transaction
and does not pretend to roll back. Where a step's tool declares an `undo`, the run view offers it as
an explicit follow-up action, chosen by a human.

## 5. Triggers

| Trigger | Phase |
|---|---|
| Manual (`pipe.run` command, or typing the pipeline's name — matched deterministically by the pre-router, with no model in the loop) | **10, built** |
| ~~Agent-initiated (a plan step)~~ — **forbidden.** `PlannedTask` forbids extra fields and `TaskSpec.tool` is set by the supervisor, never by a plan (ADR-0021). A pipeline is started by a person. | — |
| Scheduled (cron-like) | Post-MVP |
| On file change / on git event | **Not planned** — that is a watcher, and it invites runaway loops |

Scheduled runs need extra care: a pipeline firing while I'm asleep must never contain a step above T1,
and that is enforced at validation, not left to the author's judgement.

## 6. UI

The run view is the [Task Inspector](UI.md#6-task-inspector) with a step list — pipelines are tasks,
not a parallel concept, so they inherit cancellation, logging, the timeline, artifacts and cost for
free. Building a separate "pipeline runs" subsystem would duplicate all of it.

`AS BUILT 2026-08-26` — now literally true: `TaskTree.tsx`, `GET /api/v1/tasks` and
`GraphService.tree()` render a pipeline run with **no new code**, because its rows are ordinary
tasks. The one thing that *is* new is `PipelineCard.tsx`, the approval card — and it is a separate
component from `GraphCard` rather than a variant, because a graph's rows are
`{role, agent, objective, egresses}` and a pipeline's are `{step, tool, args, tier, rule}`. Sharing
one component would mean one of the two sets of columns is always a lie, and this is the only card
in ORACLE that authorises several actions at once.

## 7. Initial pipelines

Two, both real, built at Phase 10 and shipped in [`config/pipelines/`](../config/pipelines/):

- [`asterim-check`](../config/pipelines/asterim-check.yaml) — the example above.
- [`oracle-selfcheck`](../config/pipelines/oracle-selfcheck.yaml) — format, lint, types, tests,
  security suite, audit-log verification. ORACLE running its own quality gate is the most honest
  dogfooding available. Four of its six steps are `dev.execute` at T2, so it is also the live
  demonstration of §8's third criterion rather than a second fixture.

`test_the_two_shipped_pipelines_are_real` prices both against the **shipped** `config/policy.yaml`
and the **real** tool registry: every tool is looked up, every argument validated against that
tool's own model, every path canonicalised and every program pinned. Nothing executes, but a typo,
a renamed tool or an argument a contract does not take fails there — which is the difference
between "two files that parse" and the promise this section makes.

## 8. Acceptance criteria

- `asterim-check` runs end to end and reports per-step results.
- An invalid pipeline fails validation with a line number **before any step executes**.
- A run containing a T2 step asks exactly once, before starting.
- Cancelling mid-run kills the current step's process tree and marks the rest `skipped`.
- A pipeline cannot execute a tool the caller would not be allowed to execute directly. Asserted by a
  security test — this is the escalation path worth guarding.

### Met  `2026-08-26`

`tests/test_pipelines_{schema,loader,template,compile,end_to_end}.py` and
`tests/security/test_pipeline_authority.py`. What each criterion turned into:

| Criterion | Where it is asserted, and what it actually says |
|---|---|
| runs end to end, per-step results | `test_a_read_only_pipeline_runs_and_reports` — real executor, real gate, real scheduler; every step reports its status **and the rule that allowed it** |
| invalid → line number, before anything runs | `test_a_typo_in_the_last_step_stops_the_first` — the record says `invalid`, names the tool, and **no `task.created` event exists** |
| a T2 step asks exactly once, before starting | `test_a_t2_step_asks_exactly_once_and_before_any_task_exists` — one `approval.requested`, and its `seq` is **lower than the first `task.created`**. Plus `test_nothing_parks_mid_run`: no task ever reaches `WAITING` |
| cancel kills the step, skips the rest | `graph.cancel` on the run's `root_id`; already proved against a real child pid by `test_halt_reaches_a_graphs_child_process` |
| not a privilege escalation path | `tests/security/test_pipeline_authority.py` — 21 cases, most of them about the grants: bound to the digest the card showed, single-use, revoked in a `finally`, T3 refused at validation, a repository pipeline confined to its own project, a parameter that cannot become a traversal, and the package structurally unable to import the execution layer |
| *(roadmap)* identical event shapes to a hand-written graph | `test_the_two_produce_the_same_events_in_the_same_order` — normalised events compared element for element, and **no event type unique to the compiled one** |

**One deliberate v1 acceptance, stated rather than hidden.** A `continue` step and its successor
may run at the same time, because "nothing depends on it" is how `continue` is expressed and both
are in the `tool` slot class. The alternative is a completion-only edge in `graph.ready()`, which
means touching the fail-closed rule the whole graph algebra rests on — a P7-core change, and not
one to make on speculation about a pipeline nobody has written yet.
