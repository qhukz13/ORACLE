# OQ-22: the knowledge graph, measured before it was drawn

**Task:** P11-T1 · **Date:** 2026-08-26 · **Script:** `scripts/measure_graph.py` ·
**Data:** `logs/measurements/oq22-graph.{json,txt}` · **Corpus fingerprint:** `e342f8a55a6ce17d`

[OQ-22](../../docs/OPEN_QUESTIONS.md#oq-22) asked four questions about a view nobody had built, on a
corpus nobody had measured. Three are answered here. The fourth needs a real window on this GPU and
is the one thing left.

The corpus census and the link-table finding that reordered the spike are in
[the planning log](2026-08-26-p11-graph-data.md); this is the measurement itself.

> **A note on the corpus.** It grew from 1,414 to 1,420 documents *during* this work, because
> `C:/Projects` is an indexed root and the live watcher was picking up my own commits. Every run
> stamps a fingerprint for that reason. The proportions below do not turn on six documents.

---

## Measurement 3 — the edge model (run first, and it decides everything)

OQ-22 lists this third. It ran first because the edge model decides the node count and the node
count is what the rendering question is asked at: measuring canvas-vs-SVG against a 166-node graph
and concluding "SVG is comfortable" would be measuring a graph the product may not ship.

`k` per node, threshold on cosine similarity, merged with the resolved wikilinks. Orphans exclude
the 95 `config` documents, which have no vector **by policy** (RAG.md §2) and would otherwise put a
95-item false floor under the "neglect" question.

| config | edges | cross-coll | orphans | components | largest | avg deg | 2-hop median |
|---|---:|---:|---:|---:|---:|---:|---:|
| **explicit only** | 1,172 | **2** | **1,168** | 1,264 | 11.1% | 1.6 | **0** |
| k=2 thr=0.80 | 2,618 | 1 | 44 | 189 | 42.2% | 3.7 | 8 (0.6%) |
| k=3 thr=0.80 | 3,383 | 1 | 44 | 171 | 49.1% | 4.8 | 14 (1.0%) |
| **k=4 thr=0.85** | 3,103 | 1 | 189 | 357 | 34.8% | 4.4 | 13 (0.9%) |
| k=4 thr=0.80 | 4,084 | 1 | 44 | 162 | 53.8% | 5.8 | 22 (1.6%) |
| k=6 thr=0.80 | 5,416 | 1 | 44 | 159 | 85.4% | 7.6 | 42 (3.0%) |
| k=8 thr=0.80 | 6,644 | 1 | 44 | 159 | 85.4% | 9.4 | 60 (4.2%) |
| any k, thr=0.95 | ~1,134 | 1 | 904 | 1,125 | 11.1% | 1.6 | 0 |

**The threshold is the knob after all, and the earlier claim that it was not was measured in the
wrong range.** Below 0.8 the sweep saturates — 0.5 and 0.6 give identical edge sets — which is what
produced the "k is the real knob" note in the planning log. Between 0.80 and 0.95 the threshold
moves orphans from 44 to 904. Both statements are true of their own range, and the useful one is
this: **the interesting band is 0.80–0.90, and 0.95 is indistinguishable from having no semantic
edges at all.**

### What this answers, and the one thing it does not

* **Neglect — answered, and only by semantic edges.** Explicit-only leaves **1,168 of 1,325
  embeddable documents orphaned** across 1,264 components. Semantic edges take that to 44. Without
  them the "what am I neglecting?" question returns "almost everything", which is not an answer.
* **Reach — answered, comfortably.** The 2-hop neighbourhood is 0.6–1.6% of the corpus in the
  usable band. The hairball risk was real but sits above k=6/0.80, where the largest component
  jumps to 85% and the 2-hop median to 42.
* **Shape — answered for clusters, not for bridges.** 357 components at k=4/0.85 with a 35% giant
  is a readable structure.
* **Bridges — NOT answered, at any configuration.** This is the finding that matters.

### Cross-collection edges: 1

UI.md §11b's first question is *"where are the bridges between a vault and a project?"*. Across the
entire sweep — every k, every threshold — the graph contains **one** edge joining `notes` to
`projects`. The explicit link graph has two.

That is not a tuning failure. The notes are prose about machine learning; the projects are
TypeScript, Rust and Python. `bge-m3` is right that they are not about the same things, and no
threshold will invent a relationship that is not there. **The view can answer three of its four
questions on this corpus and cannot answer the fourth, because the answer is "there are none".**

Which is itself worth showing — "your notes and your code do not touch" is a true and slightly
uncomfortable fact about this corpus — but §11b promises a *feature* that finds bridges, and a
feature that reliably finds one edge is a sentence, not a view.

---

## Measurement 1 — layout cost

Fruchterman-Reingold, vectorised, 200 iterations, at the chosen k=4/thr=0.85 edge model.

| | measured | gate | |
|---|---:|---:|---|
| cold layout, 1,420 nodes / 3,103 edges | **27.8 s** | ≤ 10 min | **pass**, by 20x |
| peak RSS | **121 MB** | ≤ 500 MB | **pass** |
| incremental placement, p95 | **0.032 ms** | ≤ 250 ms | **pass**, by four orders of magnitude |

Incremental placement is a centroid of a handful of 2-vectors; it was never going to be the
expensive part, and now that is a number rather than an assumption.

### But the expensive part is reading the vectors, and it is not in this table

| | |
|---|---:|
| read 13,771 chunk vectors out of `vec0` | **51.8 s** |
| mean-pool them into 1,325 document vectors | **0.10 s** |
| full 1,325×1,325 cosine kNN | ~0.1 s |

**The arithmetic is 0.2% of the cost.** Any design that re-derives document vectors on each
incremental index spends 52 seconds against TESTING.md §6's `< 5 s` budget for indexing one file —
and would be diagnosed as "the layout is slow", which it is not.

**So `document_vectors` is a required table, not an optimisation.** It should be written by
`store.put()`, which is the single write chokepoint both `indexer.index()` and the watcher already
go through. This spike caches to an `.npz` and is 50 s faster on every re-run because of it.

### Where the full-matrix approach stops

| N | 30 iterations | projected 200 | pair matrix |
|---:|---:|---:|---:|
| 500 | 0.52 s | 3.4 s | 2 MB |
| 1,000 | 2.08 s | 13.8 s | 8 MB |
| 2,000 | 8.31 s | 55.4 s | 32 MB |
| 4,000 | 30.10 s | 200.7 s | 128 MB |

Clean O(N²): every doubling costs 4x. Extrapolated to ADR-0023's stated 10,000-document ceiling
that is **~21 minutes and ~800 MB** — inside the 30-minute gate, outside the 500 MB one. So the
ceiling needs Barnes-Hut or a grid approximation, and **the current corpus does not**: at 1,420 the
naive version costs 28 seconds. Build the simple one; the note here is what to reach for if the
corpus grows by 7x.

---

## Measurement 4 — stability, reframed as a holdout

OQ-22 asks whether the map "stays recognisable after a week of real edits", which cannot be answered
inside a phase. Substituted: lay out the corpus minus a random slice, place the slice incrementally
at neighbour centroids, re-layout everything, and compare neighbour sets.

| holdout | Jaccard@10 vs a full re-layout | gate |
|---:|---:|---|
| 5% | **0.477** | ≥ 0.70 — **missed** |
| 10% | 0.410 | |
| 20% | 0.336 | |

### The first run of this measured its own noise, and finding out why changed the layout

The first attempt returned **0.249 at every holdout fraction** — flat across 5%, 10% and 20%. A
metric that does not respond to its own independent variable is not measuring it.

The cause was in the layout, not the metric. Initial positions came from
`rng.normal(size=(n, 2))` — seeded by **array index**. So the same document starts in a different
place depending on how many documents there are and what order they arrived in, and a force layout
amplifies a different start into a different picture. Two layouts of *almost the same graph* were
therefore unrelated, and the number was measuring that.

**Initial positions are now a hash of the node's own id.** Same document, same starting point,
regardless of what else is in the corpus. The measurement immediately began behaving — 0.477 /
0.410 / 0.336, monotone in the holdout fraction.

This is worth more than the number it produced. **ADR-0013's whole argument is that a person learns
where things are**, and array-order seeding quietly breaks that at the source: reindex after adding
one file and every position shifts. It would have shipped as "the layout is unstable, add more
iterations".

### And the gate is still missed

0.477 at 5% means about half of a document's ten nearest neighbours differ between the
incrementally-grown map and the one a re-layout would produce. The **0.70 gate was mine**, set in
`current_task.md` before the run, and it is missed by a wide margin at every fraction.

What that does and does not mean:

* ADR-0023's actual promise — *positions are frozen and stable across sessions* — **holds**. The map
  does not move on its own. Incremental placement moves nothing that already exists, by construction.
* What degrades is **fidelity**: the longer you go without a re-layout, the further the picture is
  from what the data now says.
* So re-layout cannot be a rare admin action buried in a health view. It needs to be **offered, and
  prompted** once growth passes a few percent. At 28 seconds for the whole corpus that is cheap —
  which is the happy consequence of measurement 1.

---

## Measurement 2 — canvas vs SVG: not answered here

It needs `requestAnimationFrame` deltas from a compositing window on this GTX 1050 Ti, inside
WebView2, which is what Tauri ships. The environment this spike ran in has no displayed browser
pane, so any frame timing it produced would be a number from a page that was never drawn — exactly
the kind of measurement this project keeps having to throw away.

`logs/measurements/oq22-graph.positions.npz` holds the frozen positions (`ids`, `pos`,
`collection`) so the harness has its input the moment somebody runs it at a real window.

**What the other measurements imply about it**, to be confirmed rather than assumed: at the chosen
edge model the scene is **1,420 nodes and 3,103 edges**. That is unremarkable for SVG. ADR-0023
chose canvas against a 10,000-document ceiling the corpus is 7x below, and OQ-22 explicitly asks for
the SVG control *"to keep ADR-0023 honest — if SVG survives at this node count, the canvas
complexity is unjustified"*. The honest current status of ADR-0023 is **unconfirmed**, and it should
not be treated as settled until somebody runs the harness.

---

## Verdict

**OQ-22 is resolved on three of four measurements, and the view is worth building — narrower than
§11b describes it.**

1. **Semantic edges ship on, and are not optional.** Explicit links cover 11% of the corpus; without
   semantic edges there is no graph. k=4/thr=0.85 is the recommended default: 3,103 edges, 189
   orphans, a 35% giant component, 2-hop median 0.9%. The 0.80–0.90 band is the useful one and the
   knob should be exposed there, not from 0.
2. **The bridges question is struck**, with a number: one cross-collection edge at every setting.
   §11b's four questions become three, and the view's description must stop promising the fourth.
3. **Layout is cheap and the vector read is not.** `document_vectors` is a required table.
4. **Re-layout must be prompted, not buried.** Incremental placement is stable but drifts; it costs
   28 seconds to fix.
5. **ADR-0023 remains unconfirmed** pending the one measurement that needs a GPU and a window.

### What would have made this a no

Stated in `current_task.md` before the run: no configuration giving median 2-hop < ~10%, *and*
> 0 cross-collection edges, *and* orphans meaningfully below the explicit-only baseline. Two of
three passed decisively. The third passed on a technicality — one edge is `> 0` — and the honest
reading is that it failed, which is why the bridges question is being struck rather than claimed.
