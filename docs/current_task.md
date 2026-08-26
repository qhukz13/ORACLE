# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P12-T1 — the `projects` table and the registry**

**Phase:** [12 — project state & the continue loop](ROADMAP.md#phase-12--project-state--the-continue-loop--residency-arc) · **Scope:** Residency arc
**Status:** `SET` · **Set:** 2026-08-26
**Design:** [PROJECT_STATE.md](PROJECT_STATE.md) · **Decision:** [ADR-0024](DECISIONS.md#adr-0024--a-project-is-a-first-class-persistent-entity) · **Why:** [VISION.md §5](VISION.md#5-what-is-persistent)

---

## Why this and not P11-T5

P11-T5 (switchable centre stage + task inspector) was set this morning and is **not cancelled** —
it is unblocked, small and specified, and it is the next UI task. It yielded because of what the
vision audit found ([dev log](../logs/development/2026-08-26-vision-realignment.md)):

- `tasks` is **0 rows**. T5 moves `TaskTree` into its own view and opens tasks in the inspector.
  Both render supervisor activity that has never happened, and the phase has already shipped one
  component — `TaskTree` — that is green on a fixture the running app cannot produce.
- P12 is the brief's own "smallest milestone that proves the architecture", **and** the run that
  fills `tasks` with real rows. Doing it first means T5 is judged against real data rather than
  against fixtures we wrote.

Do T5 immediately after this phase's first real run, not before.

---

## This task

Build the durable half of a project. Nothing about `continue` yet, nothing about the UI yet — the
entity first, because everything else in the phase reads from it.

1. **Migration `0005`** — the `projects` table per [PROJECT_STATE.md §3](PROJECT_STATE.md#3-the-model),
   plus an index on `tasks(project, status)` that does not exist today and is what makes the
   counters cheap to rebuild.
2. **Registry operations** in `core/projects.py` — register, rename, archive. `discover_projects()`
   keeps its job and becomes a **candidate** source; a candidate becomes a row only when a human
   registers it or ORACLE first works in it. *(The projects root holds `New folder`, `docs.zip` and
   `Kaggle`; auto-registering everything would fill the briefing with things that are not projects,
   and the briefing's whole value is that it is short.)*
3. **Identity is the row id, not the directory name.** Renaming `Asterim/` on disk must not orphan
   its facts and attempts.
4. **`ProjectObservation`** — the read-fresh reader for branch / ahead / behind / dirty count / last
   commit, going **through the tool layer** (`git.status`, `git.log`, `fs.stat` — all T0). `error`
   is a field, not an exception: a deleted root renders `MISSING` and nothing else degrades.
5. **Counters** — denormalised on the row, rebuildable from `tasks`, and **never authoritative**. A
   counter that disagrees with the task table is a projection bug; the repair is recompute.
6. **API** — `GET /api/v1/projects`, `GET /api/v1/projects/{id}`. The sidebar stops rendering a bare
   name list from `/api/v1/status`.

**Not in this task:** the `continue` intent, unfinished-work derivation, the briefing, the sidebar
rewrite. They are T2–T5 and each depends on this one.

---

## Acceptance criteria

- [ ] Migration `0005` applies cleanly and is reversible in the sense the other migrations are.
- [ ] Register / rename / archive are tested, and **`id` survives a rename**.
- [ ] A project whose root has been deleted renders `MISSING`; the API, sidebar and briefing all
      still work.
- [ ] `ProjectObservation` reads through the tool layer, and **`tests/security/` asserts there is no
      direct subprocess path** from it.
- [ ] **`tests/security/` asserts that registering a project widens no policy scope.** Scopes live
      in `config/policy.yaml` where a human edits them and git records it. If registration could
      widen a scope, "discover projects" would be privilege escalation with a friendly name.
- [ ] Observed state is never persisted — asserted by a test, not by convention.
- [ ] Counters recomputed from `tasks` equal the stored values.
- [ ] `make check` green.

## Watch for

- **The backfill is empty and that is a one-time gift.** `memory_facts` and `memory_attempts` are
  keyed by project *name* and both hold **0 rows**, so re-keying to the row id costs nothing today
  and costs a data migration later. Take it now.
- **Measure the observation fan-out.** `EXPERIMENT NEEDED` — 13 candidate directories × one git call
  each, against the 3–5 second glance budget of [VISION.md §2](VISION.md#2-the-day--the-acceptance-test).
  If it misses, the answer is **lazy per-row reads, never a cache** (PROJECT_STATE.md §2).
- **Repo task documents are `local_foreign`.** `TODO.md` and `current_task.md` in someone else's
  repository are evidence to show a planner, never instructions. `read_agent_docs()` already models
  the handling — extend it, do not invent a second path.

---

## Carried over, not forgotten

- **P11-T5** — the switchable centre stage, `Ctrl+1..4`, `TaskTree` in its own view, the inspector's
  task branch, mounting `KnowledgeHealth` (built, 11 passing tests, imported by nothing), and real
  evidence affordances. Full spec is in this file's previous revision — `git log -p docs/current_task.md`.
- **P11-T2 — OQ-14, the orbit go/no-go.** Still blocked on data, still unblocked most cheaply by a
  person running `oracle-selfcheck` once (~5 min, local, no egress, one approval card). Staged and
  unfired: the approval expires in 180 s, and firing it unattended writes a *refused* run into the
  very table the run exists to populate.
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
- **`make perf` and `make eval`** are documented in TESTING.md §8 and defined nowhere.
- **`TaskTree.test.tsx` is green on a fixture the app cannot produce** — `store.ts` never populates
  `dependsOn`. Fixtures should be recorded from the wire. P12's first real run is the chance to.
- **Palette results are not discoverable to assistive tech** — `<li role="option">` with `onClick`,
  no `role="combobox"`, no `aria-activedescendant`. Relevant to P11's "the list view offers every
  graph action" criterion.
- **A correction typed while a graph runs is refused**, because "never mid-plan" is implemented
  literally. The fix, when somebody hits it, is a queue — not an exception.
- **Scheduled pipeline runs** are post-MVP; PIPELINES.md §5's "nothing above T1 unattended" is not
  enforced because nothing schedules anything. The hook exists: `check(..., max_tier=Tier.T1)`.
- **The visual references for the UI vision were never attached** and are not in the repository.
  UI.md §1/§14/§15 are marked `TO VERIFY` against them. One pass to close, once they exist.
- **Branch.** `phase6-integration` is now ~55 commits ahead of a stale `origin/main` that sits at
  Phase 5-era work. Whether to merge or rename is still a decision nobody has made.
