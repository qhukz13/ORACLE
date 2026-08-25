# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P8-T1 — the planner tier: an objective becomes a validated graph, and a person approves
it. **Done: all seven acceptance criteria.**
**Status:** Something creates graphs now. `plan.py`, `registry.py` + `config/agents.yaml`,
`runners/planning.py`, and the `graph.plan` command. The daemon constructs the runners, closing
P7-T2's one deliberately-unmet criterion. `make check` green.
**Date:** 2026-08-25

---

## The loop closes

An objective goes out, a plan comes back, a person approves the shape, and rows execute in
dependency order — all of it through machinery that already existed. Phase 7 built the graph
without a planner on purpose; this task supplies one without changing anything underneath it.

## What a plan is allowed to say

The `ExecutionPlan` models are the P6-T5 spike's, **moved rather than rewritten**: they were
measured against a real vendor before they were trusted here. Three of PLANNER.md §2's rules are
one line of code each:

- **A plan may not name a tool** — `extra="forbid"` on both models. A plan carrying *any* unknown
  field is rejected whole, because "silently trimmed" is exactly how `tool` would have arrived.
  This is the line between a to-do list and a privilege, and it has a test in both the unit suite
  and the security suite.
- **Check 0 is non-empty** — the silently-emptied plan the spike actually received.
- **Enums, not ranges** (ADR-0017).

Two decisions the design left open and the code had to make: plan-local ids are **remapped**
(`"A"` → `{root}-a`, dependencies with them) so two plans' first task is not the same row; and
`expected_outcome` chooses the `TaskKind`, because a plan says what it wants back and the
supervisor decides how that is produced.

## The registry is where OQ-20's answer lives now

`config/agents.yaml`, loaded like policy — versioned, human-edited, never writable from a tool.
**Antigravity does not hold `planner`** there, and a test asserts it, so restoring that role means
arguing with the dev log rather than editing a line.

It **fails closed**: an unreadable registry means no agent holds any role, planning becomes
unavailable, and the ladder takes over. A registry that failed open would let a plan pick its own
executor. A **stale default loses** to the roles table, because honouring it would resurrect a
decision a measurement already overturned.

## Two questions, never one, never three

1. **The planning egress** — the preview carries the whole prompt and the bound: *"up to 2 calls
   (one repair attempt if the plan is invalid)"*. Stating the bound before the click is the
   pipeline rule; asking again for a repair the person already sanctioned is how approval fatigue
   is manufactured. It also says `sends_repo_contents: false`, which is the question a person
   actually has.
2. **The graph card** — every task with its role, its agent, and whether it will egress. Priced
   with `external` provenance, so the plan's own untrustworthiness escalates the tier before
   anybody is asked. Approving it authorises the graph to **exist and run, not to egress**: each
   delegation still asks separately, because an egress approval binds to bytes that do not exist
   yet.

A refusal at either point is a full stop, not a fallback to doing it anyway.

## Two bugs, both mine, both in tests

- **A test hung the whole suite for ten minutes.** It waited on the event stream for an approval
  that a *denied* action never requests. Every stream wait now has a deadline, so the same mistake
  fails in ten seconds with a readable message.
- **The end-to-end test answered the wrong approval.** `eventlog.stream(0)` replays the backlog, so
  the second "approve the next request" call re-found the *first*, already-answered one and left
  the graph card waiting. The helper now skips approvals that are no longer open. This is the third
  time this exact trap has been sprung in this project; the helper that avoids it should probably
  move into `helpers_delegation.py` when a fourth suite needs it.

## Tests

42 across three files (`test_plan_validation.py`, `test_plan_to_graph.py`,
`security/test_plan_injection.py`), no vendor in any of them — the fake planner is an
`ExternalAgentAdapter` returning recorded plan JSON, so the schema, the collection and the
structured field are all real. Notable:

- an injected instruction (`IGNORE PREVIOUS INSTRUCTIONS … git push --force`) compiling into a
  row whose `objective` is that sentence and whose `tool` is `None` — text stays text;
- the graph card **showing** that sentence rather than summarising it away, because approving what
  you did not read is the attack;
- a plan naming a tool rejected whole, twice, in two suites;
- exactly one repair attempt, told the specific errors, then a stop;
- prose-wrapped JSON treated as a failure rather than salvaged.

## What is deliberately still missing

**Replanning** (`supersedes` is written by the store and populated by nothing until P8-T2) ·
**context** (`context_hints` survive as text; nothing fetches them until Phase 9, so a plan cannot
cause a read) · **`REPORT` runs as a delegation**, though PLANNER.md §4 says a summarizer is never
routed to a cloud agent — that is a Phase 9 correction and the code says so where it happens.

## Next

**P8-T2** ([current_task.md](current_task.md)): replanning and multi-worker — a failed task
produces a bounded, append-only replan; two workers run concurrently on one graph; the lineage is
visible.

## Unresolved

[OQ-18](OPEN_QUESTIONS.md#oq-18) (recall 61% vs an 80% gate — Phase 9) ·
[OQ-19](OPEN_QUESTIONS.md#oq-19) (Agent SDK, trigger-based) ·
[OQ-21](OPEN_QUESTIONS.md#oq-21) (MCP spec migration, watch) · `agy`'s unauthenticated preflight
state, still unobserved · whether a task row should carry a child PID · the unexplained gate stall
from P7-T3 (did not recur).
