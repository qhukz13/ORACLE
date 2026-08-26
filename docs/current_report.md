# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** **P11-T1 — OQ-22, measured.** Plus Phase 10 (done earlier the same day) and two defects
found by looking rather than by anything breaking.
**Status:** OQ-22 answered on three of four measurements; the fourth needs a real window. The
knowledge graph is worth building, **narrower than the design describes it**.
**Date:** 2026-08-26

---

## P11-T1 — the knowledge graph, measured before it was drawn

`scripts/measure_graph.py` · [dev log](../logs/development/2026-08-26-oq22-knowledge-graph.md) ·
data in `logs/measurements/oq22-graph.{json,txt}` · corpus fingerprint `e342f8a55a6ce17d`.

**Measurement 3 ran first**, against OQ-22's own ordering, because the edge model decides the node
count and the node count is what the rendering question gets asked at.

### The link table is one collection's, and that inverts the design

Explicit wikilinks touch **157 of 1,420 documents — 11%** — and 156 of those are in one Obsidian
vault. `links` is populated only from the Obsidian chunker, and exactly one collection sets
`obsidian: true`, so this is structural rather than incidental.

Explicit-only leaves **1,168 of 1,325 embeddable documents orphaned** across 1,264 components, with
a 2-hop median of **zero**. UI.md §11b and OQ-22 both describe semantic edges as an optional toggle
that might "ship off"; on this corpus they are the difference between a graph and a scatter of dots.
Recommended default **k=4, thr=0.85** — 3,103 edges, 189 orphans, a 35% giant component, 2-hop
median 0.9%.

### One question the view cannot answer, and it is question 1

§11b asks *"where are the bridges between a vault and a project?"*. Across **every** k and **every**
threshold the graph holds **one** edge joining `notes` to `projects`.

That is not a threshold wanting tuning. The notes are ML prose; the projects are TypeScript, Rust and
Python. `bge-m3` is right that they are not about the same things. The promise is **struck from
UI.md §11b** with the number attached, rather than left to disappoint — a view that reliably finds
one bridge is a sentence, not a feature.

### The cost is reading vectors, not laying them out

| | |
|---|---:|
| read 13,771 chunk vectors out of `vec0` | **51.8 s** |
| pool them + full 1,325² cosine kNN | **0.2 s** |
| cold layout, 1,420 nodes / 3,103 edges | **27.8 s** (gate 10 min) |
| peak RSS | **121 MB** (gate 500 MB) |
| incremental placement p95 | **0.032 ms** (gate 250 ms) |

The arithmetic is 0.2% of the work. **`document_vectors` is a required table**, written by
`store.put()` — without it, incremental indexing spends 52 s against a `< 5 s` budget and gets
misdiagnosed as slow layout.

Scaling is clean O(N²): extrapolated to ADR-0023's 10k ceiling, ~21 min and ~800 MB — inside the
time gate, **outside the memory one**. The current corpus does not need Barnes-Hut; a 7x larger one
would.

### A measurement that measured its own noise, and what fixing it exposed

The stability holdout first returned **0.249 at every fraction** — 5%, 10%, 20% alike. A metric that
does not respond to its own variable is not measuring it.

The cause was in the layout: initial positions were seeded by **array index**, so the same document
started somewhere different depending on how many documents existed and in what order they arrived.
Seeding from a hash of the node's own id fixed it, and the numbers became monotone
(**0.477 / 0.410 / 0.336**).

That matters well beyond the measurement. **ADR-0013's whole argument is that a person learns where
things are**, and array-order seeding breaks it at the source: reindex after adding one file and
every position shifts. It would have shipped as "the layout is unstable, add more iterations".

The 0.70 gate — mine, set before the run — is still **missed** at 0.477. Positions stay *stable*
(nothing moves on its own); what degrades is *fidelity*. So re-layout must be **prompted** after a
few percent of growth rather than buried in a health view, and at 28 s that is cheap.

### Not answered: canvas vs SVG

It needs `requestAnimationFrame` deltas from a compositing window on this GTX 1050 Ti inside
WebView2, and this environment has no displayed browser pane. A frame timing from a page that was
never drawn is exactly the class of number this project keeps having to throw away.
`oq22-graph.positions.npz` holds the frozen positions so the harness has its input.
**ADR-0023 is therefore UNCONFIRMED** — at 1,420 nodes the scene is unremarkable for SVG, which is
precisely why OQ-22 asks for that control.

---

## Two defects found by reading, not by breaking

**HALT was bound to `F1`.** UI.md §16 specifies `Ctrl+Alt+Shift+H` and says why — *"deliberately
awkward: four keys, so it cannot be hit by accident"*. The code used one key, and the one it used is
the universal help key, next to Esc. HALT cancels every task, terminates every job object and drops
policy to deny-all until a human resumes. Rebound, with a keybinding test rather than a UI test,
because the failure mode is silent: the app looks identical and the only symptom is somebody's work
stopping when they reached for help.

**The least visible status in the app was the one that means HALTED.** UI.md §14 requires status
≥ 3:1 contrast and singles out amber as "the risky one … must be verified, not assumed". It never
had been — `a11y.test.tsx` disables axe's `color-contrast` rule, correctly, because happy-dom lays
nothing out. So the one rule flagged as risky was the one rule the audit could not check.

`contrast.test.ts` checks it now as a pure function over tokens parsed **out of `styles.css`
itself**, so it cannot drift. **Amber was fine** (7.22–8.99:1); the section guessed wrong. Two others
failed: `--st-halt` at **2.99 / 2.82 / 2.63 / 2.40** — under 3:1 on *every* surface — and `--fg-2`
under 3:1 on the two raised surfaces. Both raised.

---

## Phase 10 — pipelines, done earlier today

A YAML file becomes a validated `Pipeline`, compiles to a task graph and runs on P7's scheduler. No
pipeline executor, no `pipeline_runs` table, no new `TaskKind`. The roadmap's extra criterion is a
passing test: a compiled pipeline and a hand-written graph of the same steps emit identical event
sequences. Two shipped pipelines, both priced against the real policy by a test.

It also uncovered a **P7 defect** — `Limits.timeout_s[TaskKind.TOOL]` is 120 s while `dev.run_tests`
declares 630 s, so any TOOL task running it died at two minutes and was recorded as `TIMEOUT` — and
**five places PIPELINES.md disagreed with reality**, each corrected in the document with its reason.
Detail in the [Phase 10 dev log](../logs/development/2026-08-26-p10-pipelines.md).

## And one dead root that took the watcher down

`C:/Users/qhukz/Documents/MLAI NOTES/ML/AI` had not existed for some time. The indexer skipped it
with a warning; `watchfiles` **refuses to start** on a path that is neither file nor directory, so
that one absent root disabled **live re-indexing for every collection** with a single line at boot
and no other symptom. Removed from `collections.yaml` and from `policy.yaml` (where it was also a
read-only scope root — removing a scope only ever narrows). Verified: `rag.watch_started roots=9`,
no warnings, and a live mtime-only touch now round-trips in 6 ms.

---

## Next

**P11-T2** — OQ-14's go/no-go on the orbital view. See [current_task.md](current_task.md); note that
the `tasks` table is **empty**, so the execution tree, orbit, timeline and queue would all render
activity that has never happened outside a test. The cheapest unblock is a person running
`oracle-selfcheck` once.
