# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P8-T2 — replanning: a failure buys one more idea, not an afternoon.
**Done: all seven acceptance criteria.**
**Status:** `supersedes` is populated now. `orchestration/replan.py` decides, the scheduler
triggers, `runners/planning.py` spends, `TaskGraph.extend` appends, and the desktop tree shows a
superseded attempt under its replacement. Two delegations from a plan-authored graph run
concurrently against real worktrees. `make check` green.
**Date:** 2026-08-25

---

## What a failure buys

One more idea, from a planner told what ORACLE measured, bounded at two per root and one per
failure — and **nothing at all** if the failure was a person saying no. That last clause is
closer to the point of this task than it looks: without it, replanning is a mechanism for asking
someone who just refused whether they would prefer a differently-worded version, twice, before
giving up.

## Three layers, and the seam is the deliverable

The risk this task named up front was "the scheduler grows a planner-shaped hole in it". It did
not:

- **`orchestration/replan.py` decides.** Pure functions, no I/O, imports nothing that acts. Is
  there a replan here, and if not, *why not* — because "the budget is spent" and "you refused
  this" are different things for a person to read.
- **The scheduler triggers.** It hands a failed `Task` to an injected hook and takes back rows to
  append. It does not know what a planner is, what a budget is, or that anybody was asked to
  approve anything. `scheduler.py` still imports neither `plan.py` nor `replan.py`, and a security
  test asserts that against the source.
- **`runners/planning.py` spends.** The composition layer that was already allowed to see both
  sides: the egress, the validation, the additions card, the compiled rows.

## The decisions the design did not make

Six, with reasons, in the [dev log](../logs/development/2026-08-25-p8t2-replanning.md) and
[ORCHESTRATION.md §4](ORCHESTRATION.md#as-built--replanning--p8-t2-2026-08-25). The two worth
knowing without reading either:

- **The budget is not a counter.** `budget_used()` is `len({t.supersedes for t in rows})`, computed
  from the table every time. A restarted daemon and a reconnecting client read the same number,
  one replan authoring three tasks still costs one, and there is nothing to forget to increment —
  which is the failure mode a budget cannot survive.
- **A replan cannot run inline.** The first sketch awaited it inside `_record()`, which stops the
  graph collecting results — including from delegations against real worktrees — for the length of
  a vendor call *and* two human decisions. It is now a tracked child held beside the parked tasks:
  no slot, but the graph is not finished while one is outstanding. A test pins it by having a
  sibling delegation complete *and be recorded* while the replan is blocked.

## The dead end worth recording

The first design resurrected the skipped branch: A failed, B was `SKIPPED`, so A′ succeeding
should make B eligible again. Intuitive, and wrong twice — it rewrites a row the event log
already stated, and it assumes A′ is a drop-in for A when a replan exists precisely because the
first approach was not. **As built:** skipped rows stay skipped and are *named to the planner*,
with an explicit instruction that the work is not resumed and must be asked for again. Silent
scope loss becomes a plan the person reads on the card.

## The claim's absence is a missing field, not a filter

`Attempt` — the shape that carries a failure to the planner — has `evidence` and no `claim`.
Feeding "I already fixed it, the tests are wrong" into the thing that authors the next task is
inter-agent instruction injection with the supervisor as the courier. Excluding it by *having
nowhere to put it* is one refactor safer than excluding it in a rendering function, and the
security test asserts on `model_fields` rather than on a string for that reason. A second test
checks the bytes the adapter actually received.

## Two questions, both existing cards

The replan egress is `ai.delegate` with the same "up to 2 calls" bound and the same
`sends_repo_contents: false`, naming which task is being replaced and which budgeted attempt this
is. The additions card is `ai.graph` — same tier, same `external` provenance, `addition: true`,
and **only the new tasks on it**. Re-showing the whole graph for two new rows is how a person is
trained to click through a card without reading it.

## Tests

45 new across `tests/test_replanning.py` (29) and `tests/security/test_replan_authority.py` (16),
plus 5 vitests. Notable:

- a graph that always fails, driven with a planner that always answers: it stops at two, reports
  every attempt with the **branches the partial work was harvested onto**, and leaves all three
  rows readable;
- a replan batch that would collide with an existing id, refused *whole* — the good row does not
  slip in beside the bad one;
- three independent `coder` tasks compiled from plan JSON: two delegations at once in separate
  worktrees, three distinct branches and three distinct harvest commits, the third queued;
- a replan trying to reach a project the original graph could not, twice, including on the repair
  attempt;
- the graph card, already approved, buying nothing for rows that did not exist when it was shown.

## What is deliberately not answered

**Whether a failure-carrying prompt produces a *different* plan or the same plan reworded**
([OQ-23](OPEN_QUESTIONS.md#oq-23)). P6-T5 measured plan *validity*; validity and difference are
not the same property, and a valid restatement of a failed plan spends an approval and a
delegation to arrive back where it started. It was not answered with a synthetic run because a
synthetic failure answers a synthetic question. What makes shipping it anyway defensible: the
budget does not make the prompt correct, it makes a wrong prompt cheap.

Also still open from P8-T1: **the fallback ladder** (a plan that cannot be produced logs and
stops — there are no template plans and no second planner), **the reference multi-task scenario as
one deterministic test**, and **`REPORT` still runs as a delegation**.

## Next

**P8-T3** ([current_task.md](current_task.md)): the ladder and the reference scenario — what
happens when the planner cannot produce a plan at all, and the one end-to-end test Phase 8's
acceptance criteria are written against.
