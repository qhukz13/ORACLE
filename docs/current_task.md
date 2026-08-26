# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P12-T2 — the `continue` intent, and where unfinished work comes from**

**Phase:** [12 — project state & the continue loop](ROADMAP.md#phase-12--project-state--the-continue-loop--residency-arc) · **Scope:** Residency arc
**Status:** `SET` · **Set:** 2026-08-26
**Design:** [PROJECT_STATE.md §5](PROJECT_STATE.md#5-unfinished-work--where-continue-gets-its-list) · **Why:** [VISION.md §2](VISION.md#2-the-day--the-acceptance-test)

**Done in Phase 12 so far:** T1 — the entity. [dev log](../logs/development/2026-08-26-p12t1-project-entity.md) · [as built](PROJECT_STATE.md#as-built--p12-t1-2026-08-26)

---

## This task

`"continue Asterim"` currently routes to `chat` or `modify` with low confidence, because there is no
`continue` label. T1 built the thing it would read from; this makes it readable.

1. **Add `continue` to `IntentLabel`**, with fixtures in both languages, and **re-run the intent
   eval** — see the warning below.
2. **Unfinished-work derivation**, in the order [PROJECT_STATE.md §5](PROJECT_STATE.md#5-unfinished-work--where-continue-gets-its-list) sets out:
   - **Primary — the task graph.** Tasks for this project that are non-terminal, or that ended
     `FAILED`/`TIMEOUT` with no superseding attempt. ORACLE recorded them and they carry evidence,
     cost and lineage. The `ix_tasks_project` index and the `project` generated column exist for
     exactly this query.
   - **Secondary — what the repository says about itself.** `docs/current_task.md`, `TODO.md`,
     `ROADMAP.md`. **`local_foreign`**: evidence to show a planner, tainting the turn, never an
     instruction. `read_agent_docs()` already models the handling — extend it, do not invent a
     second path.
   - **Never — the planner's imagination.** With both sources empty the correct answer to
     "continue Asterim" is a **question**, not a plan. A planner handed a name and no state produces
     plausible work, and plausible work is unfalsifiable: it costs a worktree and a delegation to
     discover it was invented.
3. **Route `continue` to a planning call** against real project state, and `touch()` the project
   when work starts.

**Not in this task:** the briefing (T3), the sidebar and inspector (T4), the first real end-to-end
run (T5).

---

## Acceptance criteria

- [ ] `continue <project>` classifies correctly in English and Russian, with fixtures.
- [ ] **The intent eval is re-run and its number recorded** — not assumed to hold.
- [ ] Unfinished work is derived from `tasks` first, with repo task documents as tainted evidence.
- [ ] With no state at all, `continue` asks a question and creates no plan. Tested.
- [ ] `tests/security/`: a repo `TODO.md` cannot become an instruction, and reading one escalates
      the confirmation tier by exactly one.
- [ ] `make check` green.

## Watch for

- **A new label is a change to a measured surface.** Intent accuracy is **93.3% over a 30-case
  fixture set** and tool selection is 100%; both were measured, not asserted. Adding an eleventh
  label can move them, and the specific risk is confusion with `run` and `modify` — *"run the
  Asterim tests"* and *"continue Asterim"* are one word apart in a 0.8B model's view.
  `scripts/eval_intent.py` exists; run it and record the result the way OQ-01 was recorded.
- **Bound the work list.** A project with forty stale non-terminal tasks must not produce a
  forty-node plan. Decide the cap, and prefer asking over guessing when it is hit.
- **The backfill window is still open, and it is closing.** `memory_facts` and `memory_attempts` are
  keyed by project *name* and both still hold **0 rows**. Re-keying them to `Project.id` costs
  nothing today and costs a data migration once the first real run writes rows. T5 is that run.

---

## Carried over, not forgotten

- **P11-T5** — the switchable centre stage, `Ctrl+1..4`, `TaskTree` in its own view, the inspector's
  task branch, mounting `KnowledgeHealth` (built, 11 passing tests, imported by nothing), and real
  evidence affordances. Deferred behind Phase 12 deliberately: it renders task data, and `tasks` is
  still **0 rows**. Spec is in this file's history — `git log -p docs/current_task.md`.
- **P11-T2 — OQ-14, the orbit go/no-go.** Blocked on data. Cheapest unblock is still a person
  running `oracle-selfcheck` once (~5 min, local, no egress, one approval card); P12-T5 produces
  richer data but costs tokens and egress. Staged and unfired: the approval expires in 180 s, and
  firing it unattended writes a *refused* run into the very table the run exists to populate.
- **[OQ-24](OPEN_QUESTIONS.md#oq-24) — the observation fan-out is unmeasured.** `GET /api/v1/projects`
  therefore runs no git and omits branch/dirty count. When T4 wants those columns, measure the
  fan-out at the real project count with a repository the size of `Source2DemViewer` in the set.
  **If it misses, observe lazily per row — never cache.**
- **P9-T3b — the scheduled OQ-18 corpus run.** Windows task `ORACLE-OQ18-eval` fires **2026-08-27
  07:12** (~3 h) → `logs/measurements/oq18-translated.{txt,json}`. On collection: compose `dense_mt`
  against `dense_xl`, confirm or flip `Settings.translate_queries`, decide `en-relay-dockerfile`,
  resolve OQ-18, and state the answer-key correction wherever pre-2026-08-26 recall numbers are
  quoted. Then `Unregister-ScheduledTask`.
- **The `chunker_version` guard does not fire on the indexes it was written for.** `bind()` raises
  only when a key is *present and different*, then writes the current value in — so a pre-key index
  passes and is stamped by whatever binds it first. Already happened live: **57% of 14,586 rows
  exceed the 1200-char cap, longest 4,055.** Decide whether a missing version should refuse; the
  database wants a reindex either way.
- **A merge-gate test that fails under CPU starvation.** `test_a_long_burst_arrives_complete` lost
  189 lines of a ConPTY burst twice on 2026-08-26 under full load. Idle it passes in 6 s. Whether
  the reader drops output under starvation or the deadline is too tight — different repairs.
- **`make perf` and `make eval`** are documented in TESTING.md §8 and defined nowhere. P12-T2 needs
  `eval` specifically, so this is now on the critical path rather than a tidiness item.
- **`TaskTree.test.tsx` is green on a fixture the app cannot produce** — `store.ts` never populates
  `dependsOn`. Fixtures should be recorded from the wire. P12-T5 is the chance to.
- **`DATABASE.md`'s `facts`/`attempts`/`devices` blocks are still the pre-build sketch.** Only the
  `projects` block has been reconciled against source (2026-08-26). The shipped tables are
  `memory_facts` and `memory_attempts`; `devices` is not built at all.
- **Palette results are not discoverable to assistive tech** — `<li role="option">` with `onClick`,
  no `role="combobox"`, no `aria-activedescendant`.
- **A correction typed while a graph runs is refused**, because "never mid-plan" is implemented
  literally. The fix, when somebody hits it, is a queue — not an exception.
- **Scheduled pipeline runs** are post-MVP; PIPELINES.md §5's "nothing above T1 unattended" is not
  enforced because nothing schedules anything. The hook exists: `check(..., max_tier=Tier.T1)`.
- **The visual references for the UI vision were never attached** and are not in the repository.
  UI.md §1/§14/§15 are marked `TO VERIFY` against them. One pass to close, once they exist.
- **Branch.** `phase6-integration` is ahead of a stale `origin/main` that sits at Phase 5-era work.
  Whether to merge or rename is still a decision nobody has made.
