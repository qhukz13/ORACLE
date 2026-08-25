# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P7-T2 — The real runners: tool, delegation, verify, and what happens after a crash**

**Phase:** [7 — task graph & supervisor](ROADMAP.md#phase-7--task-graph--supervisor--supervisor-arc) · **Scope:** Supervisor arc
**Status:** `SET — not started` · **Set:** 2026-08-25
**Previous task:** P7-T1 — **done**, all acceptance criteria; see
[`current_report.md`](current_report.md) and
[ORCHESTRATION.md "As built"](ORCHESTRATION.md#as-built--p7-t1-2026-08-25).

---

## Why this task exists

P7-T1 built a supervisor that schedules correctly and executes nothing: runners are injected, and
every test supplies its own coroutine. That was the point — the scheduling logic is now decided
without any vendor in the room. This task connects it to the machinery that already works.

Nothing here is new capability. `DelegationService` runs a delegation end to end today;
`ToolExecutor` runs a gated tool call today; `dev.run_tests` verifies today. P7-T2 is **wiring,
plus the two things P6-T5 proved the wiring must not get wrong**: a verifier without a baseline,
and a result that does not outlive its worktree.

## Requirements

1. **The TOOL runner.** One task → one `ToolInvocation` through the existing `ToolExecutor`. The
   scheduler must remain unable to import the tool layer: the runner is constructed in the daemon
   (`api/app.py`) and injected, exactly as the fake ones are in tests. Approval-requiring calls
   park the task in `WAITING` — the state exists in the vocabulary and nothing enters it yet.
2. **The DELEGATION runner**, wrapping `DelegationService` **without rewriting it**. Its lifecycle
   (render → preflight → gate → approval → worktree → adapter → collect → verify) is intact and
   already tested; this runner adapts `ActiveDelegation` into a `TaskResult`, putting ORACLE's diff
   and test evidence in `evidence` and the agent's own words in `claim`. The rename to
   "DELEGATION runner" is a docs-and-wiring change, not a refactor.
3. **Harvest on collect.** Call `Worktree.harvest()` when a delegation produces a diff, and
   `discard(keep_branch=True)` afterwards, so the result outlives the workspace (built and tested
   in P7-T1; unused until now). The task's `evidence` records the commit sha — that sha is how a
   dependent task, a reviewer, or a merge finds the work.
4. **The VERIFY runner, with a baseline.** P6-T5 measured 28 test failures in a *pristine* worktree
   of this repo (no `.venv`, so suites that spawn a binary die); the delegate's worktree failed the
   same 28 and passed 5 more. **A verifier that reads "failures > 0" as failure rejects every
   correct delegation.** So: baseline the suite once per graph, compare, and report the delta as
   the evidence. A verifier that cannot obtain a baseline says so and fails open to a human — it
   does not guess.
5. **Startup recovery**, per [ORCHESTRATION.md §3](ORCHESTRATION.md#crash-recovery): load
   `TaskStore.unfinished()`; for each `RUNNING` task, gate rather than resume — child alive → gate,
   child gone → `FAILED(interrupted)` and gate, **never auto-restart**. Corrupt state gates loudly.
6. **The four-task graph, for real.** The P7-T1 end-to-end test with the stub CLI in place of the
   fake delegation runner: the same order assertions, now through the real lifecycle.

## Constraints

- **Still no planner and no `ExecutionPlan`.** Graphs stay hand-written. P8 wires plans in.
- `DelegationService`'s lifecycle is not to be re-architected. If this task produces a large diff
  in `delegation/service.py`, the scope fence failed.
- The scheduler's import ban stands — `tests/security/test_orchestration_boundary.py` enforces it,
  and the fix for a violation is "inject it", never "add the import".
- No UI work; the execution tree is P11.

## Acceptance criteria

- [ ] A four-task graph runs end to end with the **stub Claude CLI** for the delegation, asserted
      by order and by events, like `test_reference_scenario.py`.
- [ ] A delegation's `TaskResult` carries ORACLE's diff/test evidence in `evidence` and the
      agent's report in `claim`, and `evidence` alone gates the dependent task.
- [ ] The harvested commit sha appears in `evidence`, and a test proves the work is reachable after
      the worktree is discarded.
- [ ] VERIFY reports a **delta against a baseline**; a test with a repo whose suite fails
      identically before and after proves the delegation is not marked failed for it.
- [ ] Kill the daemon mid-graph (or simulate it): on restart the interrupted task gates, nothing
      auto-restarts, and there is no event gap.
- [ ] Two delegations run concurrently in separate worktrees without interference; the third queues.
- [ ] `make check` green, security suite included.

## Relevant files

New: `src/oracle/orchestration/runners.py` · `tests/test_orchestration_runners.py` ·
`tests/test_orchestration_recovery.py`.
Modify: `src/oracle/api/app.py` (construct and inject the runners) · `docs/ORCHESTRATION.md`
(as-built) · `docs/AGENT_RUNTIME.md` (the graph as a third delegation entry path, if it lands).
Read first: `src/oracle/delegation/service.py` · `src/oracle/orchestration/scheduler.py` ·
`tests/helpers_delegation.py` · [ORCHESTRATION.md §3–§4](ORCHESTRATION.md).

## Dependencies

P7-T1 (done). P8 waits on this.

## Risks

| Risk | Mitigation |
|---|---|
| The DELEGATION runner turns into a rewrite of `DelegationService` | Judge by diff size in `delegation/`; the runner is an adapter, and adapters are small |
| Recovery tests that depend on real process death are flaky on Windows | Simulate the crash at the store level (rows say RUNNING, no live process) and test the *rules*; one real kill as a separate, tolerant test |
| The baseline suite run makes every graph slow | Baseline once per graph, not per task; cache it on the root; measure and record the cost |

## Definition of done

All acceptance criteria · `make check` green · ORCHESTRATION.md's as-built section extended ·
a dev log if recovery or the baseline turns up anything non-obvious · `current_report.md`
overwritten · this file set to **P7-T3**.
