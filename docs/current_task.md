# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P11-T3 is done — §11b is built. Next up: collect OQ-18, then the tool-selection defect.**

**Phase:** [11 — execution visualisation & advanced UI](ROADMAP.md#phase-11--execution-visualisation--advanced-ui--capability-arc) · **Scope:** Capability arc
**Status:** `READY` · **Set:** 2026-09-09 · **Blocked on:** nothing

**Done 2026-09-09:** [OQ-22](OPEN_QUESTIONS.md#oq-22) resolved on all four measurements ·
[ADR-0023](DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered)
confirmed · the data layer (`rag/graph.py`, `document_vectors`, `document_positions`) · the API
(`GET /api/v1/knowledge/graph`, `POST /api/v1/knowledge/relayout`) · the view on **Ctrl+5**,
verified against the real corpus at 1,564 documents / 3,465 edges.
[Report](current_report.md) · [dev log](../logs/development/2026-09-09-oq22-canvas-vs-svg.md)

### What remains

**§11b is complete as specified.** Traces, hulls, the legend and select-as-context all landed
2026-09-09. Two things are outstanding, and neither is new construction:

1. **A live retrieval trace has never been seen.** The mechanism is unit-tested against the exact
   payload `rag/retrieval.py:to_citation` emits, but no real retrieval has lit the map up, because
   the router does not reach for `know.search` — see the tool-selection finding below. Worth one
   confirmation the first time a `know.*` call actually runs.
2. **The collection hull is on probation.** It traces each collection's core (hulling the whole
   collection just hulls the orphan ring, which is a polygon over the entire map). It reads weakly
   on this corpus, and the legend plus the per-row collection name are what actually discharge
   UI.md §1. If it does not prove useful in real use, cut it — a faint polygon that clarifies
   nothing is decoration.

### Two things to know before touching it

- **Frame budgets written as constants are wrong here.** This display is **163.9 Hz**, so one vsync
  is **6.1 ms**, not 16.7. Measure the floor; never assume it. `apps/desktop/bench/graph-render.html`
  re-measures the real thing in the real window and records itself to `logs/measurements/`.
- **The view must never simulate.** It draws on demand, one coalesced `requestAnimationFrame` per
  change. A standing loop would burn a core to say nothing and break the measured 1.09% idle.

## Still owed, in rough priority order

- **⚠ P12-T5 is still one human click**, and still an owner's task, not an agent's — approvals
  expire in 180 s, so firing it unattended writes a *refused* run into the table the run exists to
  populate. `tasks` remains **0 rows**, and with it [OQ-14](OPEN_QUESTIONS.md#oq-14) (the orbit's
  go/no-go), the execution tree's acceptance criteria, `TaskTree`'s fixture, and the sidebar
  counters. Start the daemon and UI, type `continue ORACLE` in the command bar, approve the T3
  `confirm_strong` card. `oracle-selfcheck` is the cheaper first fill — local, no egress, ~5 min.
- **OQ-18 restarted 2026-09-09 ~20:10 on a stabilised tree, and three runs' worth of causes are
  now understood.** It was never the idle timer: `STANDBYIDLE` on AC is already `0` (Never), and
  **21 of the last 21 sleeps are `Sleep Reason: Application API`** — another process explicitly
  calls `SetSuspendState`, which `SetThreadExecutionState` cannot veto, so the 2026-08-28
  `keep_system_awake()` hardening was aimed at a timer that was already disabled.
  [Dev log](../logs/development/2026-09-09-oq18-the-wrong-thing-hardened-twice.md).
  **The fix is to survive the sleep:** the task repeats every 30 min, each firing resumes from the
  last 256-chunk checkpoint, `StopOnIdleEnd` is off, and the wrapper exits once
  `oq18-translated.json` exists. The log **appends** now — truncating it is what hid the evidence.
  **Two corpus findings from the 2026-09-09 attempt, both acted on:**
  (a) a retrieval fixture pointed at `Asterim/docs/operations-runbook.md`, which Asterim moved into
  `docs/archive/2026-08-pipeline-era/` — an automatic miss for every arm, depressing absolute recall
  across all of them. **Repointed.** (b) The corpus has grown from 18,153 to 27,920 chunks since the
  2026-08-26 baseline, so **this run's absolute numbers are not comparable to the earlier ones.**
  OQ-18's actual question is a *within-run* comparison of arms, which corpus drift does not
  invalidate — but do not quote the new recall figures against the old ones.
  ⚠ **A 2026-09-09 20:00 "hang" was a misdiagnosis** — 0% CPU samples, 71 threads in Wait and a
  py-spy stack inside `session.run()` were all real, but the pass was merely running at
  **0.20 chunks/s against 2.52**, because two full `check.py` runs were executing in the same
  window. `Win32_Processor` `LoadPercentage` is a stale counter and was trusted over the eval's own
  progress line. A stack in native code proves where a thread *is*, not that it is stuck.
  ⚠ **Do not run the test suite while it runs** — the eval measures `chunks_per_s` and query
  latency, and concurrent work both slows it and corrupts those numbers. **Repo edits are worse than
  slow:** ORACLE indexes itself, so any edit moves the corpus fingerprint and a retry after an edit
  restarts the pass from zero. Leave the tree alone until it lands.
  **On collection:** compose `dense_mt` against `dense_xl`, confirm or flip
  `Settings.translate_queries`, decide `en-relay-dockerfile`, resolve
  [OQ-18](OPEN_QUESTIONS.md#oq-18), then `Unregister-ScheduledTask -TaskName ORACLE-OQ18-eval`.
  The 2026-08-28 answer-key correction still applies: **38/38 queries carry an answer-key chunk in
  their top-12 lexical candidates**, and the old "0/38" diagnostic was broken from birth.
- **Global search misses its budget, measured for the first time on 2026-09-09**: warm 504–1,467 ms
  against **p95 < 300 ms**, cold 7,932 ms. The previous session could not measure it (`know.*` was
  refusing). Wants its own task — profile before optimising; the cold number smells like model load.
- **The reindex is still unfired** — 57% of live rows exceed the 1200-char cap. `POST
  /api/v1/knowledge/reindex` is verified live. Full rebuild ~1 h synchronous. Note it will also
  repopulate `document_vectors` as it goes, which makes the graph's one-time 88 s backfill free.
- **Tool selection picks the wrong tool for a search-intent turn.** Measured 2026-09-09: the 0.8b
  router classified *"search the knowledge index for taint tracking"* as intent `search` — correct —
  and then selected **`fs.list`**, answering *"I don't have a tool for that yet — fs.list needs to
  know which project."* `know.search` was never called. Intent routing is fine; selection is not.
  `scripts/eval_selection.py` is the harness that should be pointed at this. It also means no
  `know.*` call has run in the live system recently, so the retrieval-trace view is unproven
  end-to-end. (qwen2.5:7b does not fit this GPU — the turn stalled with no runner loaded.)
- **Palette results are not discoverable to assistive tech** — `<li role="option">` with `onClick`,
  no `role="combobox"`, no `aria-activedescendant`. The rest of the a11y audit is 15/15.
- **`DATABASE.md`'s `facts`/`attempts`/`devices` blocks are still the pre-build sketch.**
- **Tests with implicit wall-clock assumptions, now on a fourth sighting across two tests.**
  `test_a_long_burst_arrives_complete` fails under CPU starvation and passed immediately after on an
  idle box (2026-09-09). Separately, `test_the_launched_app_survives_the_toolhost_dying` was found
  racing its own startup — it launched `python.exe` with no args and no stdin, which exits in
  milliseconds, then asserted it was alive, failing ~1 run in 3 and *blaming the Job Object*. That
  one is **fixed** (it now launches something that sleeps). The pattern is not: these should become
  explicit perf tests or carry deadlines, rather than staying implicit in the merge gate.
- **A correction typed while a graph runs is refused** — the fix, when somebody hits it, is a queue.
- **Scheduled pipeline runs** are post-MVP; PIPELINES.md §5's "nothing above T1 unattended" is
  unenforced because nothing schedules anything.
- **The visual references for the UI vision were never attached**; UI.md §1/§14/§15 remain
  `TO VERIFY` against them.
- **P11 remainder beyond the graph:** T2 orbit (blocked on OQ-14 → blocked on the click above) · the
  agent queue (needs live task data) · notifications.
- The fossil `phase6-integration` branch can be deleted at leisure.

## Operational notes

- **`main` is the branch.** Commit and push after every task, not batched (owner's standing rule,
  2026-08-28).
- **The daemon holds `.venv\Scripts\oracled.exe`**, so syncing `uv` commands fail with `os error 32`
  while it is up. Use `uv run --no-sync …`, or stop it first — by PID, never by pattern. One plain
  `uv sync` after it stops reconciles the entry-point exe.
- **The Tauri shell spawns `oracled` as a child.** Killing the window takes the daemon with it, and
  a stale window holds `target/debug/oracle-desktop.exe` so the next `cargo` link fails with
  "Access is denied" — stop the old window by PID before rebuilding.
- **Frame budgets written as constants are wrong here.** This display is **163.9 Hz**. Measure the
  vsync floor; never assume 16.7 ms.
