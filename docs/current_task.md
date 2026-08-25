# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P8-T1 — The planner tier: an objective becomes a validated graph, and a person approves it**

**Phase:** [8 — planner integration & multi-worker](ROADMAP.md#phase-8--planner-integration--multi-worker--supervisor-arc) · **Scope:** Supervisor arc
**Status:** `SET — not started` · **Set:** 2026-08-25
**Previous task:** P7-T3 — **done**; **Phase 7 is complete**. See
[`current_report.md`](current_report.md) and
[ORCHESTRATION.md](ORCHESTRATION.md#as-built--the-human-surfaces--p7-t3-2026-08-25).

---

## Why this task exists

Everything a graph needs exists and nothing creates one. Phase 7 deliberately ended there: the
supervisor's correctness was settled without a planner in the room, which is why P6-T5's "no" on
Antigravity cost a line of YAML instead of a redesign.

P8-T1 closes the loop: an objective in, a validated `ExecutionPlan` back, a graph the person
approves, and the runners finally constructed in the daemon rather than in a test.

## What the earlier phases hand you — read before designing

1. **Claude is the planner, not Antigravity** ([OQ-20](OPEN_QUESTIONS.md#oq-20)): 75% valid-on-
   first-attempt against a 90% gate. `config/agents.yaml`'s design already says so.
2. **A plan is untrusted input** (ADR-0021). It arrives `external`, taints the ingesting turn, and
   every task it spawns starts tier-escalated. `TaskSpec.tool`/`args` exist for TOOL tasks and
   **a plan must never be allowed to set them** — that is the field where "a plan is not a
   privilege" would quietly stop being true.
3. **Validation runs before anything sees the plan**, in PLANNER.md §2's order, and **check 0 is
   non-empty**: a vendor returned a schema-valid plan whose `tasks` array had been silently
   emptied by its own filter while the prose beside it held six well-formed tasks.
4. **Plans arrive with no edges** — five of twelve valid ones in the spike. A zero-edge plan is
   legal and the scheduler runs it fine, but the approval card should show it as the smell it is.
5. **Plan acceptance criteria are a hint for the worker, never the verification contract.**
   Planners write criteria naming files that do not exist. `VERIFY` uses ORACLE's baseline
   comparison and ignores them.

## Requirements

1. **The PLANNING task**, run by a planner runner (`runners/planning.py`) through the existing
   Claude adapter with `--json-schema`. It is an egress like any other: packet, preview, approval.
2. **`ExecutionPlan` for real** — the pydantic models move from `scripts/verify_agy_planning.py`
   into `src/oracle/orchestration/plan.py`, with PLANNER.md §2's validation and **one repair
   attempt** fed the specific errors, then the fallback ladder.
3. **Plan → graph**: a validated plan compiles to `Task` rows (`plan_id` set, `role` and `project`
   registry-checked, dependencies mapped from plan-local ids to task ids). A plan that fails
   validation twice never becomes a graph.
4. **The graph approval card**: one up-front approval listing every statically-priceable elevated
   task, per the pipeline rule ([SECURITY.md §10](SECURITY.md#10-the-multi-agent-surface--added-2026-08-24-phases-78)).
   Delegation egress previews still bind individually — their bytes do not exist yet.
5. **The runners, constructed in the daemon** and injected into `GraphService`, closing P7-T2's
   one deliberately-unmet criterion.
6. **Intent → graph**: a `continue_project`-class intent routes to a plan instead of a single
   delegation. The single-turn path stays the default for everything else.

## Constraints

- **The single-delegation path stays.** A graph is an addition; `delegate` must still work
  unchanged, and the fallback ladder's bottom rung is *today's behaviour*.
- No new HALT path, no second chokepoint, and the scheduler's import ban stands.
- Replanning is **not** in this task. `supersedes` stays unpopulated until P8-T2.
- Roles and agents come from `config/agents.yaml` as data. A role the registry does not know is a
  validation error, never a lookup that happens to miss.

## Acceptance criteria

- [ ] An objective produces a validated `ExecutionPlan` from Claude, with the packet previewed
      and approved like any other egress; a fixture-replayed plan makes this deterministic in CI.
- [ ] An invalid plan gets exactly **one** repair attempt with its specific errors, then falls
      down the ladder; a test covers empty `tasks`, an unknown role, a dangling dependency, and a
      cycle reported as a path.
- [ ] A plan that names a tool and arguments is **rejected**, with a security test saying why.
- [ ] The compiled graph runs end to end with the stub CLI: plan → approval → tasks → verify →
      report, asserted by order and events.
- [ ] The graph approval card lists elevated tasks up front; approving it does not pre-approve any
      delegation's egress.
- [ ] The daemon constructs and injects the runners; a graph started from a WS command runs.
- [ ] `make check` green, security suite extended: plan-injection fixtures (adversarial text in
      planning context lands as a tainted, inert plan) and graph-escalation.

## Relevant files

New: `src/oracle/orchestration/plan.py` · `src/oracle/runners/planning.py` ·
`tests/test_plan_validation.py` · `tests/test_plan_to_graph.py` ·
`tests/security/test_plan_injection.py` · `tests/fixtures/plans/*.json`.
Modify: `src/oracle/api/app.py` (construct the runners) · `src/oracle/router/pipeline.py` (the
intent path) · `config/agents.yaml` · `docs/PLANNER.md`, `docs/ORCHESTRATION.md` (as-built).
Read first: [PLANNER.md](PLANNER.md) §2–§6 · `scripts/verify_agy_planning.py` (the models and the
validation already exist there, measured against a real vendor) ·
`src/oracle/orchestration/service.py` · `logs/development/2026-08-24-p6t5-antigravity-planning.md`.

## Dependencies

Phase 7 (complete). Nothing waits on this but Phase 9+.

## Risks

| Risk | Mitigation |
|---|---|
| The planner's egress becomes a per-call approval storm | One approval for the planning call, one card for the graph; delegations keep their own previews because their bytes are new |
| Plan validation drifts from the spike's version | Move the models rather than rewriting them; the spike's file imports from the new home so both stay in step |
| "Repair" becomes an agentic loop | Exactly one attempt, fed the errors, then the ladder. If a second appears, the budget was a suggestion |

## Definition of done

All acceptance criteria · `make check` green · PLANNER.md and ORCHESTRATION.md corrected to
as-built · a dev log if plan quality against real objectives differs from the spike's numbers ·
`current_report.md` overwritten · this file set to **P8-T2** (replanning and multi-worker).
