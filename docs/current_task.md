# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P11-T1 — OQ-22: measure the knowledge graph before drawing it**

**Phase:** [11 — execution visualisation & advanced UI](ROADMAP.md#phase-11--execution-visualisation--advanced-ui--capability-arc) · **Scope:** Capability arc
**Status:** `IN PROGRESS` · **Set:** 2026-08-26
**Previous:** Phase 10 done; P9-T3 half done and waiting on a scheduled job (see "Carried over").
Planning evidence: [`2026-08-26-p11-graph-data.md`](../logs/development/2026-08-26-p11-graph-data.md).

---

## Why this task, and why it is first

Sequencing rule 6: *no phase builds on a vendor behaviour — or a corpus property — that has not been
recorded.* [OQ-22](OPEN_QUESTIONS.md#oq-22) is Phase 11's opening obligation, and two probes taken
while planning have already moved what it needs to measure.

**Finding 1 — the link graph is one collection's.** 1,176 links, all `wikilink`, and 157 of the 166
`notes` documents carry one against **2 of 1,248** `projects` documents. **Zero cross-collection
edges**, and there cannot be any: `links` is populated only from `Chunk.links`, set only by
`chunk_markdown(..., obsidian=True)`, and `obsidian: true` is on exactly one collection.

So UI.md §11b's *"every indexed document across the Obsidian vaults, project docs and PDFs"* is, on
this corpus, a 166-node vault map with 1,248 dots around it. **That inverts what the design treats
as optional**: §11b and OQ-22 both say semantic edges may ship off and *"the explicit link graph
alone may be the honest product"*. For 88% of the corpus semantic edges are not an enhancement —
they are the only thing that makes those nodes part of a graph at all.

**Finding 2 — the cost is I/O, not maths.** Mean-pooling chunk vectors to document vectors and
running a full 1,320×1,320 cosine kNN costs **0.24 s**. Reading the 13,708 embeddings out of `vec0`
to do it costs **45.4 s**. The incremental-index budget is `< 5 s` for one file (TESTING.md §6) and
the observed pass is 1.4–4.4 s, so a 45 s read per save is not absorbable. **The design needs a
document-vector cache, or measurement 1 fails on I/O and the failure gets misattributed to layout.**

**Finding 3 — the threshold is not the knob.** On `bge-m3`, thresholds 0.5 and 0.6 produce
*identical* edge sets; at k=6/0.5 the semantic graph connects 99.9% of documents at average degree
9 — the quantified hairball. Sweep **k first**, threshold second.

## Requirements

1. **`scripts/measure_graph.py`**, shaped like `scripts/index_knowledge.py` (same bootstrap, same
   coloured report, same "a measurement script as much as a build one" framing). Reads a **copy or
   read-only URI** of the live `knowledge.db` and writes nothing to it. Emits
   `logs/measurements/oq22-graph.{json,txt}`.
2. **Measurement 3 first, not last.** The edge model decides the node count, and the node count is
   what the rendering question is asked at. Measuring canvas-vs-SVG at 166 nodes and concluding
   "SVG is fine" would be measuring the wrong graph. Sweep `k` over {2,3,4,6,8} and `thr` over
   {0.80, 0.85, 0.90, 0.95} plus an explicit-edges-only control, and report per configuration:
   * **shape** — cluster modularity, and **cross-collection edge count** (explicit-only baseline: 0);
   * **neglect** — orphan count, with the **94 unembeddable `config` documents excluded** as a
     distinct state. They are edge-less *by policy* (RAG.md §2 never embeds config), and counting
     them as orphans gives question 2 a 94-item false-positive floor. `parse_error` is 0, so §11b's
     "failed to index appear hollow" is the wrong bucket for them and the spec needs a third state:
     *indexed, not embeddable*.
   * **reach** — 2-hop neighbourhood size distribution. A configuration whose median 2-hop is most
     of the corpus has answered "everything", not "reach".
3. **Measurement 1 — layout cost**, cold and incremental, at N=1,414 and a synthetic N=10,000.
   Specify the **document-vector cache** the 45 s read forces. Gates: incremental placement
   **≤ 250 ms/document** (the free headroom under TESTING.md's `< 5 s` is ~0.6 s at worst);
   full re-layout **≤ 10 min** at 1.4k and **≤ 30 min** at 10k; peak RSS **≤ 500 MB**.
4. **Measurement 2 — canvas vs SVG**, at the node count measurement 3 implies, in a throwaway
   harness fed the frozen positions. **Inside the Tauri shell, not just Chrome** — WebView2 is what
   ships. Gates: p95 frame ≤ 16.7 ms, idle CPU < 5% focused and 0% hidden, first paint < 1 s.
   The SVG control run is **mandatory**: OQ-22 says if SVG survives, ADR-0023's canvas complexity is
   unjustified — and then ADR-0023 gets amended, not quietly ignored.
5. **Measurement 4 — reframed, because it is unanswerable as written.** OQ-22 asks "does the map
   stay recognisable after a week of real edits". Substitute a **holdout**: lay out the corpus minus
   a random 5%, place the holdout incrementally at neighbour centroids, then re-layout everything and
   report **neighbour-set Jaccard @ k=10** plus the Procrustes-aligned displacement of pre-existing
   nodes (which must be exactly 0 — incremental placement moves nothing else, per ADR-0023). Repeat
   at 10% and 20%. **Gate: Jaccard@10 ≥ 0.7 at 5%.** Amend OQ-22 to say so rather than silently
   substituting.
6. **State the fail condition before running it.** If no (k, thr) gives median 2-hop < ~10% of the
   corpus **and** > 0 cross-collection edges **and** orphan count meaningfully below the
   explicit-only baseline, then semantic edges ship off — and at that point the honest outcome is to
   **cut the knowledge-graph view** and write the ADR, exactly as §11b already permits (*"If, after
   real use, it answers none of these better than search does, it gets cut and an ADR records
   that"*). The list view over `documents` + backlinks is then the deliverable.

## Constraints

- **`C:/Projects` is an indexed collection root.** Editing a tracked file changes the corpus. Take
  the census once, cache it, and cite its hash in the dev log.
- Do not write to the live `knowledge.db`.
- **Do not add a `positions_version` with fill-in-if-missing semantics.** `bind()` already does that
  for `chunker_version` and it is why the live index is stamped v2 with 57% v1 rows (below).
- No graph library. ADR-0013 permits pure-maths helpers (`d3-scale`, `d3-shape`) and nothing that
  owns the DOM.

## Acceptance criteria

- [ ] `scripts/measure_graph.py` exists and is reproducible from a cold checkout.
- [ ] All four measurements recorded in `logs/measurements/oq22-graph.{json,txt}` and argued in a dev log.
- [ ] The edge model is **decided**, with the k/threshold sweep as its evidence.
- [ ] Canvas-vs-SVG decided at the implied node count; ADR-0023 confirmed or amended.
- [ ] OQ-22 resolved or explicitly re-gated, its `EXPERIMENT NEEDED` marker deleted, and its
      measurement 4 amended to the holdout form.
- [ ] Numbers folded into TESTING.md §6's performance table.
- [ ] `make check` green.

## Then, in order

| Task | Delivers | Depends on |
|---|---|---|
| **P11-T2** | **OQ-14 go/no-go.** Minimum `OrbitView` (SVG, <40 nodes, deterministic polar, ~250 LOC), a **label-cover vitest** (render with every `<text>` removed; the node needing attention must still be uniquely identifiable), and the idle-CPU budget. Then **ship it or delete it and write ADR-0024.** Placed second, not last, precisely because it can *remove* scope. The honest bar: the command-bar state pill already answers two of §3's four questions at zero cost — **the orbit must beat the pill, not beat nothing.** | — |
| **P11-T3** | **Execution tree, backend half.** `task.*` payloads gain `depends_on`, `objective`, `role`, `agent`, `attempt`, timings and **`cost`** (`TaskResult.cost` exists and is projected nowhere); `/api/v1/tasks` reconcile-on-connect in the client, which is what `service.tree()`'s own docstring says it is for. | P8 |
| **P11-T4** | **Execution tree, view half.** `graph/rank.ts` (longest-path `dagColumns`, pure, property-tested), root/plan row with elapsed+cost, retry-attempt rows, **evidence as links**, `role="tree"` + roving tabindex, the `tt-*` CSS block, graph-to-delegation cross-link. **Plus the seven missing axe cases** — see Accessibility. | T3 |
| **P11-T5** | Centre stage becomes switchable (`Ctrl+1..4`), a `TaskInspector` branch, and **`KnowledgeHealth.tsx` finally mounted** — it is built, tested, and imported by nothing, and ADR-0023 puts re-layout on it. | T4, T2 |
| **P11-T6/T7/T8** | Knowledge graph data layer, view, and trace mode — **only if T1 passes.** | T1 |
| **P11-T9/T10** | Timeline + agent queue; global search + notifications. Search needs a **new backend** (`GET /api/v1/search`, six sources, p95 < 300 ms) and is the first thing cut. | T5 |

**Cut order, first to last:** T10, T8, T9, T7 (keeping the list view and `/api/v1/graph`), T2.
**Never cut:** T1 (the phase's opening obligation) and T3/T4 (the only part that visualises the
supervisor arc the last four phases built).

## Accessibility — structured so it cannot be an afterthought

Three choices make "the list view offers every graph action" true by construction:

1. **One action registry, two renderers.** `graph/actions.ts` defines the actions once; canvas and
   list both render from it, and a vitest asserts the two reachable sets are **equal**. Adding a
   canvas action without a list action fails the build.
2. **`GraphListView` is built in T7, not bolted on after**, sharing the canvas's filter state.
3. **`a11y.test.tsx` grows in T4, before the new surfaces land.** It covers 4 of 12 components
   today; `TaskTree`, `Inspector`, `DelegationPanel`, `MemoryView`, `GraphCard`, `PipelineCard` and
   `KnowledgeHealth` have no case.

**And one honesty caveat to fix rather than inherit:** `a11y.test.tsx` disables `color-contrast`
(happy-dom has no layout engine), so UI.md §14's *"amber on dark is the risky one and must be
verified, not assumed"* **has never been verified**. `--st-wait: #f59e0b` on `--bg-1: #11151f` needs
a pure-function contrast test over the token values — no DOM, so no layout engine needed.

---

## Carried over, not forgotten

- **P9-T3b — the scheduled OQ-18 corpus run.** Windows task `ORACLE-OQ18-eval` fires **2026-08-27
  07:12** (~3 h) and writes `logs/measurements/oq18-translated.{txt,json}`. On collection: compose
  `dense_mt` against `dense_xl` for the shipped path, confirm or flip `Settings.translate_queries`,
  decide `en-relay-dockerfile`, resolve OQ-18, and state the answer-key correction wherever
  pre-2026-08-26 recall numbers are quoted. Then remove the task with `Unregister-ScheduledTask`.
- **The `chunker_version` guard does not fire on the indexes it was written for.** `bind()` raises
  only when a key is *present and different*, then writes the current value in — so an index built
  before the key existed passes and is stamped by whatever binds it first. Already happened to the
  live index: **57% of its 14,586 rows exceed the shipped 1200-char cap, longest 4,055** — the v1
  signature. Decide whether a missing version should refuse; the database wants a reindex either way.
- **Phase 11's other half has no data.** The `tasks` table is **empty** — 0 rows, 0 roots, 0
  superseded — and the event log holds 302 events over 5 days. The execution tree, orbit, timeline
  and queue all render supervisor activity that has never happened outside a test. ROADMAP defends
  scheduling OQ-14 late by citing "months of real event data"; there are none.
  **The cheapest unblock is `oracle-selfcheck`** — local, no egress, six steps, one approval card,
  ~5 minutes — which produces a real six-task graph with real evidence. The Phase 8 scenario is
  richer (it is the only thing that exercises `supersedes` lineage) but costs tokens and egress.
  **Both are T2-or-above and are a person's to run, not an agent's.** T3/T4 can be built against
  fixtures; their acceptance criteria cannot be judged without one real run.
- **A merge-gate test that fails under CPU starvation.**
  `test_a_long_burst_arrives_complete` lost 189 lines of a ConPTY burst twice on 2026-08-26 under
  full load, and at `HEAD` too. Idle it passes in 6 s. If it reappears, decide whether the reader
  drops output under starvation or the deadline is too tight — different repairs.
- **`make perf` and `make eval` are documented in TESTING.md §8 and defined nowhere.** Phase 11's
  acceptance depends on budgets being assertable, so add the targets or correct the doc.
- **`TaskTree.test.tsx` is green on a fixture the app cannot produce.** `store.ts` never populates
  `dependsOn`, so `TaskTree`'s `after {deps}` line is dead in the running app. Fixtures should be
  recorded from the wire, not hand-written.
- **A memory friction**: a correction typed while a graph runs is refused, because "never mid-plan"
  is implemented literally. The fix, when somebody hits it, is a queue — not an exception.
- **Band 6 is not on the interactive answer path.** P9-T2 measured why. Revisit only with a number.
- **Scheduled pipeline runs** are post-MVP; PIPELINES.md §5's "nothing above T1 unattended" is not
  enforced because nothing schedules anything. The hook exists: `check(..., max_tier=Tier.T1)`.
