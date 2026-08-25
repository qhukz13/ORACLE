# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P7-T1 — the task graph: model, storage, algebra, and a scheduler that runs fake work.
**Done: all seven acceptance criteria.**
**Status:** `src/oracle/orchestration/` exists — 4 modules, migration `0002`, **29 tests**, all
offline and deterministic. `make check` green, security suite included. No planner, no vendor, no
real runner: those are P7-T2, by design.
**Date:** 2026-08-25

---

## What was built

`src/oracle/orchestration/`:

- **`models.py`** — `Task`, `TaskSpec`, `TaskResult`, `TaskError`, `Cost`, and the two enums.
  Transitions are copies (`with_status`), because the row is the record and a mutated object in
  someone's local variable is not.
- **`graph.py`** — the algebra ported from Asterim: validation, cycle-detection-as-a-path, the
  ready set, the blocked set, aggregation. Pure functions, no clock, no I/O.
- **`store.py`** + **`0002_tasks.sql`** — the durable `tasks` table. Written before announced, so
  recovery can trust the row to be at least as current as the world.
- **`scheduler.py`** — batched topological dispatch with per-slot concurrency limits, per-kind
  timeouts, retry-if-retryable, the skip cascade, and cancellation. Runners are **injected**.

Plus `Worktree.harvest()` in the workspace layer — see below.

## The three decisions worth arguing about

**1. Cancellation and timeout are the scheduler's assertions, never a runner's.** This is P6-T5's
finding turned into code: a cancelled `agy` run reports `status: ERROR` / "timeout waiting for
response", identical to a genuine vendor timeout. If a runner's answer could set these states,
`TIMEOUT ≠ FAILED` and `SKIPPED ≠ CANCELLED` would survive in the enum and die in practice. So
`_record()` checks "did I cancel this?" *before* it reads the result, and a test hands it a runner
that lies about being cancelled to prove the ordering holds.

**2. Ready means every dependency SUCCEEDED — not "finished".** One line, and it makes the graph
fail-closed with no special-casing anywhere: a failure, a timeout or a cancellation all stop
downstream work by the same rule, and the skip cascade becomes a consequence rather than a feature.

**3. The scheduler executes nothing.** It imports no tool, toolhost, policy or LLM module, and a
security test parses the AST of every file in the package to keep it that way. A task whose kind
has no runner **fails, visibly, having run nothing** — the alternative, falling through to a
default path, is exactly the second chokepoint SECURITY.md §10 forbids.

## The harvest step — the hole P6-T5 fell into

`Worktree.harvest(message)` commits a worker's diff to the task's own branch;
`discard(keep_branch=True)` then removes the checkout without removing the work.

Delegates are forbidden git commands (a delegate that commits has hidden its own diff), so a result
lived only in the working tree — and `discard()` deleted it. Harmless when the diff is evidence to
be *read* once; fatal in a graph, where task C's output is task D's input. **ORACLE commits, the
delegate still may not**, after `diff()` has been read, under this machine's git identity — a
security test asserts the commit author, because a commit attributed to an agent would be a
provenance lie in the one place provenance is checkable.

## Tests

29 across three files. Notable ones, because they pin judgement rather than arithmetic:

- the four-task graph (tool → delegation → verify → report) asserted by **execution order**;
- a graph with **no edges** dispatching all four at once — the common case, per OQ-20's finding
  that five of twelve valid plans declared no dependencies at all;
- concurrency limits measured by the runners' own high-water mark, not by wall clock;
- a failure skipping its dependents while an **independent branch still finishes**;
- a denial never retried, however many attempts remain;
- the cycle walk over a 5,000-node chain — the graph may come from an untrusted planner;
- durability across a **closed connection**, not just a shared one;
- and the security file's AST-level import ban.

## What is deliberately not built

Real runners (`TOOL`, `DELEGATION`, `VERIFY`, `REPORT`), startup recovery's gating rules,
`WAITING`/approval-parking, and replanning. `supersedes` and `plan_id` are carried on `Task` and
written by the store so the audit chain needs no migration when P8 arrives; nothing populates them
yet. `TaskStore.unfinished()` exists and is tested — the rules built on it (never auto-restart an
interrupted agent) need a child process to have an opinion about, which is P7-T2.

## Docs

[ORCHESTRATION.md](ORCHESTRATION.md) gained an **as-built** section: what matches the design, the
six questions the design underspecified and how they were decided, what is not built yet, and the
harvest step. Two places where the code deviates from the sketch are corrected in the sketch
itself — `role`/`project` live in `TaskSpec`, and `TaskResult.error` is a `TaskError` rather than
the tool layer's `ToolError`, because the orchestration layer must not import that layer.

## Next

**P7-T2** ([current_task.md](current_task.md)): the real runners — `DelegationService` wrapped as
the DELEGATION runner (lifecycle intact), the TOOL runner through the existing executor, VERIFY
with the baseline comparison P6-T5 showed is mandatory, and startup recovery's gating rules.

## Unresolved

[OQ-18](OPEN_QUESTIONS.md#oq-18) (recall 61% vs an 80% gate — Phase 9) ·
[OQ-19](OPEN_QUESTIONS.md#oq-19) (Agent SDK, trigger-based) ·
[OQ-21](OPEN_QUESTIONS.md#oq-21) (MCP spec migration, watch) · `agy`'s unauthenticated preflight
state, still unobserved · whether `agy` works with the Antigravity IDE closed.
