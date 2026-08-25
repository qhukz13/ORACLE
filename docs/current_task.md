# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P8-T3 — The ladder, and the scenario Phase 8 was written against**

**Phase:** [8 — planner integration & multi-worker](ROADMAP.md#phase-8--planner-integration--multi-worker--supervisor-arc) · **Scope:** Supervisor arc
**Status:** `SET — not started` · **Set:** 2026-08-25
**Previous task:** P8-T2 — **done**; see [`current_report.md`](current_report.md),
[ORCHESTRATION.md §4](ORCHESTRATION.md#as-built--replanning--p8-t2-2026-08-25) and the
[dev log](../logs/development/2026-08-25-p8t2-replanning.md).

---

## Why this task exists

Three of Phase 8's six acceptance criteria are still open, and they are the ones that decide
whether the planner tier is a feature or a demo.

Today, a planner that cannot produce a valid plan **logs and stops**. `PLANNER.md §6` describes a
four-rung ladder; one rung is built (Claude is the default, because the ladder already promoted
once). Rungs 2–4 are prose. "No single vendor is load-bearing" is currently a claim about a vendor
that happens to be working.

And the scenario the whole phase was specified against — context → planning egress → validation →
graph approval → per-task gating → verification → report — exists as five separate tests that each
prove one link. Nobody has run the chain.

## What the earlier phases hand you

1. **Everything below the planner is real and tested.** Runners, scheduler, recovery, replanning,
   the two approval cards, `GraphService`, the tree projection, the UI.
2. **The ladder's shape is already designed** ([PLANNER.md §6](PLANNER.md#6-fallbacks)) and has
   already been *used* once, at the registry level, when OQ-20 came back negative.
3. **`build_runners` and `_plan_and_run` are the composition point.** A ladder belongs beside
   them, not inside `Planner`.
4. **Degrading is cheap because the degraded modes are the old modes**: ORACLE without a planner
   is ORACLE as shipped on 2026-08-24, which works.
5. **The fake-planner harness exists** (`ScriptedPlanner`, `answer_approvals`), and the reference
   scenario has a precedent in `test_reference_scenario.py` for the single-turn pipeline.

## Requirements

1. **The ladder, rungs 2–4**, walked when a plan cannot be produced — not when one is merely
   disliked:
   - **template plans**: a small set of known shapes (investigate→fix→test→review) filled from the
     intent, deterministic, no model. They are data, loaded like the registry, and validated by
     exactly the same validator a vendor's plan is.
   - **single-task plan**: one delegation or one tool — Phase 6's behaviour, reached as a
     *defined state* rather than as a crash.
   - **human-provided plan**: the person edits the task list on the graph approval card, and the
     result is validated identically. No privileged path for a plan a human typed.
2. **Which rung, and why, is visible.** Each descent is an event and appears on the graph card, so
   approving a template plan is never mistaken for approving a planner's.
3. **The reference multi-task scenario as one deterministic test**, asserting the order named in
   the ROADMAP, with FakeProvider + stub CLIs and no vendor. This is the acceptance criterion, and
   it is also the regression test for every seam Phase 8 built.
4. **A planner recommending an agent the policy forbids is overridden, and the audit shows the
   rule.** `agent_hint` is already dropped for selection; what is missing is the *audit entry* that
   makes the override reviewable.
5. **`REPORT` stops being a delegation** if the local model can hold `summarizer`
   ([PLANNER.md §4](PLANNER.md#4-roles)). If it cannot yet, say so where it happens and leave the
   admission — but decide, rather than inheriting the shrug.
6. **One supervised live run** of the full scenario on a real project, every preview
   human-approved, recorded in a dev log with what it cost.

## Constraints

- **A ladder is not a retry.** Descending happens when a rung cannot produce a *validated* plan,
  never because the plan looked unambitious. One repair per rung, then down.
- Template plans get **no privilege a vendor plan does not have**: same validator, same registry,
  same card, same per-delegation egress questions.
- The scheduler's import ban stands. So does the replan budget: a template plan that fails is a
  failure like any other.
- Do not touch the single-delegation path or the single-turn pipeline.

## Acceptance criteria

- [ ] A planner that returns nothing usable descends to a template plan, and a test asserts the
      rung, the event and the card's provenance line.
- [ ] A machine with no planner at all reaches the single-task plan and runs it; a test says so.
- [ ] A human-edited plan is validated by the same validator, and a security test shows it buys no
      privilege a vendor's plan would not have.
- [ ] The reference multi-task scenario runs as one deterministic test in the ROADMAP's asserted
      order.
- [ ] A forbidden agent recommendation is overridden **and audited**, with the rule in the entry.
- [ ] `REPORT`'s routing is decided rather than inherited.
- [ ] One supervised live run, recorded with its cost.
- [ ] `make check` green.

## Relevant files

New: `src/oracle/orchestration/templates.py` · `config/plan_templates.yaml` ·
`tests/test_plan_ladder.py` · `tests/test_reference_graph.py`.
Modify: `src/oracle/runners/planning.py` (the descent, beside the existing egress) ·
`src/oracle/api/app.py` (`_plan_and_run` walks the ladder) · `apps/desktop/.../` (the card's
provenance line) · `docs/PLANNER.md` §6 as-built.
Read first: [PLANNER.md §6](PLANNER.md#6-fallbacks) · [ROADMAP.md Phase 8 acceptance](ROADMAP.md#phase-8--planner-integration--multi-worker--supervisor-arc) ·
`tests/test_reference_scenario.py` (the single-turn precedent).

## Dependencies

P8-T1, P8-T2 (both done). Phase 9 (memory/context) is the next arc and does not block this.

## Risks

| Risk | Mitigation |
|---|---|
| Template plans become a second planner with its own quirks | They are *data*, validated by the same function; a test feeds a template through the vendor plan's validator |
| The ladder hides a broken vendor | Every descent is an event and a line on the card; a graph that ran on rung 3 must be readable as such afterwards |
| The reference test becomes a slow, flaky monolith | It uses the stub CLI and fake providers like every other Phase 7–8 test; if it needs a real vendor it is measuring the wrong thing |

## Definition of done

All acceptance criteria · `make check` green · PLANNER.md §6 corrected to as-built · a dev log for
the live run · `current_report.md` overwritten · this file set to **P9-T1**, which closes the
supervisor arc's planner tier and opens memory.
