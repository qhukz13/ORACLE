# P8-T2 — replanning: what the design underspecified, and what is still unmeasured

**Date:** 2026-08-25 · **Task:** P8-T2 (replanning & multi-worker) ·
**Docs corrected:** [ORCHESTRATION.md §4](../../docs/ORCHESTRATION.md#4-failure-and-replanning),
[PLANNER.md](../../docs/PLANNER.md), [OPEN_QUESTIONS.md OQ-23](../../docs/OPEN_QUESTIONS.md#oq-23)

ORCHESTRATION.md §4 described replanning in four numbered sentences. Four sentences turned out to
hide six decisions and one thing nobody has measured. This records the decisions with their
reasons, and the measurement with the reason it was *not* faked.

---

## 1. Where the budget lives, and why it is not a counter

"≤ 2 replans per root, counted on the root task" implies a number stored somewhere. There is no
root task *row* — `root_id` is a synthetic id and the graph's tasks are `{root}-a`, `{root}-b` —
so there was nowhere obvious to put it.

**As built:** `budget_used(tasks) = len({t.supersedes for t in tasks if t.supersedes})`, computed
from the rows every time it is asked.

Three properties fall out for free, and each is one a counter would have had to be given
deliberately:

* a restarted daemon reads the same number, with no migration and nothing to reconstruct;
* a replan that authored three tasks costs one, because they share one `supersedes` target;
* there is nothing to forget to increment, which is the failure mode a budget cannot survive —
  a budget that is occasionally not spent is not a budget.

The cost is one `SELECT` per failure. A replan is a vendor call and a human decision; the query is
free by comparison.

## 2. The refusal branch is the whole feature

The requirement — "a task that failed because a human refused something is not replanned" — reads
like an edge case. It is closer to the point of the task. Without it, replanning is a mechanism
for asking a person who just said no whether they would prefer a differently-worded version, and
it does that twice before giving up.

The list is error *kinds*, not a heuristic: `denied · refused · expired · halted · cancelled ·
approval_required · approval_invalid · interrupted`. Two are worth stating out loud.

* `expired` is a refusal. An approval that timed out is a decision nobody made, and re-asking for
  it automatically is how a person learns that ignoring a card costs them nothing.
* `interrupted` is on the list for a different reason. Recovery marks every `RUNNING` task
  interrupted after a crash and gates rather than restarting, because a supervisor that cannot
  prove what a child did while it was dead does not pretend to. A supervisor that then *authored
  the replacement* would be doing exactly that, one level up.

## 3. `supersedes` when a replan returns several tasks

The design says the replan "produces new tasks that supersede the failed ones", which is a plural
answering a singular and does not say which new task supersedes which old one.

**As built:** every task in the batch carries `supersedes` and `parent_id` pointing at the failed
task. A replan may reasonably answer one bad coding task with a research step and a narrower
coding step; nominating one of them as "the" replacement would produce a lineage that reads
cleanly and is false. The UI folds them the same way — first replacement wins the attempt, so the
failed row is shown once, not once per replacement.

## 4. A replan cannot be inline (found by asking what P7-T2 proved)

The first sketch awaited the replan inside `_record()`. That is a graph that stops collecting
results — including from delegations still running against real worktrees — for the length of a
vendor call *and* two human decisions. The concurrency limit of 2 would have been a claim about
the first minute of a graph and a lie thereafter.

**As built:** a replan is a tracked child task of the scheduler, held in `_replanning` beside
`_parked` for the same reason parking exists: it holds no slot, but the graph is emphatically not
finished while one is outstanding. `_loop` therefore ends on *no active tasks **and** no replan
outstanding*, and `_abandon`/`cancel` reach replans so HALT and "stop this task" still mean what
they say. `test_a_replan_does_not_stall_the_other_delegations` pins it: a sibling delegation
completes and is *recorded* while the replan is still blocked.

## 5. Ids: same root, new namespace

A replan compiled with `root_id=root` produces `{root}-a`, which is already taken. Compiling with
a different root would have hidden the replacement from `GET /api/v1/tasks?root_id=` — the one
place a person looks.

**As built:** `compile_plan(..., id_prefix=f"{root}-r{n}")`. The root is what makes a replacement
visible in the same tree; only the name has to be new. `{root}-r1-a`, `{root}-r2-a`.

## 6. The ceiling, stated rather than implied

`MAX_GRAPH_SIZE = 12` is a rule about a *plan*. A replanned graph is several plans. Applying 12 to
the combined graph would have made replanning impossible for exactly the large graphs most likely
to need it; applying nothing would have made "append-only" mean "unbounded".

**As built:** `MAX_GRAPH_TOTAL = 3 × MAX_GRAPH_SIZE`, which is what the per-plan cap and the ≤2
budget already imply, written down as a constant so nothing has to multiply it in its head. Every
individual plan is still checked at 12.

---

## The dead end: a "replan" that resumed the skipped branch

The first design had the replacement inherit the failed task's dependents — task B was `SKIPPED`
because A failed, so A′ succeeding should make B eligible again. It is intuitive and it is wrong,
in two ways that only became visible once written down:

1. **It rewrites history.** B's row says `SKIPPED` with a reason naming A. Flipping it back to
   `PENDING` makes the table disagree with the event log, which is the one thing ADR-0010 says
   never happens. The status is a fact about what did not run, not a cache of eligibility.
2. **It assumes A′ is a drop-in for A.** A replan exists precisely because the first approach was
   wrong. A verification task written against A's intended output may be checking for something A′
   deliberately no longer produces.

**As built:** skipped rows stay skipped and are *named to the planner*, with an explicit sentence
that the work is not resumed and must be asked for again if it is still wanted. That converts a
silent scope loss into a plan the person can read on the additions card. It also means a replan
that forgets to re-author needed work produces a visibly incomplete card rather than a quietly
incomplete graph — which is the failure mode worth having.

Whether a real planner actually *takes* that instruction is item (3) of
[OQ-23](../../docs/OPEN_QUESTIONS.md#oq-23).

---

## What was not measured, and why it was not faked

**Does a failure-carrying prompt produce a different plan, or the same plan reworded?**
Nobody knows. The P6-T5 spike measured plan *validity* against a real vendor; validity and
difference are not the same property, and a valid restatement of a failed plan is worse than
useless — it spends an approval and a delegation to arrive back where it started.

This was left as [OQ-23](../../docs/OPEN_QUESTIONS.md#oq-23) rather than answered with a synthetic
run, because a synthetic failure answers a synthetic question. The useful measurement needs real
objectives that really failed, scored on whether the replan is a rewording, a re-targeting, or a
genuinely different approach — and that corpus does not exist until replanning has been running in
normal use.

What makes shipping it anyway defensible is that a bad answer is bounded on every axis that costs
something: two attempts per root, one per failure, each behind its own egress preview and its own
additions card, appended rather than substituted, with every attempt still readable afterwards.
The budget does not make the prompt correct. It makes a wrong prompt cheap.

---

## Two smaller things worth keeping

**The claim's absence is a missing field, not a filter.** `Attempt` — the shape carrying an
attempt to the planner — has `evidence` and no `claim`. Excluding the worker's prose by *not
having anywhere to put it* is stronger than excluding it in a rendering function, because the
second kind of exclusion is one refactor away from being forgotten. `test_the_workers_claim_cannot
_reach_the_planner` asserts on `model_fields`, not on the string, for exactly that reason.

**The approval-stream trap, sprung a fourth time.** `eventlog.stream(0)` replays the backlog, so a
helper that answers "the next approval" restarts from seq 0 and re-reads the first request forever
while the others expire. This is now the fourth suite to hit it (P6-T2, P7-T2, P8-T1, P8-T2).
`answer_approvals(approvals, eventlog, [decisions])` in `tests/test_replanning.py` takes a *list*
of answers over one subscription. If a fifth suite needs it, it should move to
`helpers_delegation.py` — the recommendation P8-T1's report already made, now with one more data
point behind it.
