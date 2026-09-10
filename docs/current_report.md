# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** find the state, find the next task, build it — then keep going.
**Status:** **OQ-22 and OQ-18 both resolved. Phase 11's knowledge graph (§11b) is built in full.
Retrieval now reaches a chat turn, which it never did before.** Fourteen commits on `main`, gate
green each time.
**Date:** 2026-09-09 → 2026-09-10
**Dev logs:** [canvas vs SVG](../logs/development/2026-09-09-oq22-canvas-vs-svg.md) ·
[OQ-18 resolved](../logs/development/2026-09-10-oq18-resolved.md) ·
[the sleep that was not a sleep guard](../logs/development/2026-09-09-oq18-the-wrong-thing-hardened-twice.md)

---

## 1 · OQ-22 resolved — ADR-0023 confirmed

Canvas holds **p50 6.1 ms — the vsync interval exactly** on this **163.9 Hz** panel; SVG holds
12.2 ms, exactly twice it, missing 96% of frame budgets. Idle 1.09% of one core, first paint 10.1 ms.

**The verdict is narrower than "canvas won".** Against OQ-22's *written* gate — 60 fps — every
renderer passes; SVG turns in 82. They separate only against the measured refresh rate. On a 60 Hz
panel the canvas complexity would be unjustified at this node count, exactly as OQ-22 suspected.
What carries it past this panel is the ceiling: 10k documents is ~32,000 SVG elements against 4,523.

Two of my own hypotheses were wrong: the SVG variants came out **identical** (the cost is
compositing elements, not the 1,420 per-frame attribute writes), and canvas hit-testing beat
`elementFromPoint` by **4–9×**.

## 2 · OQ-18 resolved — the mechanism reaches the ceiling, the gate does not

| arm | r@5 | RU-only |
|---|---|---|
| dense | 61% | 56% |
| **rrf_w2** | **68%** | — |
| dense_xl — *human translation, the ceiling* | 66% | 64% |
| **dense_mt — *0.8b translation, the mechanism*** | **66%** | **64%** |
| rrf_mt | 58% | — |

`dense_mt` and `dense_xl` are **identical**. An 0.8b model's translation is as good as a human's for
retrieval, so **there is no headroom left in a better translator** — a closed direction.
`Settings.translate_queries` stays `True` on evidence: 61% → 66%, and 56% → 64% on Russian.

**The 80% gate is still missed at 68%.** Phase 5's recall criterion remains unmet, said plainly
rather than moved.

**Two cheap follow-ups, both seconds because the forward pass is cached:** the winning arm
(`rrf_w2`, no translation) has never been composed with translation — there is no `rrf_w2_mt`; and
`gated`, which exists to be language-aware fusion, scores identically to plain `rrf`, so **the gate
is not doing its job**. BM25 scores **0.00** on cross-language queries and dilutes a good dense
ranking, which is why naive `rrf_mt` (58%) is worse than not translating at all.

`en-relay-dockerfile` misses in all eight arms because `Dockerfile.relay` is `CONFIG` (never
embedded) *and* because a filename is not searchable at all — `rel_path` is `UNINDEXED`. Kept as a
true negative; the generalisation is **[OQ-26](OPEN_QUESTIONS.md#oq-26)**.

## 3 · Phase 11's knowledge graph, built in full

`rag/graph.py` + two endpoints + `KnowledgeGraph.tsx` on **Ctrl+5**, against the real corpus:
**1,565 documents · 988 wikilinks · 2,480 inferred edges · 195 orphans**. All of §11b now exists —
the map, retrieval traces, collection hulls, the labelled legend, and select-as-context.

Select-as-context fills **band 6**, which was empty because *search* on the answer path costs
seconds. A pin has no query, so the argument does not apply. Provenance rides on each pinned
document: choosing a file by hand does not launder its taint.

**Defects only real use found** — none of which the suite could see, because none of those tests lay
anything out or run a browser: the canvas sized itself **166 × 11280**; a horizontal scrollbar sat
under a pannable map; `unplaced` counted 106 documents that can *never* be placed, producing a
permanent banner offering an action that could not change the number; list rows rendered as `"B…"`;
and the pan handler dereferenced a ref inside an async state updater, blanking the whole stage on
mouse-release.

## 4 · ORACLE could not retrieve from a chat turn — fixed

The largest single finding of the session, and it was not a model-quality problem.

`router/selection.py` filters candidates through `ARG_BUILDERS`, and **no `know.*` tool was in it**.
For intent `search` the router was offered **exactly one tool, `fs.list`** — and ADR-0017 builds the
enum from the candidates, so the decoder could not spell `know.search`. The whole retrieval stack
was unreachable from chat; its only callers were the global-search endpoint and MCP delegates.

Fixed with a `"query"` shape that needs no project. `eval_selection.py` re-run as the discipline
requires — **25/25**, up from 20/20, with five new cases. Verified end to end: a live turn now emits
`tool.started know.search` → `tool.finished`, and **the knowledge map's retrieval trace lit up for
the first time**.

⚠ **What that immediately revealed:** the top hits for *"taint tracking"* were ML notes about
*experiment tracking* and a `budget.py`, not `SECURITY.md §6`. The plumbing is right; the ranking is
the 68% OQ-18 measured, now visible on a real query.

## 5 · Things found in passing, all fixed

- **ADR-0023 spent two weeks contradicting its own measurement** ("semantic edges default off",
  disproved 2026-08-26).
- **`check.py` crashed while *printing* a failing step** — the gate died exactly when it had
  something to say. `eval_selection.py --verbose` carried the identical defect on its Russian cases.
- **A security test raced its own startup**, launching a process that exits in milliseconds and then
  asserting it was alive — ~1 run in 3, blaming the Job Object.
- **A retrieval fixture pointed at a moved file**, an automatic miss for every arm.
- **Vite's watcher killed the dev server** by opening the Rust binary mid-link.

## 6 · One retraction

I diagnosed an OQ-18 run as a thread-pool deadlock — 0% CPU samples, 71 threads in `Wait`, a py-spy
stack inside `session.run()`. **All real, and the conclusion was wrong.** It was running at 0.20
chunks/s because two full `check.py` runs were executing in the same window; each batch took ~81 s,
so a stack sample landed inside `run()` essentially always. **The starvation was mine** — I had
written "don't run the test suite while it runs" into the ledger hours earlier.

Kept in the script rather than deleted: *a stack in native code proves where a thread is, not that
it is stuck*, and `Win32_Processor LoadPercentage` is a stale counter that was trusted over the
eval's own instrumentation.

## Gate

All seven steps green. The wall-clock flake family now spans **three** tests
(`test_a_long_burst_arrives_complete`, `test_a_burst_becomes_one_group`, and a WebSocket timeout);
a fourth — the app-detachment race — turned out to be a real defect and is fixed. The rest still
carry implicit timing assumptions and should become explicit perf tests.
