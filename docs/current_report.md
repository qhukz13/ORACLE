# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P8-T3 — the ladder, and the scenario Phase 8 was written against.
**Done: seven of eight acceptance criteria.** The eighth — one supervised live run — is a person's
to spend, not an agent's, and is stated below rather than ticked.
**Status:** ORACLE now produces a validated graph with the planner returning rubbish, with no
planner at all, and with the template file unreadable. The reference multi-task chain runs as one
test. `REPORT` runs on the local model. `make check` green.
**Date:** 2026-08-25

---

## The ladder, and the rule that keeps it from being a hole

Rungs 2–4 of [PLANNER.md §6](PLANNER.md#6-fallbacks) exist: `config/plan_templates.yaml` (data,
loaded like the registry), `single_task_plan()` (code, because the rung below "the template file
is unreadable" cannot be in the template file), and `graph.submit_plan` for a plan a person wrote.

**Every rung produces the same `ExecutionPlan`, checked by the same validator, compiled by the
same compiler, shown on the same card at the same tier with the same `external` provenance.** A
template naming a role nobody holds is rejected exactly as a vendor's plan is, and costs a rung. A
plan a person typed meets the same parser — "the author is trusted" is precisely the control
ADR-0021 says never to build.

**A refusal is not a rung.** Decline the planning egress and the ladder stops; descending would be
answering "no" with "how about this". P8-T2's rule, unweakened by the alternative being cheaper.

Every descent is a `plan.descended` event and a line on the card, so a graph that ran on rung 3 is
readable as one months later rather than inferred from how thin it looks.

## Two things that were true on paper only

Both were invisible to every test that existed, and both are in the
[dev log](../logs/development/2026-08-25-p8t3-ladder.md).

- **The graph card was never rendered.** P8-T1 put every task, role, agent and egress marker into
  the approval payload, and the desktop UI matched `ai.delegate` for the egress box and fell
  through to the generic EFFECT block for everything else — which shows one line of prose. A
  person approving twelve tasks saw the tool name and the tier. P8-T1's test asserting "the card
  shows the injected sentence" was asserting it about the payload; the card did not exist.
  `GraphCard.tsx` renders it now, objectives **verbatim**, with a vitest that plants the injection
  string and checks it appears character for character.
- **A delegation was being handed to the local model.** `holders_of` sorts free before
  subscription, so `researcher` — held by `claude` and `local` — resolved to `local` and then ran
  through the Claude adapter. Nothing failed; the row was simply a lie in the one column that
  answers "which agent did this". `resolve_agent()` now narrows by task kind before cost order.

## `REPORT` stops being an admission

P8-T1 mapped `REPORT` to the delegation runner and said so out loud. The fix reads the rule off
the registry it was already written in: **a role whose holders are all `locality: local` compiles
to `REPORT`**, which `runners/report.py` runs against the local provider. Adding another local-only
role is a YAML edit.

It summarises ORACLE's evidence and **is not shown the workers' claims** — the same rule as the
replan prompt, for a sharper reason: a local model writing ORACLE's report from a worker's prose is
inter-agent injection one step further from anybody checking. It degrades to a deterministic
listing rather than failing, because a report task that failed the graph when Ollama is down would
report a summary outage as a work outage.

## The override is auditable now

§5 always said `agent_hint` breaks ties and nothing more. Selection dropped a forbidden hint
*silently*, which made "the planner was overridden" a true statement nobody could check.
`overridden_hints()` reports each one with its reason; `audit_overrides()` writes a hash-chained
entry per override before anybody is asked to approve the graph those hints were steering.

## The reference chain

`tests/test_reference_graph.py` runs objective → planning egress → validation → graph approval →
per-task gating → verification → report with everything below the vendor real, and asserts the
**order** — appended to by the things that do the work, not by the test narrating itself. It found
nothing, which is the correct outcome: its value is that every future change to any Phase 8 seam
has to keep the order intact. Two variants run beside it, on the template rung and the single-task
rung, because the ladder's whole claim is about paths nobody usually walks.

## Tests

50 new: 28 in `tests/test_plan_ladder.py`, 3 in `tests/test_reference_graph.py`, 4 added to
`tests/security/test_plan_injection.py`, 2 to `tests/test_api.py`, 7 vitests, plus the plan-suite
updates. Notable:

- the shipped templates validated against the shipped registry, so the two files cannot drift;
- an objective containing `{0.__class__}` surviving substitution as text;
- a refused planning egress producing zero descents and zero packets;
- an unusable registry reaching nothing **and egressing nothing** on the way to finding out;
- a template plan priced at the same escalated tier as a planner's.

## The one criterion not met

**A supervised live run** of the full scenario on a real project, every preview human-approved. Not
attempted: it spends real quota and its entire point is that a person reads each preview and
decides. Answering the approvals programmatically would produce a green tick for the one criterion
whose subject is the human in the loop. What it should measure is in the dev log.

Also still open: **editing a plan inside the card** (rung 4 is the writing half; amending in place
is a UI surface, not a validation one), and a validator inconsistency found and deliberately left —
`verifier` + `verdict` is rejected while `reviewer` + `verdict` produces the identical
deterministic task. The reasoning for not fixing it inside this task is in the dev log.

## Next

**P9-T1** ([current_task.md](current_task.md)): memory and the context engine — the bands are still
empty, and both the planner and every delegation are now bounded by context quality rather than by
mechanism.
