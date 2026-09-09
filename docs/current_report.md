# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** find the state, find the next task, build it — then continue.
**Status:** **OQ-22 resolved, ADR-0023 confirmed, and P11-T3 (the knowledge graph) built and
running against the real corpus.** Four commits on `main`, gate green on all seven steps.
**Date:** 2026-09-09
**Dev log:** [canvas vs SVG, answered](../logs/development/2026-09-09-oq22-canvas-vs-svg.md) ·
data in `logs/measurements/oq22-render.json`

---

## The eleven-day gap contained nothing, and OQ-18 is still unmeasured

The 04:00 OQ-18 run **died four minutes in** on 2026-08-29 — exit `1073807364`
(`DBG_TERMINATE_PROCESS`) at 04:05, 256 of 16,717 chunks. `WakeToRun` woke the machine and
something put it back to sleep; the sleep guard did not hold. The task is still registered, has not
fired since, and the checkpoint is 256 chunks — so a re-fire is effectively a cold 2.5–3 h run, and
whatever stopped it wants diagnosing first.

## 1 · OQ-22 measurement 2 — canvas vs SVG, answered

Run in the **real Tauri/WebView2 window** at the scene measurement 3 chose (1,420 nodes,
3,103 edges, rebuilt from the frozen artifacts).

| renderer | fps | p50 | over budget | first paint | pick |
|---|---|---|---|---|---|
| **canvas** | **163.9** | **6.1 ms** | **2.6%** | 10.1 ms | **0.0 ms** |
| svg-group | 82.0 | 12.2 ms | 96% | 26.5 ms | 0.4 ms |
| svg-constant | 82.0 | 12.2 ms | 97% | 24.0 ms | 0.3 ms |

Idle with the graph mounted: **1.09% of one core**.

**The verdict is narrower than "canvas won".** Against OQ-22's written gate — 60 fps — *every*
renderer passes; SVG turns in 82. They separate only against the display's **measured 163.9 Hz**,
where canvas sits at exactly one vsync interval and SVG at exactly two. On a 60 Hz panel SVG would
pass on merit and the canvas complexity would be unjustified at this node count, which is what
OQ-22 suspected when it demanded the control. What carries the decision past this panel is the
ceiling: 10k documents is ~32,000 SVG elements against today's 4,523.

Two results contradicted the reasoning going in. The SVG variants came out **identical**, so the
cost is compositing 4,523 elements rather than the 1,420 per-frame attribute writes — constant-size
nodes are free. And canvas hit-testing, written as a naive O(n) scan *specifically* to keep its cost
visible, beat `elementFromPoint` by **4–9×**; ADR-0023's accessibility debt stands in full, the
performance cost it implied does not exist.

**The trap, which is the more transferable result.** The first attempt ran in a hidden pane where
`visibilityState` reported `"visible"`, `document.hidden` was `false`, `setTimeout` fired normally —
and `requestAnimationFrame` delivered **zero callbacks in 1,500 ms**. Every guard anyone would reach
for passes there. The harness now checks the frame loop itself and refuses rather than reporting.

## 2 · P11-T3 — the knowledge graph, built

`rag/graph.py` + `GET /api/v1/knowledge/graph` + `KnowledgeGraph.tsx` on **Ctrl+5**. Verified
against the real corpus rather than a fixture:

**1,564 documents · 988 wikilinks · 2,477 inferred edges · 195 orphans**, response 154 ms / 496 KB.
`Learning Path.md` answers the reach question with *126 direct, 34 at two hops*. The map also
*shows* measurement 3b's finding instead of merely citing it: the vault and the projects render as
two populations that visibly barely touch.

Two tables were added and neither bumps `_SCHEMA_VERSION` — the disposable-index contract exists so
a schema change cannot leave *stale* rows, and a table that did not exist has none. Bumping would
have charged every existing index a ~1 h rebuild to add two empty tables.

The layout is the measured one ported whole, **including the hash seeding**, which is the part that
matters: array-order seeding makes every position depend on how many documents exist and in what
order they arrived, so indexing one file moves the entire map. It still renders and cannot be
learned. There is a test pinning it.

### Three defects only a real window found

Every one of these passed the test suite, because none of those tests lay anything out.

1. **The canvas was 166 × 11280** — a tall thin strip, off screen, with a 1.9-megapixel backing
   store. The stage panel is a plain block with no definite height, so `height: 100%` resolved to
   `auto` and the 400-row list drove the panel to 11,662 px.
2. **A horizontal scrollbar under a pannable map** — the filter row measured 554 px against a
   523 px stage. That is the one place a reader cannot tell which surface their trackpad will move.
3. **`unplaced` counted documents that can never be placed.** Config has no vector by policy, so on
   the real corpus 106 of 1,561 documents produced a permanent *"106 documents have no settled
   position — Re-layout"* banner offering a 30-second action that could not change the number. A
   prompt that can never be satisfied trains the reader to ignore the one that matters. Now 3, and
   the 3 are real — the watcher had indexed the files this session wrote.

**The re-layout's cost is now measured, not claimed:** 34 s for the layout plus a one-time 88 s
vector backfill. The docstring said 28 s and the button said "~30 s"; the first run anyone makes is
~2 minutes, so both say so now and the API returns the two numbers separately.

## 3 · Four defects found in passing, all fixed

- **ADR-0023 had spent two weeks telling readers the opposite of its own measurement** — its
  consequences still said semantic edges "default off", which measurement 3 disproved on
  2026-08-26. UI.md's table said it too, three paragraphs below a note saying it was backwards.
- **`tauri dev` and Vite could not coexist**: Vite's watcher opened the Rust binary mid-link, died
  with `EBUSY`, and left the Tauri window pointed at nothing — presenting as "the harness didn't
  run". Fixed with `server.watch.ignored`.
- **`check.py` crashed while *printing* a failing step's output** (`UnicodeEncodeError`, cp1252), so
  the gate died exactly when it had something to say and the failure was lost. It read as a broken
  script rather than a broken build.
- **A security-suite test was racing its own startup.** The app-detachment test launched
  `python.exe` with no arguments and no stdin — which exits in milliseconds — then asserted it was
  still alive, failing about one run in three and blaming the Job Object. It now launches something
  that sleeps: five for five, and faster.

## Global search still misses its budget

Measured for the first time (the previous session could not — `know.*` was returning refusals):
warm **504–1,467 ms**, cold **7,932 ms**, against TESTING.md's **p95 < 300 ms**. Not a regression —
the first time the number could be taken. It wants its own task.

## Gate

`scripts/check.py` — all seven steps green on an unloaded machine (ruff format, ruff lint, mypy,
tsc, pytest, security, vitest). The CPU-starvation flake was seen once more under real load and is
now on its **fourth** sighting across two distinct tests, which is past the point where those
wall-clock assumptions should stay implicit.
