# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P12-T4 — the sidebar and the briefing, rendered**

**Phase:** [12 — project state & the continue loop](ROADMAP.md#phase-12--project-state--the-continue-loop--residency-arc) · **Scope:** Residency arc
**Status:** `SET` · **Set:** 2026-08-26
**Surfaces:** [UI.md §4](UI.md#4-sidebar) (sidebar) · [UI.md §7b](UI.md#7b-the-briefing--backend-built--p12-t3-2026-08-26) (briefing)

**Done in Phase 12 so far:** T1 the entity · T2 the `continue` intent · T3 the briefing backend.
[T1](../logs/development/2026-08-26-p12t1-project-entity.md) ·
[T2](../logs/development/2026-08-26-p12t2-continue-intent.md) ·
[T3](../logs/development/2026-08-26-p12t3-briefing.md)

---

## This task

Three endpoints now return real project state and nobody can see any of it. This is the first
task in Phase 12 that touches `apps/desktop/`.

1. **The sidebar reads `GET /api/v1/projects`** instead of the bare name list from
   `/api/v1/status`. Registered projects with their counters; candidates behind a "show all".
2. **The briefing is the centre stage on first paint** after a gap, and demotes to a
   command-bar badge once acknowledged. `GET /api/v1/briefing` → render `projects[]` and
   `system`; `[dismiss]` → `POST /api/v1/briefing/ack` with the `through_seq` **from the
   payload that was displayed**, never a freshly-read one.
3. **Every line is actionable or it is not rendered.** `[inspect]`, `[review]`, `[logs]` open
   something real. A line with no affordance is a log entry in a costume.
4. **`waiting` is the only loud element**, matching the sidebar and the core. One attention
   channel, one meaning.

**Not in this task:** branch and dirty count in the sidebar — that needs
[OQ-24](OPEN_QUESTIONS.md#oq-24) measured first, and the answer if it misses is lazy per-row
observation, never a cache.

---

## Acceptance criteria

- [ ] The sidebar renders registered projects with live counters; candidates are separate and
      collapsed.
- [ ] A project whose root vanished renders as `missing` and degrades nothing else.
- [ ] The briefing renders, and **glancing at it does not clear it** — asserted in the UI
      tests, not only in the API ones.
- [ ] Acknowledging sends the displayed `through_seq`, so work that arrived mid-view is not
      marked seen.
- [ ] Empty renders as "Nothing ran since …", not a skeleton.
- [ ] `axe` passes on both surfaces; every status carries icon + label + colour, never colour
      alone ([UI.md §14](UI.md#14-colour-and-status-semantics)).
- [ ] `make check` green.

## Watch for

- **Fixtures must come from the wire, not from imagination.** `TaskTree.test.tsx` is green on a
  shape `store.ts` cannot produce, and that is exactly the bug to avoid repeating. The API
  responses are stable and testable — record from them.
- **`through_seq` is a value, not a timestamp.** Re-reading the head at dismissal time silently
  acknowledges work the person never saw. The payload carries the number for this reason.
- **The briefing is a glance, not a report.** If it stops being readable in 3–5 seconds it has
  failed, however much information it gained.

---

## Still a person's, and now genuinely runnable: P12-T5

**"continue ORACLE"** resolves this project, reads its real open tasks and this repository's own
`docs/current_task.md`, and asks before planning. It needs **Ollama running** — the `continue`
label is classified by the router and there is no slash-command bypass — and it asks twice, once
for the graph and once for any delegation.

This is the run that puts the first rows in `tasks`. Everything in P11 (orbit, timeline, queue)
and everything T4 renders is currently computed over fixtures only.

A person's to fire, not an agent's: approvals expire in 180 s, and firing it unattended writes a
*refused* run into the very table the run exists to populate.

---

## Carried over, not forgotten

- **[OQ-25](OPEN_QUESTIONS.md#oq-25) — the intent eval was not re-run** after `continue` became
  an eleventh label. Deferred deliberately; the prompt boundary and four few-shots shipped
  instead, and a test pins them. `scripts/eval_intent.py` needs `continue` cases adding first.
- **`make eval` and `make perf` are documented in TESTING.md §8 and defined nowhere.** OQ-25's
  documented resolution path therefore does not exist.
- **[OQ-24](OPEN_QUESTIONS.md#oq-24) — the observation fan-out is unmeasured**, so
  `GET /api/v1/projects` runs no git and omits branch/dirty count.
- **P11-T5** — switchable centre stage, `Ctrl+1..4`, `TaskTree` in its own view, the inspector's
  task branch, mounting `KnowledgeHealth` (built, 11 passing tests, imported by nothing). T4
  touches the same shell and may absorb part of it; decide deliberately rather than by drift.
- **P11-T2 — OQ-14, the orbit go/no-go.** Blocked on data. `oracle-selfcheck` remains the cheap
  unblock (~5 min, local, no egress); P12-T5 produces richer data but costs tokens and egress.
- **P9-T3b — the scheduled OQ-18 corpus run.** Windows task `ORACLE-OQ18-eval` fires
  **2026-08-27 07:12** (~3 h) → `logs/measurements/oq18-translated.{txt,json}`. On collection:
  compose `dense_mt` against `dense_xl`, confirm or flip `Settings.translate_queries`, decide
  `en-relay-dockerfile`, resolve OQ-18, state the answer-key correction wherever pre-2026-08-26
  recall numbers are quoted, then `Unregister-ScheduledTask`.
- **The `chunker_version` guard does not fire on the indexes it was written for.** 57% of
  14,586 live rows exceed the 1200-char cap, longest 4,055. The database wants a reindex.
- **A merge-gate test that fails under CPU starvation.**
  `test_a_long_burst_arrives_complete` lost 189 lines of a ConPTY burst twice under full load.
  Related: a gate run hung in the pytest step under concurrent load on 2026-08-26 and passed
  quietly on retry. **There is no `pytest-timeout` installed**, which makes either recurrence
  expensive to bisect.
- **`TaskTree.test.tsx` is green on a fixture the app cannot produce** — `store.ts` never
  populates `dependsOn`.
- **`DATABASE.md`'s `facts`/`attempts`/`devices` blocks are still the pre-build sketch.** Only
  `projects`, `meta` and the task/event indexes have been reconciled against source.
- **Palette results are not discoverable to assistive tech** — `<li role="option">` with
  `onClick`, no `role="combobox"`, no `aria-activedescendant`.
- **A correction typed while a graph runs is refused**, because "never mid-plan" is implemented
  literally. The fix, when somebody hits it, is a queue — not an exception.
- **Scheduled pipeline runs** are post-MVP; PIPELINES.md §5's "nothing above T1 unattended" is
  unenforced because nothing schedules anything.
- **The visual references for the UI vision were never attached** and are not in the repository.
  UI.md §1/§14/§15 are marked `TO VERIFY` against them — and T4 is the first task that would
  actually benefit from having them.
- **Branch.** `phase6-integration` is ahead of a stale `origin/main` at Phase 5-era work.
  Merge or rename is still a decision nobody has made.
