# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P12-T3 — the briefing: "what happened while I was away"**

**Phase:** [12 — project state & the continue loop](ROADMAP.md#phase-12--project-state--the-continue-loop--residency-arc) · **Scope:** Residency arc
**Status:** `SET` · **Set:** 2026-08-26
**Design:** [PROJECT_STATE.md §6](PROJECT_STATE.md#6-the-briefing--what-happened-while-i-was-away) · **Surface:** [UI.md §7b](UI.md#7b-the-briefing--phase-13) · **Why:** [VISION.md §2](VISION.md#2-the-day--the-acceptance-test)

**Done in Phase 12 so far:** T1 the entity · T2 the `continue` intent.
[T1 log](../logs/development/2026-08-26-p12t1-project-entity.md) ·
[T2 log](../logs/development/2026-08-26-p12t2-continue-intent.md)

---

## This task

`briefed_through_seq` already exists on the project row and already advances monotonically.
This builds the thing that reads it.

1. **The query.** Events with `seq > briefed_through_seq`, grouped by project, reduced to
   counts and outcomes — what completed, what failed, what is waiting, what it cost. Bounded:
   away for a week is not 40,000 events in a payload.
2. **`GET /api/v1/briefing`** — every non-archived project's delta, plus a system section.
3. **`POST /api/v1/briefing/ack`** — advances the pointer. **On acknowledgement only, never on
   render.** A briefing that clears itself on sight is a notification, and notifications are
   how people miss things.
4. **Deterministic prose.** Counts, outcomes, timings and cost are arithmetic over the task
   rows — no model, no latency, and no possibility of a hallucinated summary of my own work.
   Prose summarisation belongs to the local mid-tier ([P16](ROADMAP.md#phase-16--local-tier-ladder--experimental--gpu-conditional-unscheduled)),
   which does not exist; the template stays the permanent fallback because it is the version
   that is always correct.
5. **A dead daemon briefs itself.** If `oracled` crashed overnight that is the first line.
   This is the mitigation for [ADR-0025](DECISIONS.md#adr-0025--oracle-is-a-resident-service-the-window-is-a-client)'s
   main risk and it is cheap now: the gap is visible in the event log's own timestamps.

**Not in this task:** the sidebar and inspector (T4), the first real run (T5).

---

## Acceptance criteria

- [ ] An unacknowledged briefing **survives a restart**. Tested.
- [ ] Acknowledging advances the pointer; rendering does not. Tested both ways.
- [ ] Empty is a real state — "Nothing ran since 18:04", not a placeholder and not a
      fabricated summary.
- [ ] The payload is bounded regardless of how long the gap is.
- [ ] A project with a deleted root still renders its line.
- [ ] No model is called on this path, and a test asserts it.
- [ ] `make check` green.

## Watch for

- **The pointer is per-project, and system events belong to no project.** Decide where the
  system section's pointer lives before writing the query — a single global pointer would make
  acknowledging one project's work hide another's.
- **`tasks` is still 0 rows.** T3 can be built against fixtures; its *acceptance* cannot be
  judged without T5. Record fixtures from the wire when T5 runs, not by hand — `TaskTree` is
  already green on a fixture the running app cannot produce.

---

## Now unblocked for a person: P12-T5, the first real `continue`

T1 and T2 make the loop runnable end to end for the first time: **"continue ORACLE"** resolves
the project, reads real open tasks and this repository's own `docs/current_task.md`, builds an
objective, and asks for approval before planning.

Everything about it is gated and previewable. It needs **Ollama running** (the `continue`
label is classified by the router, and there is no slash-command bypass for it), and it will
ask twice — once for the graph, once for any delegation. This is the run that finally puts
rows in `tasks`, which is what P11's orbit, timeline and queue are all waiting on.

It is a person's to fire, not an agent's: approvals expire in 180 s and firing it unattended
writes a *refused* run into the very table the run exists to populate.

---

## Carried over, not forgotten

- **[OQ-25](OPEN_QUESTIONS.md#oq-25) — the intent eval was not re-run** after `continue` became
  an eleventh label. Deferred deliberately; the prompt boundary and four few-shots shipped
  instead, and a test pins them. `scripts/eval_intent.py` needs `continue` cases adding first.
- **`make eval` and `make perf` are documented in TESTING.md §8 and defined nowhere.** OQ-25's
  documented resolution path therefore does not exist. Fix the target or correct the doc.
- **P11-T5** — switchable centre stage, `Ctrl+1..4`, `TaskTree` in its own view, the inspector's
  task branch, mounting `KnowledgeHealth` (built, 11 passing tests, imported by nothing).
  Deferred behind Phase 12 deliberately; spec is in this file's history.
- **P11-T2 — OQ-14, the orbit go/no-go.** Blocked on data. `oracle-selfcheck` remains the cheap
  unblock (~5 min, local, no egress); P12-T5 produces richer data but costs tokens and egress.
- **[OQ-24](OPEN_QUESTIONS.md#oq-24) — the observation fan-out is unmeasured**, so
  `GET /api/v1/projects` runs no git and omits branch/dirty count. T4 wants those columns;
  measure before adding them, and **if it misses, observe lazily per row — never cache.**
- **P9-T3b — the scheduled OQ-18 corpus run.** Windows task `ORACLE-OQ18-eval` fires
  **2026-08-27 07:12** (~3 h) → `logs/measurements/oq18-translated.{txt,json}`. On collection:
  compose `dense_mt` against `dense_xl`, confirm or flip `Settings.translate_queries`, decide
  `en-relay-dockerfile`, resolve OQ-18, state the answer-key correction wherever pre-2026-08-26
  recall numbers are quoted, then `Unregister-ScheduledTask`.
- **The `chunker_version` guard does not fire on the indexes it was written for.** 57% of
  14,586 live rows exceed the 1200-char cap, longest 4,055. The database wants a reindex.
- **A merge-gate test that fails under CPU starvation.**
  `test_a_long_burst_arrives_complete` lost 189 lines of a ConPTY burst twice under full load.
- **`TaskTree.test.tsx` is green on a fixture the app cannot produce** — `store.ts` never
  populates `dependsOn`. T5 is the chance to record fixtures from the wire.
- **`DATABASE.md`'s `facts`/`attempts`/`devices` blocks are still the pre-build sketch.** Only
  `projects` has been reconciled against source. The shipped tables are `memory_facts` and
  `memory_attempts`; `devices` is not built.
- **Palette results are not discoverable to assistive tech** — `<li role="option">` with
  `onClick`, no `role="combobox"`, no `aria-activedescendant`.
- **A correction typed while a graph runs is refused**, because "never mid-plan" is implemented
  literally. The fix, when somebody hits it, is a queue — not an exception.
- **Scheduled pipeline runs** are post-MVP; PIPELINES.md §5's "nothing above T1 unattended" is
  unenforced because nothing schedules anything.
- **The visual references for the UI vision were never attached** and are not in the repository.
  UI.md §1/§14/§15 are marked `TO VERIFY` against them.
- **Branch.** `phase6-integration` is ahead of a stale `origin/main` at Phase 5-era work.
  Merge or rename is still a decision nobody has made.
