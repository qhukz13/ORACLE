# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P12-T5 — the first real `continue` run.** ⚠ **A person's to run, not an agent's.**

**Phase:** [12 — project state & the continue loop](ROADMAP.md#phase-12--project-state--the-continue-loop--residency-arc) · **Scope:** Residency arc
**Status:** `SET` · **Set:** 2026-08-26 · **Blocked on:** a human, and Ollama

**Done in Phase 12:** T1 the entity · T2 the `continue` intent · T3 the briefing · T4 both
surfaces rendered.
[T1](../logs/development/2026-08-26-p12t1-project-entity.md) ·
[T2](../logs/development/2026-08-26-p12t2-continue-intent.md) ·
[T3](../logs/development/2026-08-26-p12t3-briefing.md) ·
[T4](../logs/development/2026-08-26-p12t4-sidebar-and-briefing.md)

---

## Why this is not an agent's task

Every step is gated and previewable, and that is the point: approvals expire in **180 s**, so
firing this unattended writes a *refused* run into the very table the run exists to populate.
The same reasoning has kept `oracle-selfcheck` staged and unfired since this morning.

## What to run

```bash
uv run oracled
```

```bash
npm --prefix apps/desktop run dev
```

Then, in the command bar: **`continue ORACLE`**.

**Ollama must be up.** `continue` is classified by the router and there is no slash-command
bypass for it — the pre-router matches pipeline *names*, not intents. If Ollama is down the turn
will say so rather than doing anything surprising.

## What should happen

1. The router resolves `ORACLE`, or asks which project if it cannot.
2. `_continue_project` registers it (already done — T4's live verification registered it), reads
   open tasks from `tasks`, and reads this repository's own `docs/current_task.md` as
   **`local_foreign` evidence**.
3. `continue.derived` is emitted, naming the files that were quoted in.
4. A plan is authored, and **an approval card appears** naming those files under
   `untrusted_sources`.
5. Approving runs the graph. Every delegation asks again before anything leaves the machine.

## What to watch for, and record

- [ ] Does the router actually classify `continue`? **This is [OQ-25](OPEN_QUESTIONS.md#oq-25)'s
      first real evidence** — the intent eval has not been re-run since an eleventh label was
      added, and the named risk is confusion with `run` and `modify`.
- [ ] Does the objective read sensibly, or is `docs/current_task.md` too long/too noisy to be
      useful evidence? `MAX_NOTE_CHARS` is 1200 and was chosen without a real sample.
- [ ] Does the approval card name `docs/current_task.md`?
- [ ] Afterwards: does the **briefing** show the run? Does the **sidebar** show real counts?
      Both currently render numbers no live run has ever produced.

**Write down what happens** — `logs/development/2026-08-26-p12t5-first-run.md` — including if it
goes wrong. That is the whole value of this task.

---

## What this unblocks

`tasks` is **0 rows**, and has been for the entire life of the supervisor arc. Once it is not:

- **P11-T2 / [OQ-14](OPEN_QUESTIONS.md#oq-14)** — the orbital view's go/no-go can finally be
  judged against real data instead of a picture we drew ourselves.
- **P11-T3/T4** — the execution tree's acceptance criteria become judgeable.
- **`TaskTree.test.tsx`** — its fixture can be re-recorded from the wire, which is the standing
  fix for a component that is green on a shape the app cannot produce.
- **P12-T4's numbers** — the sidebar counters and the briefing's arithmetic get their first real
  input.

`oracle-selfcheck` remains the cheaper unblock (~5 min, local, **no egress**, one approval card,
six steps) if a full `continue` is too much for a first run. It produces a real six-task graph.

---

## Carried over, not forgotten

- **The a11y audit covers 12 of 15 components** — measured 2026-08-26, correcting
  `current_state.md`, which said only `Inspector` was uncovered. The three are `Inspector`,
  `Citations` and `EgressPreview`; two of them predate Phase 12.
- **[OQ-25](OPEN_QUESTIONS.md#oq-25) — the intent eval was not re-run.** T5 is its first real
  evidence. `scripts/eval_intent.py` needs `continue` cases adding.
- **`make eval` and `make perf` are documented in TESTING.md §8 and defined nowhere.**
- **[OQ-24](OPEN_QUESTIONS.md#oq-24) — the observation fan-out is unmeasured**, so the sidebar
  shows no branch or dirty count. **If it misses, observe lazily per row — never cache.**
- **The briefing inspector is a stopgap.** `onInspect` routes a task id into the *turn* selector,
  because the inspector has no task branch yet. That is P11-T5.
- **P11-T5** — switchable centre stage, `Ctrl+1..4`, `TaskTree` in its own view, the inspector's
  task branch, mounting `KnowledgeHealth` (built, 11 passing tests, imported by nothing). T4 has
  now added a fourth stage (`briefing`) by the same ad-hoc mechanism T5 is meant to replace, so
  the shape it fixes has one more caller than it did.
- **P9-T3b — the OQ-18 corpus run is RUNNING again.** Re-fired **2026-08-28 00:47:31**;
  expect it to finish around **03:15–03:45** (the script says 2.5–3 h). Health at +6.5 min:
  6,053 CPU-seconds, 2.3 GB RSS, 73 threads — `onnxruntime` on all cores.
  **`logs/measurements/oq18-translated.txt` will look empty until it ends**: Python
  block-buffers stdout when redirected, so an unchanging file is not a stuck job. Check the
  worker instead, or wait for the trailing `=== finished … with exit code N ===` line.
  **Do not kill stray `python` processes while it runs** — that is exactly what ended the
  previous attempt (`LastTaskResult 3221225786` = `STATUS_CONTROL_C_EXIT`).
  *The attempt before it* died within seconds of starting on 2026-08-27 23:11, leaving only a
  header line and a `^C`. Its output is untracked on purpose — a two-line artifact filed under
  `logs/measurements/` is worse than no file, because the next reader finds an answer where
  there is none. The current run overwrites it.
  On collection: compose `dense_mt` against `dense_xl`, confirm or flip
  `Settings.translate_queries`, decide `en-relay-dockerfile`, resolve OQ-18, state the
  answer-key correction wherever pre-2026-08-26 recall numbers are quoted, then
  `Unregister-ScheduledTask`.
- **The `chunker_version` guard does not fire on the indexes it was written for.** 57% of
  14,586 live rows exceed the 1200-char cap, longest 4,055. The database wants a reindex.
- **A merge-gate test that fails under CPU starvation**, and **no `pytest-timeout` installed** —
  which makes a hang expensive to bisect. One gate run hung on 2026-08-26 under concurrent load
  and passed quietly on retry.
- **`DATABASE.md`'s `facts`/`attempts`/`devices` blocks are still the pre-build sketch.**
- **Palette results are not discoverable to assistive tech** — `<li role="option">` with
  `onClick`, no `role="combobox"`, no `aria-activedescendant`.
- **A correction typed while a graph runs is refused**, because "never mid-plan" is implemented
  literally. The fix, when somebody hits it, is a queue — not an exception.
- **Scheduled pipeline runs** are post-MVP; PIPELINES.md §5's "nothing above T1 unattended" is
  unenforced because nothing schedules anything.
- **The visual references for the UI vision were never attached** and are not in the repository.
  UI.md §1/§14/§15 are `TO VERIFY` against them. T4 shipped two surfaces without them.
- **Branch.** `phase6-integration` is ahead of a stale `origin/main` at Phase 5-era work.
