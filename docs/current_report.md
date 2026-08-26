# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** **P12-T3 — the briefing.** Third of three today, after T1 (the project entity) and T2
(the `continue` intent), and the vision realignment that scheduled all of them.
**Status:** Done. One acceptance item across the phase remains **knowingly outstanding** — the
intent eval ([OQ-25](OPEN_QUESTIONS.md#oq-25)).
**Date:** 2026-08-26
**Dev logs:** [T3](../logs/development/2026-08-26-p12t3-briefing.md) ·
[T2](../logs/development/2026-08-26-p12t2-continue-intent.md) ·
[T1](../logs/development/2026-08-26-p12t1-project-entity.md) ·
[vision realignment](../logs/development/2026-08-26-vision-realignment.md)

---

## What shipped

`core/briefing.py`, migration `0007`, `GET /api/v1/briefing`, `POST /api/v1/briefing/ack`, and
43 tests. The data and the deterministic text exist; **the view does not** — that is T4.

---

## The distinction the design turned on

The briefing looked like one thing ("events since seq N") and is two.

**Delta** — completed, failed, cancelled. The watermark is the whole point.

**Current state**, which appears *regardless* of the watermark:

- **`waiting`** — a task parked on an approval. Watermarking it would mean acknowledging a
  briefing could **bury the thing that most needs a person**. That is actively harmful, not
  merely unhelpful.
- **`in_flight`** — pending, ready or running. I got this wrong first and a test caught it: a
  `RUNNING` task produced an empty brief and was filtered out, so a briefing during a long run
  said *"Nothing ran"*. *"What is running now"* is one of the six things the vision gives the
  screen 3–5 seconds to answer.

The test that caught it was written to check elapsed-time handling. That is twice today a test
found a real gap while asserting something adjacent.

---

## A dead daemon can only brief itself if it left a note

UI.md already promised *"if ORACLE crashed overnight, that is the first line"* as the mitigation
for [ADR-0025](DECISIONS.md#adr-0025--oracle-is-a-resident-service-the-window-is-a-client)'s
named risk. Implementing it showed the promise was **not implementable as written**: a crash
leaves no trace, the log simply stops, and a silent gap is indistinguishable from an idle night.

So the fact is now established rather than inferred — `system.shutdown` on the way out,
`system.boot` on the way in carrying `unclean`, computed from what the last event actually was.
Both critical event types: a lost one turns a crash report into silence.

One edge stated rather than discovered later: **a first-ever boot is not unclean.** There was no
previous run for the absence of a shutdown to mean anything about.

**This mitigates P13's main risk before P13 exists**, which is why the briefing moved from P13
to P12-T3: its watermark lives on the project row and its content is per-project, so it is a
reading of *project state*, not of residency. ROADMAP and UI.md are reconciled to match.

---

## Where a watermark lives when it belongs to no project

Migration `0007` adds `meta(key, value)` to `oracle.db` — one thing to reason about instead of a
one-column table per scalar, and the shape `knowledge.db` already uses. The migration comment
says what it is **not**: configuration lives in `config/*.yaml` where a human edits it and git
records the edit.

The same migration adds `ix_events_task`. The briefing joins `events.task_id` to `tasks.id`, and
none of the log's three existing indexes helps — it would have been a full scan per render.

---

## Rendering is arithmetic, and a test says so

No model on this path: the briefing has the tightest latency budget of any surface, and it is
the one place a fabricated summary would be **a summary of the owner's own work**.
`TestNoModelIsOnThisPath` checks the module imports nothing from `oracle.llm` or `oracle.router`
and that `render` is a plain `def` with no `await`, so it cannot do I/O at all. "Have the local
model summarise this" is a genuinely attractive future edit; the template stays the permanent
fallback because it is the version that is always right.

---

## Four smaller decisions

**`through_seq` is pinned by the caller** and echoed back on acknowledgement — otherwise work
arriving mid-view is marked seen by an acknowledgement of what nobody saw. **An unknown
`project_id` on ack is a 404**, not a fallback to acknowledging everything on a typo. **A
per-project dismissal never touches the system section** — that is how a crash gets lost.
**Archived projects are not briefed**: archiving is a human saying "not now", and this is the
surface that must respect it most.

---

## Next

**[P12-T4](current_task.md) — the sidebar and the briefing, rendered.** Three endpoints now
return real project state and nobody can see any of it. First task in Phase 12 to touch
`apps/desktop/`. Note the standing trap: fixtures must be recorded from the wire, because
`TaskTree.test.tsx` is already green on a shape the running app cannot produce.

**Still a person's, and now genuinely runnable: P12-T5.** *"continue ORACLE"* will resolve this
project, read its real open tasks and this repository's own `docs/current_task.md`, and ask
before planning. Needs **Ollama running**; asks twice. It is the run that puts the first rows in
`tasks` — everything P11 renders, and everything T4 will render, is computed over fixtures until
it happens.
