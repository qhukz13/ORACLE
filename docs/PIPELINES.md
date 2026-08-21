# ORACLE — Pipelines

Declarative, repeatable local workflows. **Phase 7, Post-MVP.**

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
    with: { project: "{{ project }}" }

  - id: backend_tests
    tool: dev.run_tests
    with: { project: "{{ project }}", filter: "apps/server" }
    timeout: 300
    on_failure: abort

  - id: frontend_tests
    tool: dev.run_tests
    with: { project: "{{ project }}", filter: "apps/web" }
    when: "not params.skip_frontend"
    timeout: 300
    on_failure: continue          # report, but keep going

  - id: build
    tool: dev.build
    with: { project: "{{ project }}" }
    timeout: 600
    retry: { max: 1, on: ["timeout"] }

  - id: report
    tool: oracle.report
    with:
      template: check_summary
      inputs: [status, backend_tests, frontend_tests, build]

artifacts:
  - { from: backend_tests, capture: junit,  as: backend.xml }
  - { from: build,         capture: stdout, as: build.log }
```

### Template expressions — intentionally tiny

Only `{{ params.x }}`, `{{ project }}`, `{{ steps.<id>.<field> }}`, and `when` conditions limited to
boolean operators over those values. No arbitrary expressions, no function calls, no arithmetic. The
evaluator is a small, auditable, non-Turing-complete interpreter — **never `eval`**, because a
pipeline file is a place where injected content could otherwise become code execution.

## 3. Execution

```
validate  → schema · every tool exists · args match each tool's schema ·
            template refs resolve · no cycles  → FAIL FAST, with line numbers
   ↓
authorise → tier(pipeline) = max(tier(step) for step in steps)
            ONE approval up front for the whole run, listing every step
            that needs it — never a prompt mid-run
   ↓
execute   → each step is an ordinary ToolInvocation through the POLICY GATE
            (a pipeline is not a privilege escalation path)
   ↓
observe   → per-step events, logs, artifacts, durations
   ↓
report    → structured summary + a run record in oracle.db
```

Two rules that matter:

- **Validation happens before anything runs.** A typo in step 5 must not be discovered after step 4
  has already pushed a branch.
- **Approval is up front, once.** Being interrupted at step 3 of 6 to approve something is exactly the
  prompt fatigue the security model tries to avoid
  ([SECURITY.md §2](SECURITY.md#2-design-principles)). The pre-run approval card lists every elevated
  step so the decision is informed.

`on_failure`: `abort` (default) · `continue` (record and proceed) · `ask` (pause and ask; converts the
run to `waiting`). Retries apply only to steps declared `retryable` in their tool contract — retrying a
non-idempotent step is a data-loss bug, so the tool decides, not the pipeline author.

## 4. Cancellation

`pipe.cancel` cancels the running step (killing its process tree via the job object) and marks
remaining steps `skipped`. Already-completed steps stay `completed` — a pipeline is not a transaction
and does not pretend to roll back. Where a step's tool declares an `undo`, the run view offers it as
an explicit follow-up action, chosen by a human.

## 5. Triggers

| Trigger | Phase |
|---|---|
| Manual (`pipe.run`, palette, UI) | 7 |
| Agent-initiated (a plan step) | 7 |
| Scheduled (cron-like) | Post-MVP |
| On file change / on git event | **Not planned** — that is a watcher, and it invites runaway loops |

Scheduled runs need extra care: a pipeline firing while I'm asleep must never contain a step above T1,
and that is enforced at validation, not left to the author's judgement.

## 6. UI

The run view is the [Task Inspector](UI.md#6-task-inspector) with a step list — pipelines are tasks,
not a parallel concept, so they inherit cancellation, logging, the timeline, artifacts and cost for
free. Building a separate "pipeline runs" subsystem would duplicate all of it.

## 7. Initial pipelines

Two, both real, built at Phase 7:

- `asterim-check` — the example above.
- `oracle-selfcheck` — lint, typecheck, unit tests, security suite, audit-log verification. ORACLE
  running its own quality gate is the most honest dogfooding available.

## 8. Acceptance criteria

- `asterim-check` runs end to end and reports per-step results.
- An invalid pipeline fails validation with a line number **before any step executes**.
- A run containing a T2 step asks exactly once, before starting.
- Cancelling mid-run kills the current step's process tree and marks the rest `skipped`.
- A pipeline cannot execute a tool the caller would not be allowed to execute directly. Asserted by a
  security test — this is the escalation path worth guarding.
