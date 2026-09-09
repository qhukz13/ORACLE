# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P11-T3 remainder — what the graph view still owes.** The map is built and running; three
described pieces are not.

**Phase:** [11 — execution visualisation & advanced UI](ROADMAP.md#phase-11--execution-visualisation--advanced-ui--capability-arc) · **Scope:** Capability arc
**Status:** `READY` · **Set:** 2026-09-09 · **Blocked on:** nothing

**Done 2026-09-09:** [OQ-22](OPEN_QUESTIONS.md#oq-22) resolved on all four measurements ·
[ADR-0023](DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered)
confirmed · the data layer (`rag/graph.py`, `document_vectors`, `document_positions`) · the API
(`GET /api/v1/knowledge/graph`, `POST /api/v1/knowledge/relayout`) · the view on **Ctrl+5**,
verified against the real corpus at 1,564 documents / 3,465 edges.
[Report](current_report.md) · [dev log](../logs/development/2026-09-09-oq22-canvas-vs-svg.md)

### What remains

1. **Retrieval-trace edges** — [UI.md §11b](UI.md#11b-the-knowledge-graph--phase-11)'s *use*
   question: "what did ORACLE just retrieve, and from where". Episodic, from the event log, shown
   only in trace mode. This is the only one of the three remaining questions the view cannot
   currently answer at all.
2. **Collection hulls** — a tinted region behind each cluster, so collection is not carried by
   colour alone. Today it is, which is a standing violation of UI.md §1 on this surface.
3. **Select-as-context** — feed selected documents into a real context package, and if that package
   later egresses, the ordinary preview prices it.

Not owed, deliberately: *bridges*. Measurement 3b struck it — one edge joins `notes` to `projects`
at every k and every threshold.

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
- **OQ-18 never ran.** The 04:00 2026-08-29 attempt died at 04:05 after 256 of 16,717 chunks —
  exit `1073807364` (`DBG_TERMINATE_PROCESS`); the machine went back to sleep despite `WakeToRun`.
  The task is still registered and has not fired since. Diagnose the sleep before re-firing, or it
  stops again; treat it as a cold ~2.5–3 h run. On collection, the 2026-08-28 answer-key correction
  still applies: **38/38 queries carry an answer-key chunk in their top-12 lexical candidates**, and
  the old "0/38" diagnostic was broken from birth.
- **Global search misses its budget, measured for the first time on 2026-09-09**: warm 504–1,467 ms
  against **p95 < 300 ms**, cold 7,932 ms. The previous session could not measure it (`know.*` was
  refusing). Wants its own task — profile before optimising; the cold number smells like model load.
- **The reindex is still unfired** — 57% of live rows exceed the 1200-char cap. `POST
  /api/v1/knowledge/reindex` is verified live. Full rebuild ~1 h synchronous. Note it will also
  repopulate `document_vectors` as it goes, which makes the graph's one-time 88 s backfill free.
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
