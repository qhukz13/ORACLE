# OQ-22 measurement 2 — canvas vs SVG, answered

**Date:** 2026-09-09 · **Scope:** the one OQ-22 measurement left open on 2026-08-26 ·
**Outcome:** [ADR-0023](../../docs/DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered)
**confirmed** — but not by the gate it wrote, and the reason matters more than the verdict.

Three of OQ-22's four measurements ran headless on 2026-08-26. The fourth needed
`requestAnimationFrame` deltas from a window that is actually being composited, and the spike
refused to fake it. ADR-0023 has been **UNCONFIRMED** for two weeks, and it is the only thing
standing between Phase 11 and the knowledge graph view.

## What ran

`apps/desktop/bench/graph-render.html`, in the **Tauri shell** — the real WebView2 window the ADR is
about, not a near-neighbour. `scripts/export_graph_scene.py` rebuilds the scene from the frozen
artifacts (`oq22-graph.positions.npz` + `oq22-docvecs.npz`, fingerprint `e342f8a5…`) rather than
from today's index, and it comes out at **exactly 1,420 nodes and 3,103 edges** — the same scene
measurement 1 laid out. The live index has drifted since (`52989faa…`; `C:/Projects` indexes itself,
so every commit moves it), which is recorded rather than allowed to change what is measured.

| | |
|---|---|
| shell | Tauri 2 → WebView2 `Edg/152.0.0.0` |
| machine | Windows 10 19045, GTX 1050 Ti, 24 logical processors |
| viewport | 860 × 820 CSS px @ dpr 1 (the 1280 × 820 window, less the 420 px panel) |
| **display** | **163.9 Hz — measured, not assumed** |
| camera | fixed 6 s circuit: fit → zoom ×4 → orbit → zoom out, driven off wall-clock |

## Results

Raw data in [`logs/measurements/oq22-render.json`](../measurements/oq22-render.json).

| renderer | fps (med) | p50 | p95 | p99 | worst | frames over budget | first paint | build | pick p50 |
|---|---|---|---|---|---|---|---|---|---|
| **canvas** | **163.9** | **6.1** | 6.2 | 12.1 | 145.5 | **24 / 936  (2.6%)** | 10.1 | 0.9 | **0.0** |
| svg-group | 82.0 | 12.2 | 18.3 | 24.2 | 90.9 | 404 / 420  (96%) | 26.5 | 19.7 | 0.4 |
| svg-constant | 82.0 | 12.2 | 18.3 | 24.3 | 30.3 | 398 / 412  (97%) | 24.0 | 17.2 | 0.3 |

All times in milliseconds. "Over budget" = frames slower than 1.25 × the measured vsync interval.

**Idle CPU**, canvas mounted with the full scene and nothing happening, sampled over the shell and
its six WebView2 children for 30 s: **0.328 CPU-seconds → 1.09% of one core, 0.046% of the
machine.** Against the < 5% gate, comfortably.

## The finding is not "canvas won"

**Against OQ-22's literal gate — 60 fps, idle < 5%, first paint < 1 s — every renderer passes.**
SVG turns in 82 fps, 24 ms to first paint, and would idle just as quietly. A verdict of "canvas,
because 60 fps" would be a verdict the data does not support, and OQ-22 asked for the SVG control
precisely to catch that.

What separates them is the panel, and it only shows up because the harness **measures the vsync
floor instead of assuming 16.67 ms**:

* canvas sits at **p50 6.1 ms — the vsync interval, exactly.** The renderer is never the
  bottleneck; it draws faster than the display can show and waits.
* svg sits at **p50 12.2 ms — exactly twice the vsync interval.** That is not "a bit slower". It is
  the signature of missing the frame budget and falling back to every *second* refresh. 96% of its
  frames are over budget; canvas's 2.6% are.

So: on a 60 Hz display SVG would pass on merit and the canvas complexity would be unjustified at
this node count — which is exactly what OQ-22 suspected. **This machine has a 164 Hz display, so
"60 fps" and "smooth" stopped being the same requirement, and the written gate cannot tell the two
renderers apart.** ADR-0023 is confirmed on the panel the owner actually has.

