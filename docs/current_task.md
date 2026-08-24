# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P7-T1 — The task graph: model, storage, algebra, and a scheduler that runs fake work**

**Phase:** [7 — task graph & supervisor](ROADMAP.md#phase-7--task-graph--supervisor--supervisor-arc) · **Scope:** Supervisor arc
**Status:** `SET — not started` · **Set:** 2026-08-24
**Previous task:** P6-T5 — **done**; report in [`current_report.md`](current_report.md), findings in
[`logs/development/2026-08-24-p6t5-antigravity-planning.md`](../logs/development/2026-08-24-p6t5-antigravity-planning.md).

---

## Why this task exists

Phase 7 is the load-bearing new subsystem, and it is the one part of the supervisor arc that
carries **no vendor risk at all**: a hand-written graph, deterministic runners, stub CLIs. P6-T5
just demonstrated why that ordering was right — the planner tier's default vendor failed its gate,
and Phase 7 did not care, because the graph does not depend on who authors it.

P7-T1 is the first of Phase 7's tasks: the durable task model, its storage, the graph algebra, and
a scheduler that runs *fake* runners end to end. Real runners (DELEGATION, TOOL), recovery and
concurrency are P7-T2 and P7-T3 — this task exists so that everything after it has something
correct to plug into.

## What P6-T5 hands you (read these before designing)

Six findings from the spike change P7's design, not just its context:

1. **A cancelled run can be indistinguishable from a failed one.** `agy` reports an interrupted
   run as `status: ERROR` / "timeout waiting for response" — never `CANCELED`
   ([INTEGRATIONS.md §5](INTEGRATIONS.md#5-antigravity--supported-unblocked-2026-08-21)). So the
   scheduler **records that it cancelled**; it never infers cancellation from a runner's result.
   Asterim's `SKIPPED ≠ CANCELLED` and `TIMEOUT ≠ FAILED` distinctions are only preservable if the
   supervisor is the one asserting them.
2. **Plans arrive with no edges.** Five of twelve valid plans were DAGs with zero dependencies
   ([OQ-20](OPEN_QUESTIONS.md#oq-20)). A graph of independent tasks is therefore the *common* case,
   not the degenerate one: the scheduler must be correct and legible when the ready set is
   "everything", and P8's approval card will need to show a zero-edge plan as the smell it is.
3. **A task's own acceptance criteria cannot be the verification contract.** Planners write
   criteria like "pytest tests/test_scheduler.py passes" naming files that do not exist yet. The
   verification a task's status depends on is ORACLE's — diff plus its own test run — exactly as
   `DelegationService` already does it.
4. **`structured_output` can be silently emptied.** Validate every collection for emptiness before
   trusting a runner's structured result (PLANNER.md §2, check 0).
5. **Verification is a delta, not a threshold.** A pristine worktree of this repo fails 28 tests
   for environment reasons (no `.venv`, so suites that spawn a binary die). The delegate's worktree
   failed the same 28 and passed 5 more. A VERIFY task that reads "failures > 0" as failure would
   reject every correct delegation — so it baselines the suite once per graph and compares.
6. **A delegation's result exists only while its worktree does.** Delegates are forbidden git
   commands, so the diff is uncommitted; `discard()` then deletes it. Fine for one delegation
   (the diff was evidence), fatal for a graph where task C's output is task D's input. **P7 owes a
   harvest step**: ORACLE commits the worktree's diff to the task's branch at collect time. See
   Finding 8 in the dev log — it cost the P6-T5 run its artifact to notice.

## Requirements

1. **Task model** (`src/oracle/orchestration/`, pydantic): `Task`, `TaskResult`, `TaskKind`
   (TOOL · DELEGATION · VERIFY · REPORT), and the Asterim status vocabulary — `PENDING`, `READY`,
   `RUNNING`, `PASSED`, `FAILED`, `TIMEOUT`, `SKIPPED`, `CANCELLED` — with the distinctions in
   [ORCHESTRATION.md §3](ORCHESTRATION.md#3-the-graph) preserved, not collapsed.
2. **Migration `0002` — the `tasks` table**, per the schema specified in ORCHESTRATION.md. Durable
   means: the graph survives a daemon restart with no event gap, and the migration is applied by
   the existing runner (`storage/db.py`), tested like the others.
3. **Graph algebra**, ported from Asterim ([ASTERIM_REUSE.md](ASTERIM_REUSE.md) Tier 1) and
   property-tested: cycle detection that returns the cycle **as a path** (iterative, so adversarial
   input cannot blow the stack) · `ready_set` (pending ∧ all deps PASSED → fail-closed for free) ·
   status aggregation with precedence CANCELLED > FAILED > RUNNING > PASSED.
4. **The scheduler loop** with *fake* runners: batched topological scheduling, a width limit,
   per-task timeout, cancel-one-branch, and `task.*` events for a graph rather than a single
   delegation. Deterministic under test — no wall-clock sleeps in the assertions.
5. **The graph is fed through the existing gate, not beside it.** Every TOOL task is an ordinary
   `ToolInvocation`; the scheduler feeds the policy engine and is not a second one
   ([SECURITY.md §10](SECURITY.md#10-the-multi-agent-surface--added-2026-08-24-phases-78) rule 1).

## Constraints

- **No planner, no `ExecutionPlan`, no vendor.** Graphs are hand-written in tests and fixtures.
  Phase 8 wires plans in; if this task touches a planner, the scope fence failed.
- No UI beyond what already exists; the execution tree is P11.
- `DelegationService` is *reused*, not rewritten — P7-T2 renames it to the DELEGATION runner with
  its lifecycle intact. Do not refactor it in this task.
- The single-turn pipeline and single-delegation path stay the default and stay untouched.

## Acceptance criteria

- [ ] A hand-written 4-task graph (tool → delegation → verify → report) runs end to end with fake
      runners; order, gating and events asserted the way `test_reference_scenario.py` asserts them.
- [ ] Cycle detection returns the offending cycle as a path, proven on an adversarial fixture.
- [ ] One task fails mid-graph: dependents become `SKIPPED` (not `CANCELLED`), aggregate status
      follows the precedence rule, and the reason is recorded.
- [ ] A cancelled branch is recorded as `CANCELLED` **by the scheduler's own record**, and a
      runner that reports an ambiguous error does not change that.
- [ ] The `tasks` migration applies, round-trips a graph, and the graph reloads after a restart.
- [ ] A task's result survives its workspace: the harvest step commits the diff to the task branch,
      and a test proves the result is still there after the worktree is discarded.
- [ ] `make check` green, security suite included.

## Relevant files

New: `src/oracle/orchestration/{models,graph,scheduler}.py` ·
`src/oracle/storage/migrations/0002_tasks.sql` · `tests/test_orchestration_graph.py` ·
`tests/test_orchestration_scheduler.py`.
Read first: [ORCHESTRATION.md](ORCHESTRATION.md) §3–§4 · [ASTERIM_REUSE.md](ASTERIM_REUSE.md)
Tier 1 · `src/oracle/delegation/service.py` (the lifecycle a runner will wrap) ·
`tests/test_reference_scenario.py` (how an end-to-end order assertion is written here).

## Dependencies

None. Phase 8 waits on this; nothing waits on Phase 8.

## Risks

| Risk | Mitigation |
|---|---|
| Scope creep toward a workflow engine | The litmus is ORCHESTRATION.md §8; fake runners only in this task |
| Windows asyncio scheduler edge cases | OQ-16's rule (pipes on threads); no new subprocess handling here — runners are fake |
| The status vocabulary collapsing under implementation pressure | Each distinction gets a test that fails if two states are merged |

## Definition of done

All acceptance criteria · `make check` green · ORCHESTRATION.md corrected to as-built where it
guessed · `current_report.md` overwritten · this file set to **P7-T2**.
