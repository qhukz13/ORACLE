# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P8-T2 — Replanning: a failure buys one more idea, not an afternoon**

**Phase:** [8 — planner integration & multi-worker](ROADMAP.md#phase-8--planner-integration--multi-worker--supervisor-arc) · **Scope:** Supervisor arc
**Status:** `SET — not started` · **Set:** 2026-08-25
**Previous task:** P8-T1 — **done**; see [`current_report.md`](current_report.md) and
[PLANNER.md §8](PLANNER.md#8-as-built--p8-t1-2026-08-25).

---

## Why this task exists

A graph now runs a plan, and a failed task stops its branch dead. That is correct and it is not
enough: the single most common real failure — "the worker misunderstood, or the context was
wrong" — is one a supervisor can act on, once, without a human.

`supersedes` is written by the store and populated by nothing. This task populates it, under a
budget, and makes the lineage visible. It is also where a graph first runs **two workers at
once** against real worktrees rather than in a test's imagination.

## What the earlier phases hand you

1. **Replanning is append-only** (ADR-0020). Nothing is rewritten: the failed task keeps its row,
   its evidence and its place; the replacement points at it with `supersedes`.
2. **The budget is ≤ 2 replans per root** ([ORCHESTRATION.md §4](ORCHESTRATION.md#4-failure-and-replanning)).
   Unbounded replanning is how an agent burns an afternoon achieving nothing.
3. **The planner is invoked with the failure**, never a blank slate: the original objective, the
   failed task's spec, **ORACLE's evidence** (not the worker's claim), and prior attempts.
4. **Evidence gates, claims do not.** A worker saying "I fixed it" is not a reason to skip the
   VERIFY task that says otherwise.
5. **Escalation order, cheapest first**: retry (if retryable) → replan → fallback agent → human.
   Every rung must be visible in the lineage, not inferred from logs.

## Requirements

1. **The replan decision**, in the scheduler or beside it: a `FAILED`/`TIMEOUT` task whose root has
   budget left triggers one planning call carrying the failure. A task that failed because a human
   refused something is **not** replanned — that is a decision, not a problem to route around.
2. **Append, never rewrite**: new tasks arrive with `supersedes` set and `parent_id` recording
   lineage; the failed task stays `FAILED`; dependents that were `SKIPPED` become eligible again
   only through the replacement, never by resurrection.
3. **Re-validation and re-approval**: a replan's tasks are validated exactly like a first plan, and
   the graph card is shown again for the *added* tasks. A replan is new work and gets a new
   decision — but the card must show it as an addition, not as the whole graph again.
4. **The budget, enforced and visible**: ≤ 2 per root, counted on the root task, reported in the
   tree. Exhausted budget → the root fails with a report of everything tried, and the keep/discard
   decision the delegation flow already offers for a partial result.
5. **Two workers, concurrently, for real**: a graph with two independent `coder` tasks runs both
   delegations at once in separate worktrees, each harvested to its own branch, and the third
   queues (the limit is 2). P7-T2 proved this with the stub CLI; do it with a graph a plan
   authored.
6. **The lineage in the API and the UI**: `GET /api/v1/tasks?root_id=` already returns
   `supersedes`; the `TaskTree` shows a superseded attempt collapsed under its replacement rather
   than hiding it. Nothing is erased, because the event log does not erase.

## Constraints

- **One replan per failure, two per root.** If a third appears, the budget was a suggestion.
- No new approval *types* — the graph card is reused for additions.
- The scheduler's import ban stands; replanning composes, it does not reach.
- Do not touch the single-delegation path or the single-turn pipeline.

## Acceptance criteria

- [ ] A failed task with budget produces exactly one planning call carrying ORACLE's evidence, and
      the new tasks arrive with `supersedes` set.
- [ ] A refused approval does **not** trigger a replan; a test says so.
- [ ] The budget holds: a graph that keeps failing stops after 2 replans and reports what was
      tried, with every attempt still readable in the tree.
- [ ] A replan's added tasks are validated like any plan and approved on a card that shows them as
      additions.
- [ ] Two delegations from one plan run concurrently in separate worktrees, both harvested; a
      third queues.
- [ ] The tree shows a superseded attempt beside its replacement; a vitest covers the rendering.
- [ ] `make check` green, security suite extended: a replan cannot widen what the original graph
      was allowed to do.

## Relevant files

New: `src/oracle/orchestration/replan.py` · `tests/test_replanning.py` ·
`tests/security/test_replan_authority.py`.
Modify: `src/oracle/orchestration/scheduler.py` (the trigger — keep it small) ·
`src/oracle/runners/planning.py` (the failure-carrying prompt) · `apps/desktop/.../TaskTree.tsx` ·
`docs/ORCHESTRATION.md`, `docs/PLANNER.md` (as-built).
Read first: [ORCHESTRATION.md §4](ORCHESTRATION.md#4-failure-and-replanning) ·
[PLANNER.md §8](PLANNER.md#8-as-built--p8-t1-2026-08-25) · `src/oracle/orchestration/plan.py`.

## Dependencies

P8-T1 (done). P9 (memory) wants the attempt records this produces.

## Risks

| Risk | Mitigation |
|---|---|
| Replanning becomes an agentic loop | The budget is on the root and counted in one place; a test drives a graph that always fails and asserts it stops |
| The scheduler grows a planner-shaped hole in it | The trigger emits a *request*; the composition layer decides. If `scheduler.py` starts importing `plan.py`, stop |
| A replan quietly widens scope | Re-validated against the same registry and projects, and the security test asserts the added tasks cannot reach what the original could not |

## Definition of done

All acceptance criteria · `make check` green · ORCHESTRATION.md and PLANNER.md corrected to
as-built · a dev log if the replan prompt needs real measurement to be useful ·
`current_report.md` overwritten · this file set to **P8-T3** or **P9-T1**, whichever the state of
Phase 8 warrants.
