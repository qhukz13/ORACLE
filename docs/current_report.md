# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** **P12-T5 — the first real `continue` run.** Fifth and last of the Phase 12 arc.
**Status:** Ran. Stopped at the egress approval, which is where an agent stops. **Found and fixed
a defect on the safety surface.** `tasks` is still 0 rows — one human click away.
**Date:** 2026-08-28
**Dev log:** [T5](../logs/development/2026-08-28-p12t5-first-run.md) ·
[T4](../logs/development/2026-08-26-p12t4-sidebar-and-briefing.md) ·
[T3](../logs/development/2026-08-26-p12t3-briefing.md) ·
[T2](../logs/development/2026-08-26-p12t2-continue-intent.md) ·
[T1](../logs/development/2026-08-26-p12t1-project-entity.md)

---

## The loop works

`continue ORACLE`, typed into a live daemon with a live router:

```
turn.started      "continue ORACLE"
agent.state       intent=continue  confidence=medium  route_ms=1908
continue.derived  {project: ORACLE, open_tasks: 0, notes: [docs/current_task.md,
                   docs/ROADMAP.md], tainted: true}
approval.requested  ai.delegate
```

Router classifies `continue`. Derivation reads the repository's own task documents. The objective
carries them fenced, labelled and attributed. The turn finishes without waiting for the graph.
All as designed.

---

## The defect it found, which four days of fixture tests could not

The planning approval arrived as **`tier T2`, `tainted: False`,
`sends_repo_contents: False`** — while `args.objective` was **2,820 characters** of
`docs/current_task.md` and `docs/ROADMAP.md`, verbatim.

So the card told you it was not sending repository contents while doing exactly that, and the
gate priced the call as untainted seconds after `continue.derived` — the same code path —
recorded `tainted: true`. **Recording taint on an event while pricing the call as untainted is
the worst of both: it looks audited and is not.**

**Why the tests missed it.** They tested the right things in the wrong combination: `objective_of`
for fencing, `approve_graph` for `untrusted_sources`. Nothing tested what the *planning* card says
about an objective built from files — because until `continue` existed, a planning objective was
always a sentence you had typed, and `sends_repo_contents: False` was simply true. That is the
shape of every dangerous stale assumption: **correct when written**, two days before the path that
falsified it.

**Fixed**, and verified on the live system rather than only in tests — same utterance, same
daemon:

| | before | after |
|---|---|---|
| tier | T2 `confirm` | **T3 `confirm_strong`** |
| rule | `tools.ai.delegate.tier` | `taint.escalate(tools.ai.delegate.tier)` |
| tainted / escalated | false / false | **true / true** |
| `sends_repo_contents` | false | **true** |
| `untrusted_sources` | — | `docs/current_task.md`, `docs/ROADMAP.md` |

T3 means a typed confirmation phrase — the right price for sending two of your repository's files
to a cloud API, and what you were previously not being asked. Pinned by
`TestTheEgressCardTellsTheTruth`, including the negative case so the signal keeps its information.

Written up in [SECURITY.md §6](SECURITY.md) with the generalisable rule: **a preview field that is
a literal is a claim nobody re-checks.**

---

## What OQ-25 actually found

Not what I expected. `continue` was **never** confused with `run` or `modify` — the label routed
correctly both times. But `project` came back **`null` both times**, on an input whose second word
is a registered project name. The turn worked only because `_named_project` scans the raw text
against the registry — a fallback written for `delegate`.

So the finding is that the **project slot is unreliable and a string match is carrying it**. That
fallback cannot cover a project named in a previous turn. Recorded under
[OQ-25](OPEN_QUESTIONS.md#oq-25); the eval still needs running, but now with something specific to
look for.

Also: routing took 27 s cold (GPU load while the OQ-18 run had all 24 threads) and **1.9 s warm** —
inside [OQ-15](OPEN_QUESTIONS.md#oq-15)'s target. Draw nothing from the cold number.

---

## What is still not done

**`tasks` is 0 rows.** The run stopped at the approval, so no plan was authored and no graph
compiled. The orbit's go/no-go ([OQ-14](OPEN_QUESTIONS.md#oq-14)), the execution tree's acceptance,
`TaskTree`'s fixture, the sidebar's counters and the briefing's arithmetic are all still waiting on
one human click.

That is not a failure. The run's job was to get the question asked *correctly* — and it turned out
the question had been asked incorrectly, which is worth more than a green run would have been.

**`oracle-selfcheck` is still the cheaper way to fill that table**: local, **no egress**, six steps,
one card.

---

## Next

[current_task.md](current_task.md) — approve the pending T3 card to finish T5, or run
`oracle-selfcheck` for task rows without egress. Then Phase 12 is closed and
[P13](ROADMAP.md#phase-13--residency-boot--the-briefing--residency-arc) (residency) begins.