The ceiling argument survives independently and is the stronger one: at ADR-0023's 10k-document
ceiling the SVG scene becomes roughly **32,000 elements** against today's 4,523 — 7× more, for a
renderer already running at half rate.

## Three things the measurement got wrong before running

**1. The two SVG variants are indistinguishable, and that kills the obvious fallback.**
The harness measures SVG twice on purpose: `svg-group` moves one `<g>` transform and lets nodes
scale with zoom (SVG's best case), while `svg-constant` keeps nodes a constant size on screen — what
a graph you can click at any zoom needs — at a cost of 1,420 attribute writes per frame. The
hypothesis was that the writes would be the expensive half.

They came out **identical**: 12.2 / 18.3 for both, to a tenth of a millisecond. The cost is
compositing 4,523 elements, not updating them. So there is no SVG optimisation left on the table —
you cannot buy the frame budget back by giving up constant-size nodes, because constant-size nodes
were never what you were paying for.

**2. Canvas hit-testing is *faster* than the DOM's, which inverts an argument in the ADR.**
The canvas `pick()` is a deliberately naive linear scan over all 1,420 nodes, written to keep the
"canvas has to bring its own hit testing" cost visible rather than hiding it behind a quadtree. It
measured **0.0 / 0.1 ms against SVG's 0.4 / 0.9** — 1,420 float comparisons beat `elementFromPoint`
against 4,523 elements by 4–9×.

ADR-0023 pays for canvas hit-testing explicitly ("canvas forfeits free DOM semantics — mitigated as
above"). The **accessibility** half of that cost is real and unchanged: a list-view equivalent and
DOM overlays are still owed. The **performance** half does not exist, and no quadtree is needed at
this corpus size or anywhere near it.

**3. Canvas has the worst single frame of any renderer, and it is not explained.**
145.5 ms — 24× its own median, one janky frame in 936. SVG's worst was 90.9 and 30.3. One outlier
in six seconds is not a budget failure, but it is a visible hitch if it recurs under real
interaction, and the honest status is *unexplained* rather than *dismissed*. Candidates: first-frame
context warm-up, GC of the 3,103-segment path, or the compositor itself. Whoever builds the view
should watch for it rather than rediscover it.

## The trap that nearly produced a fake number

The first attempt ran in a hidden browser pane. In it:

* `document.visibilityState` reported **`"visible"`**
* `document.hidden` was **`false`**
* `setTimeout` fired **normally**
* `requestAnimationFrame` delivered **zero callbacks in 1,500 ms**

Every obvious guard passes there. A harness that checked `visibilityState` — the check anyone would
reach for — would have assembled a plausible frame distribution from a compositor that never ran,
and it would have looked like a result. This is the same failure the 2026-08-26 spike declined to
commit, wearing a disguise.

So the liveness check is now **the frame loop itself and nothing else**: the harness counts real
rAF callbacks before it measures anything, and refuses with an explanation if they are not arriving.
A measurement that cannot be taken must say so.

## One repo defect found on the way

`tauri dev` and the Vite dev server cannot both watch `src-tauri/`. Vite's watcher opened
`target/debug/deps/oracle_desktop.exe` while cargo was linking it and died with `EBUSY`, taking the
whole dev server down and leaving the Tauri window it was serving pointed at nothing — which
presents as "the harness didn't run" rather than as a watcher crash. Fixed in `vite.config.ts` with
`server.watch.ignored: ["**/src-tauri/**"]`. Nothing under that path is a frontend source, and it is
a 2.4 GB tree that was being watched for no reason.

## What this unblocks, and what it does not

**Unblocked:** the Phase 11 knowledge graph view can be built, on canvas, on the edge model
measurement 3 chose, with every budget in OQ-22 now carrying a number.

**Still owed, and not by this measurement:** the list-view equivalent and the DOM overlays that pay
ADR-0023's accessibility debt; `document_vectors` as a required table (measurement 1b); the prompted
re-layout (measurement 4). And the view's own honesty gate — if it answers none of its three
remaining questions better than search does, it gets cut and that outcome gets an ADR.
