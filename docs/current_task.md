# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P7-T3 — The graph's human surfaces: cancel, HALT, waiting, and something to look at**

**Phase:** [7 — task graph & supervisor](ROADMAP.md#phase-7--task-graph--supervisor--supervisor-arc) · **Scope:** Supervisor arc
**Status:** `SET — not started` · **Set:** 2026-08-25
**Previous task:** P7-T2 — **done** (one criterion met differently, recorded in
[`current_report.md`](current_report.md)); as-built in
[ORCHESTRATION.md](ORCHESTRATION.md#as-built--the-runners--p7-t2-2026-08-25).

---

## Why this task exists

A graph now schedules real work, verifies it against a baseline, and survives a crash without
lying about what it knows. Everything it does is invisible: cancellation exists as a method
nobody can call from outside, `WAITING` exists in the vocabulary and nothing enters it, and the
only way to see a graph is to read the `tasks` table by hand.

P7-T3 closes Phase 7 by connecting the supervisor to the two things that make it usable and safe:
**a person can stop it**, and **a person can see it**. Neither needs a planner, so Phase 7 still
ends without one.

## Requirements

1. **Cancel from outside.** A running graph is reachable: cancel one task, cancel the root. The
   scheduler already asserts `CANCELLED` itself; this is the path in — a `GraphRunner` (or
   equivalent) held by `AppState`, addressable by `root_id`, plus the WS/HTTP command surface
   that reaches it. Independent branches must keep running when one is cancelled.
2. **HALT across a running graph.** [AGENT_RUNTIME.md §7](AGENT_RUNTIME.md#7-cancellation-timeouts-halt)
   already terminates every job object and flips policy to deny-all; prove it covers a graph:
   every running task cancelled, every delegate's process tree gone, no orphan worktree lock,
   policy denying. A graph must add **no new HALT path** — if it needs one, the design is wrong.
3. **`WAITING`, for real.** A `TOOL` task whose call needs approval parks in `WAITING` instead of
   blocking a slot, and resumes when the approval resolves. Expired approval → the task fails
   with that reason. This is the state's first actual use; today nothing enters it.
4. **The API projection.** `GET /api/v1/tasks?root_id=…` returns the tree: tasks, statuses,
   dependencies, evidence summaries, and the `attempt`/`supersedes` lineage. It is a **query over
   the existing tables** ([ORCHESTRATION.md §6](ORCHESTRATION.md#6-observability)) — no parallel
   bookkeeping, and the WS `task.*` stream (with `source: "graph"`) is what makes it live.
5. **The minimal UI**: the existing task list renders a graph as a tree. No new page, no execution
   visualisation — that is P11. A person should be able to see which task is running, what
   failed, and why something was skipped.

## Constraints

- **Still no planner and no `ExecutionPlan`.** Graphs stay hand-written or fixture-loaded.
- No new HALT machinery. If a graph needs its own kill path, say so loudly rather than building
  one — it would mean the runners are holding processes the existing mechanisms cannot reach.
- The scheduler's import ban stands (`tests/security/test_orchestration_boundary.py`).
- API shape follows [API.md](API.md)'s existing conventions; a new endpoint is a projection, not
  a new source of truth.

## Acceptance criteria

- [ ] Cancelling one task of a running graph from the API leaves independent branches finishing,
      marks dependents `SKIPPED`, and the runner's own answer cannot override `CANCELLED`.
- [ ] Cancelling the root stops every non-terminal task; a test asserts no child process survives.
- [ ] HALT during a running graph: every task terminal, every process gone, policy denying, and
      **no code path added** to make it work.
- [ ] A `TOOL` task needing approval enters `WAITING`, frees its slot, and resumes on approval;
      an expired approval fails it with that reason recorded.
- [ ] `GET /api/v1/tasks?root_id=…` returns a tree that matches the `tasks` table, including a
      `SKIPPED` task's reason and a superseded attempt's lineage.
- [ ] The UI renders a four-task graph as a tree with live status; a vitest covers the projection.
- [ ] `make check` green, security suite included, with graph cases added to it:
      a graph cannot execute what its creator could not, and approvals bind per task.

## Relevant files

New: `src/oracle/orchestration/service.py` (the graph runner `AppState` holds) ·
`tests/test_orchestration_service.py` · `tests/security/test_graph_authority.py` ·
UI: the task list component + its test.
Modify: `src/oracle/api/app.py` · `src/oracle/api/routes` (the tasks endpoint) ·
`docs/API.md` · `docs/UI.md` · `docs/ORCHESTRATION.md` (as-built).
Read first: `src/oracle/core/terminal.py` (a long-lived, cancellable, WS-visible subsystem —
the closest existing shape) · `src/oracle/orchestration/scheduler.py` ·
[AGENT_RUNTIME.md §7](AGENT_RUNTIME.md#7-cancellation-timeouts-halt).

## Dependencies

P7-T1 and P7-T2 (both done). P8 waits on this; it is the last task of Phase 7.

## Risks

| Risk | Mitigation |
|---|---|
| `WAITING` grows into a scheduler rewrite | It is one state transition and one slot rule; if the diff to `scheduler.py` exceeds ~50 lines, stop and reconsider |
| The API projection duplicates state | It is a SELECT plus a shape; no caching, no second writer. A test asserts the endpoint and the table agree |
| HALT "works" because nothing was really running | The test must assert on a real child process, not a fake runner |

## Definition of done

All acceptance criteria · `make check` green · ORCHESTRATION.md, API.md and UI.md corrected to
as-built · `current_report.md` overwritten · this file set to **P8-T1** (planner integration),
naming what Phase 7 leaves for it — including whether a task row needs a child PID.
