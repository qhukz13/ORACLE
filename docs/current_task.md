# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P11-T3 — the knowledge graph view.** Now unblocked, and every budget it must hold has a number.

**Phase:** [11 — execution visualisation & advanced UI](ROADMAP.md#phase-11--execution-visualisation--advanced-ui--capability-arc) · **Scope:** Capability arc
**Status:** `READY` · **Set:** 2026-09-09 · **Blocked on:** nothing

[OQ-22](OPEN_QUESTIONS.md#oq-22) is **resolved on all four measurements** as of 2026-09-09 and
[ADR-0023](DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered) is
**confirmed**: build it on canvas, at k=4 / thr=0.85, with semantic edges **on**. Sequencing rule 6
is satisfied — the measurements came first.
[Report](current_report.md) · [dev log](../logs/development/2026-09-09-oq22-canvas-vs-svg.md)

## What to build

Spec: [UI.md §11b](UI.md#11b-the-knowledge-graph--phase-11), which now carries the measured
rendering budgets. In rough order:

1. **The offline layout pass and persisted positions.** `document_vectors` is a **required table**
   (measurement 1b: without it, incremental indexing spends 52 s against a < 5 s budget and it
   gets misdiagnosed as slow layout). Positions persist in `knowledge.db`; seeding is a **hash of
   the node's own id**, never the array index — that bug is what made the stability metric
   unresponsive, and it breaks ADR-0013's spatial-memory argument at the source.
2. **The canvas view**, `Ctrl+5`. Budgets to hold, all measured 2026-09-09: frame p50 **6.1 ms**
   (one vsync on this 164 Hz panel — *not* 16.7 ms), idle **< 5% CPU** (canvas measured 1.09% of one
   core), first paint **< 1 s** (measured 10.1 ms). Node radius is **constant on screen at every
   zoom** — measured free, so specify it rather than budget for it. Hit-testing is a plain linear
   scan; it beat `elementFromPoint` by 4–9× and needs no spatial index at this corpus size.
3. **The list-view equivalent and the DOM overlays.** This is ADR-0023's accessibility debt and it
   is the price of shipping on canvas — the measurement retired the *performance* half of that
   clause and left this half untouched. Every graph action must exist in the list, and it passes
   the axe audit like every other surface.
4. **Prompted re-layout**, not buried: incremental placement is stable but drifts
   (Jaccard@10 0.477 at a 5% holdout against a 0.70 gate), and a full re-layout costs 28 s.

**The view answers three questions, not four.** Shape, neglect, reach. *Bridges* was struck by
measurement 3b — this corpus holds exactly one edge joining `notes` to `projects` at every k and
every threshold, and no tuning invents a relationship that is not there. It inherits the orbit's
honesty gate: if it answers none of the three better than search does, it gets cut and that gets an
ADR.

## The harness is reusable, and it is how the budgets stay honest

`apps/desktop/bench/graph-render.html` measures the real thing in the real window. Re-run it against
the built view rather than re-deriving numbers by hand:

```bash
npm --prefix apps/desktop run tauri -- dev --no-watch --config <override with ?autorun=1>
```

It writes `logs/measurements/oq22-render.json` itself; `?idle=canvas` mounts and stops so an external
sampler has a window. **It refuses to run where frames are not arriving** — which is not a
formality: in a hidden pane, `visibilityState` reports `"visible"`, timers fire, and `rAF` delivers
nothing. Regenerate the scene with `uv run python scripts/export_graph_scene.py`.

---

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
  /api/v1/knowledge/reindex` is verified live now. Full rebuild ~1 h synchronous.
- **Palette results are not discoverable to assistive tech** — `<li role="option">` with `onClick`,
  no `role="combobox"`, no `aria-activedescendant`. The rest of the a11y audit is 15/15.
- **`DATABASE.md`'s `facts`/`attempts`/`devices` blocks are still the pre-build sketch.**
- **A merge-gate test fails under CPU starvation** (`test_a_long_burst_arrives_complete`) — seen a
  third time on 2026-09-09, under real load, green immediately after on an idle box. `pytest-timeout`
  (120 s) bounds the hang; the wall-clock assumption underneath is still implicit. Third sighting is
  an argument for making it explicit.
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
