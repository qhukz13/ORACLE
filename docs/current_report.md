# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P7-T3 — the graph's human surfaces: cancel, HALT, waiting, and something to look at.
**Done: all seven acceptance criteria. Phase 7 is complete.**
**Status:** A graph can now be stopped and seen. `GraphService`, `Parked`/`WAITING`,
`GET /api/v1/tasks`, the `graph.cancel` command, and a `TaskTree` in the desktop UI.
`make check` green.
**Date:** 2026-08-25

---

## Phase 7, finished

ORACLE runs multi-task graphs: durable, dependency-ordered, verified against a baseline,
crash-safe, stoppable, and visible — **with no planner anywhere in it**. That ordering was the
point. When P6-T5's planner spike came back "no", Phase 7 did not care, because a graph does not
depend on who authored it.

## What P7-T3 added

- **`orchestration/service.py`** — `GraphService` holds live schedulers by `root_id`, the way
  `TerminalBridge` holds shells. It does not build graphs and does not own runners; both are
  passed in, so composition stays in the daemon and this file imports nothing that executes.
- **`Parked`** in the scheduler — a runner can say "take my slot back until this completes".
- **`GET /api/v1/tasks?root_id=`** — a projection over the `tasks` table.
- **`graph.cancel`** WS command — one task, or the whole graph.
- **`TaskTree.tsx`** + a `graphs` slice in the store, folded from `task.*` events stamped
  `source: "graph"`.

## HALT needed no new path — and there was a real gap behind that

Cancelling a graph's coroutine does **not** cancel the runner tasks it spawned: they are
independent asyncio tasks. Left alone, HALT would have killed the supervisor and left a vendor
process running — the exact orphan HALT exists to prevent.

The fix belongs to the scheduler, not to HALT: `_abandon()` cancels its own children on
`CancelledError`, and everything downstream already worked (delegation runner → `DelegationService`
→ adapter → process). **No HALT path was added**, and
`test_halt_reaches_a_graphs_child_process` asserts on a **real child pid** — a HALT proven against
fake runners is a HALT that has never been tested. It also asserts the row says `CANCELLED`, not
`RUNNING`: a task left `RUNNING` would be read as an interrupted agent by the next start-up, which
is a stronger claim than the truth.

## `WAITING`, and what the scheduler is not allowed to know

A `TOOL` task the gate wants confirmed returns `Parked(reason, until)`. The scheduler sets
`WAITING`, **frees the slot**, and re-dispatches when `until` completes — knowing nothing about
approvals. The seam is "wait on this awaitable and try me again", so a future park on a rate limit
or a lock needs no new concept and the import ban stays intact.

Two rules the runner learned by failing a test:

- **Ask once per task.** The first version re-asked on the resumed attempt, so a *refused* task
  parked → resumed → asked again → parked again, forever, asking the person who said no every few
  milliseconds. A refusal now falls through to the gate's own `APPROVAL_REQUIRED`.
- **A grant belongs to one task.** Two tasks making the identical call are two decisions; the
  second asks for itself. Otherwise a graph of twelve identical calls costs one click.

## What the UI must not do

`TaskTree` keeps **ORACLE measured …** and **the worker said "…"** as different elements with
different labels, and a vitest asserts they are not the same node. The backend keeps evidence and
claim apart through the runner, the store, and the API; the screen is the last place the
distinction could be thrown away.

Likewise `skipped` renders as *"skipped — an earlier task did not succeed"*, because "skipped"
alone reads as a choice somebody made. A test asserts it does not read like `cancelled`.

## Tests

**88 orchestration tests** across six files, plus 8 `TaskTree` tests and 4 store tests in the
UI. Notable:

- HALT against a real wedged child process, with the row's final status checked;
- a parked task proving it freed its slot — a second task finishes while the first waits;
- a cancelled parked task that an approval answered *afterwards* does not resurrect;
- one task's approval failing to authorise another task's identical call;
- a graph's denied tool call getting the same rule and the same verdict as a direct one;
- the API projection agreeing with the table, including a skip reason and the evidence/claim split.

## A note on the gate

`make check` stalled once on the pytest step and I killed it; a clean re-run passes in the usual
time (414 python tests in ~82 s). Two stale `uv` processes from two hours earlier were sitting on
the machine and were cleared at the same time. **I could not reproduce the stall**, so it is
recorded here rather than explained — if it recurs, the first suspect is a test that leaves a
`STUB_HANG` child behind.

## Next

**P8-T1** ([current_task.md](current_task.md)): the planner tier — intent → plan → validated
graph → the graph approval card. The first task that creates a graph rather than running a
hand-written one, and the first that constructs the runners in the daemon.

## Unresolved

[OQ-18](OPEN_QUESTIONS.md#oq-18) (recall 61% vs an 80% gate — Phase 9) ·
[OQ-19](OPEN_QUESTIONS.md#oq-19) (Agent SDK, trigger-based) ·
[OQ-21](OPEN_QUESTIONS.md#oq-21) (MCP spec migration, watch) · `agy`'s unauthenticated preflight
state, still unobserved · whether a task row should carry a child PID (recovery cannot tell
"alive" from "gone"; both gate) · the unexplained gate stall above.
