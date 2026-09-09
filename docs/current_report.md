# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** find the state, find the next task, build it. The repo had been cold for eleven days; the
one unblocked, highest-value item was **OQ-22's measurement 2 — canvas vs SVG**, the last open
measurement in the project and the only thing holding
[ADR-0023](DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered)
at UNCONFIRMED.
**Status:** **measured, in the real Tauri/WebView2 window. OQ-22 is resolved on all four
measurements. ADR-0023 is confirmed. The Phase 11 knowledge graph view is unblocked.**
**Date:** 2026-09-09
**Dev log:** [canvas vs SVG, answered](../logs/development/2026-09-09-oq22-canvas-vs-svg.md) ·
data in `logs/measurements/oq22-render.json`

---

## What the eleven-day gap actually contained: nothing

The 04:00 OQ-18 corpus run **died four minutes in** on 2026-08-29 — exit code `1073807364`
(`DBG_TERMINATE_PROCESS`) at 04:05, 256 of 16,717 chunks done. `WakeToRun` woke the machine and
something put it back to sleep; the sleep guard did not hold. The scheduled task is still
registered, has not fired since, and **OQ-18 remains unmeasured**. Nothing else ran: no commits, no
daemon, since 2026-08-28.

The re-fire is still `Start-ScheduledTask ORACLE-OQ18-eval`, and it still resumes from the last
checkpoint — but the checkpoint is 256 chunks, so this is effectively a cold ~2.5–3 h run, and
whatever stopped it needs diagnosing first or it will stop again.

## The measurement

`apps/desktop/bench/graph-render.html` + `scripts/export_graph_scene.py`, run in the **Tauri shell**
— real WebView2, not a near-neighbour — at the scene measurement 3 chose, rebuilt from the frozen
artifacts to exactly **1,420 nodes and 3,103 edges**.

| renderer | fps | p50 | p95 | worst | over budget | first paint | pick p50 |
|---|---|---|---|---|---|---|---|
| **canvas** | **163.9** | **6.1 ms** | 6.2 | 145.5 | **2.6%** | 10.1 ms | **0.0 ms** |
| svg-group | 82.0 | 12.2 ms | 18.3 | 90.9 | 96% | 26.5 ms | 0.4 ms |
| svg-constant | 82.0 | 12.2 ms | 18.3 | 30.3 | 97% | 24.0 ms | 0.3 ms |

Idle CPU with the graph mounted: **1.09% of one core** (gate < 5%).

**The verdict is narrower than "canvas won", and that is the finding.** Against OQ-22's written gate
— 60 fps — *every* renderer passes; SVG turns in 82 fps. The two only separate against the display's
**measured 163.9 Hz**: canvas sits at p50 6.1 ms, the vsync interval exactly, never the bottleneck;
SVG sits at 12.2 ms, exactly twice it — the signature of missing the budget and dropping to every
second refresh. **On a 60 Hz panel SVG would pass on merit and the canvas complexity would be
unjustified at this node count**, which is what OQ-22 suspected when it demanded the control. What
carries the decision beyond this panel is the ceiling: 10k documents makes the SVG scene ~32,000
elements against today's 4,523.

Two results contradicted the reasoning going in. The two SVG variants are **identical** — the cost
is compositing 4,523 elements, not the 1,420 per-frame attribute writes — so constant-size nodes are
free and no SVG optimisation remains to be taken. And canvas hit-testing, written as a naive O(n)
scan *specifically* to keep its cost visible, is **4–9× faster** than `elementFromPoint`. ADR-0023's
accessibility debt for canvas stands in full; the performance cost it implied does not exist.

## The trap, which is the more transferable result

The first attempt ran in a hidden browser pane where `document.visibilityState` reported
**`"visible"`**, `document.hidden` was **`false`**, `setTimeout` fired normally — and
`requestAnimationFrame` delivered **zero callbacks in 1,500 ms**. Every guard anyone would reach for
passes there, and the harness would have produced a plausible frame distribution from a compositor
that never ran. The liveness check is now the frame loop itself and nothing else, and the harness
refuses with an explanation rather than reporting.

## Two defects found on the way

1. **`tauri dev` + Vite could not coexist.** Vite's watcher opened
   `src-tauri/target/debug/deps/oracle_desktop.exe` mid-link and died with `EBUSY`, taking the dev
   server down and leaving the Tauri window pointed at nothing — presenting as "the harness didn't
   run". Fixed: `server.watch.ignored: ["**/src-tauri/**"]`.
2. **ADR-0023 had been telling readers the opposite of a two-week-old measurement.** Its consequences
   still said semantic edges "default off"; measurement 3 established on 2026-08-26 that off-by-
   default is a scatter of dots (1,168 of 1,325 documents orphaned). UI.md §11b had been corrected;
   the ADR had not. Amended, and UI.md's own table row — which still said "off by default" three
   paragraphs under a note saying it was backwards — fixed too.

## Global search: first real latency numbers, and they miss

The daemon came up as a child of the Tauri shell, from current source, so the three things the last
report said needed a restart are now live and verified: `GET /api/v1/search` returns 200, the
`know.*` model fix is in, and `POST /api/v1/knowledge/reindex` exists.

Measured for the first time (the previous session could not — `know.*` was returning refusals):

| query | latency |
|---|---|
| cold, first query | **7,932 ms** |
| knowledge graph | 504 ms |
| project state | 541 ms |
| task graph | 665 ms |
| canvas rendering | 831 ms |
| embedding model | 1,467 ms |

Against **TESTING.md's p95 < 300 ms budget**, warm search is **2–5× over** and cold is **26× over**.
This is not a regression — it is the first time the number could be taken. It wants its own task; it
is listed below rather than quietly absorbed.

## Gate

`scripts/check.py` — all seven steps green (ruff format, ruff lint, mypy, tsc, pytest, security,
vitest). One earlier run of the gate timed out in a WebSocket test *while the Tauri window, WebView2,
the daemon and a CPU sampler were all running*; a full `pytest` immediately after was **1245 passed,
1 skipped**, and the gate was green on an unloaded machine. That is the documented CPU-starvation
flake, not a new failure — and it is now the third sighting, which is an argument for making those
wall-clock assumptions explicit.
