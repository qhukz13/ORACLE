# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P7-T2 — the real runners: tool, delegation, verify, and what happens after a crash.
**Done: six of seven acceptance criteria; the seventh is met differently and says so below.**
**Status:** `src/oracle/runners/` + `orchestration/recovery.py`. **74 orchestration tests**;
everything below the vendor is real and only the CLI is a stub. `make check` green, security
suite included.
**Date:** 2026-08-25

---

## What was built

- **`runners/tool.py`** — a `TOOL` task is an ordinary `ToolInvocation` through the existing
  `ToolExecutor`: same registry, same gate, same audit entry. The judgement it adds is which
  failures are retryable (a denial never is).
- **`runners/delegation.py`** — `DelegationService` wrapped, not rewritten. Splits the lifecycle's
  single dict into **evidence** (exit code, diff, tests, branch) and **claim** (what the agent
  said), and harvests the result onto the task's branch before anything can discard the worktree.
- **`runners/verify.py`** — the baseline comparison, below.
- **`orchestration/recovery.py`** — read what was in flight, mark it, announce it, restart nothing.
- **`api/app.py`** — `AppState` carries a `TaskStore`; recovery is awaited at startup, before any
  other work begins.

`runners/` sits outside `orchestration/` on purpose: the scheduler may not import the layers that
execute, and a security test enforces that against the source. The runners are a composition
layer, like `api/app.py` — allowed to see both sides, which is what composing means.

## Verification is a delta, not a threshold

The measurement from P6-T5, now load-bearing: a **pristine** worktree of this repo fails 28 tests
(no `.venv`, so suites that spawn a binary die); the delegate's worktree failed the same 28 and
passed five more. A verifier reading "failures > 0" as failure would reject every correct
delegation this repo can produce.

So `VERIFY` runs the suite in the worker's workspace, runs it once per graph in a clean one, and
reports `new_failures`, `fixed`, `delta_passed`. **No baseline, no verdict** — if the baseline
cannot be taken, the task fails and says why rather than falling back to a threshold. The
workspace it checks comes from the dependency's *row*; the row is the record, never the claim.

## Two bugs the tests found

**Harvest was gated on `diff_lines`** — which counts *tracked* changes only. A worker whose output
is new files (a new module, a new test, a recorded fixture) produces none, so its work would never
have been committed, and the P6-T5 hole would have stayed open for the most common case. Harvest
is now attempted whenever a worktree exists; `harvest()` decides for itself whether anything was
staged.

**A three-approval test approved one and let two expire.** The "wait for the next approval" helper
restarts the event stream from seq 0 and returns the first match, so calling it in a loop re-reads
the same request forever. Three concurrent egresses need one subscription and three answers. Worth
recording because the failure looked like a scheduler concurrency bug and was a bug in how the
test listened.

## Crash recovery, and what it honestly cannot do

Every `RUNNING` task becomes `FAILED(interrupted)` — never retried, never restarted; dependents are
`SKIPPED` on the next pass, so nothing proceeds on a result nobody verified. Unstarted tasks are
left alone and reported. One `system.degraded` event names everything found; a clean shutdown
emits nothing, because a recovery event on every start trains everyone to ignore recovery events.

**ORACLE records no child PID**, so ORCHESTRATION.md §3's "process alive → gate" / "process gone →
`FAILED(interrupted)` → gate" split collapses into its conservative branch. Both branches gate, so
no decision changes — what is lost is being able to say which happened. Adding `pid` is a
migration plus a scheduler hook in exchange for a diagnostic, so it waits for a task that needs
the diagnostic. And "gate" today means a critical event, not a card: the graph approval UI is P8.

## The criterion met differently

My own task file listed `api/app.py` under "construct and inject the runners". The store and
recovery are wired, because both have an effect today. **The runners are not constructed there** —
nothing creates graphs until P8 routes an intent to one, and building runners that nothing calls
is dead code wearing the costume of integration. P8's entry point constructs them from the same
factories the tests use.

## Tests

74 across five files. The ones that pin judgement rather than arithmetic:

- the four-task graph (tool → delegation → verify → report) through the **real** lifecycle, with
  the stub CLI standing in for Claude, asserted by order and by events;
- a delegation's evidence and claim proven separate, with `result_text` absent from `evidence`;
- a harvested result read back **after** its worktree is discarded;
- a refused egress that never submits and never retries;
- three real delegations under a limit of two: distinct workspaces, distinct branches, peak 2;
- pre-existing failures not failing a verification, a new failure failing it, a missing baseline
  refusing to judge;
- a suite that did not run recorded as "not verified", never as a pass — P6-T5's false green;
- an interrupted graph whose dependents refuse to run after recovery.

## Next

**P7-T3** ([current_task.md](current_task.md)): the graph's human surfaces — cancellation from
outside a running graph, HALT across one, the `WAITING` state and approval-parking, and the
API projection a task tree can be read from.

## Unresolved

[OQ-18](OPEN_QUESTIONS.md#oq-18) (recall 61% vs an 80% gate — Phase 9) ·
[OQ-19](OPEN_QUESTIONS.md#oq-19) (Agent SDK, trigger-based) ·
[OQ-21](OPEN_QUESTIONS.md#oq-21) (MCP spec migration, watch) · `agy`'s unauthenticated preflight
state, still unobserved · whether `agy` works with the Antigravity IDE closed · whether a task row
should carry a child PID (above).
