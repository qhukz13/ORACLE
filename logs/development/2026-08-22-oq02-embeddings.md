# 2026-08-22 — OQ-02: which embedding model for a mixed RU/EN corpus?

Resolves [OQ-02](../../docs/OPEN_QUESTIONS.md#oq-02), which blocked Phase 5.

**Answer: `multilingual-e5-base` at the full 768 dimensions.** Not truncated and not
quantised — both of those were expected to be cheap and are not, by wide margins (§2, §3).

`bge-m3` is the honest complication. It **beat** e5-base on every quality measure,
including 100% against 75% on the Russian questions, and it costs 2.6x the indexing time
and 2x the resident memory to do it. The overall gap is one fixture out of 21, which this
fixture set cannot resolve. e5-base ships; bge-m3 is a one-line switch, and §4 says
exactly what measurement would justify flipping it.

The most consequential result is not about the model at all: **the 10-minute full-index
budget in the Phase 5 acceptance criteria is not achievable on this CPU** at any quality
that passes the recall gate. That is recorded in §5.

## Setup

```
CPU        Xeon E5-2670 v3 · 12 cores / 24 threads · 2.3 GHz · Haswell-EP
           AVX2, no AVX-512, no VNNI  ← this matters, see §3
Baseline   422 GFLOPS SGEMM (numpy, 2048³ fp32) — the machine is healthy and idle
Runtime    onnxruntime 1.29.0, CPUExecutionProvider, intra_op=24, inter_op=1
Tokenizer  tokenizers 0.23.1, max_len 512, padding to the longest in batch
Corpus     config/collections.yaml, walked for real: 1,339 documents → 11,268 chunks
Fixtures   tests/fixtures/retrieval/cases.yaml — 21 questions, 8 of them Russian
Harness    scripts/eval_embeddings.py
```

**Method.** Each candidate embedded an identical 3,000-chunk sample of the real corpus
(every chunk of every fixture-answer document, plus a deterministic random sample of the
rest) and answered all 21 fixtures. Chunk boundaries are computed in *characters*, so
every candidate saw byte-identical chunks — otherwise this would be measuring the
tokenizer and the model at once. Truncation variants reuse their parent's forward pass.

**Sampling caveat, stated up front.** 3,000 distractors instead of 10,346 inflates every
candidate's absolute recall equally. The comparison is what this run is for; the absolute
gate number comes from the full-corpus run in §6.

## 1. Results

| candidate | dim | dense r@5 | hybrid r@5 | **RU→EN r@5** | chunks/s | model | index |
|---|---:|---:|---:|---:|---:|---:|---:|
| BM25 only | — | — | 62% | **0%** | — | — | — |
| e5-small | 384 | 76% | 71% | 38% | 7.95 | 470 MB | 4 MB |
| **e5-base** | **768** | 81% | **90%** | 75% | **4.71** | 1110 MB | 8 MB |
| e5-base → 384 | 384 | 71% | 81% | 62% | 4.71 | 1110 MB | 4 MB |
| e5-base int8 | 768 | 52% | 76% | 62% | 4.59 | 279 MB | 8 MB |
| **bge-m3** | 1024 | **90%** | **95%** | **100%** | **1.37** | 2268 MB | 11 MB |
| bge-m3 → 512 | 512 | 90% | 86% | 75% | 1.37 | 2268 MB | 6 MB |

**On the throughput column — a correction to an earlier version of this log.** The
`chunks/s` figures first recorded here were taken *during* the benchmark run, while other
work was competing for the same 24 threads, and the contention was not constant: e5-base
was measured at 2.7 under heavy load and bge-m3 at 1.0. Those numbers were wrong in a way
that flattered the comparison — the ratio looked like 2.6x when it is **3.4x**. The column
above is now from a dedicated back-to-back run on an idle machine. The recall columns are
unaffected: embedding is deterministic, and load cannot change what a model retrieves.

The `RU→EN` column is the eight Russian questions against the English codebase — the case
this experiment was created for, and the one RAG.md §8 says regresses silently.

**BM25 alone scores 0% on it.** Eight for eight. That is the clearest single number here:
lexical search is not partially weak on cross-language retrieval, it is blind to it, and
no amount of fusion tuning changes that. It is the entire justification for the dense
half of the index.

## 2. Matryoshka truncation to 384d costs 9 points, not "little"

TECH_STACK said truncation "usually [costs] minimal". On this corpus it costs **9 points
of hybrid recall** (90% → 81%) and 10 points of dense recall (81% → 71%), to save 4 MB.

The reason is worth keeping: `multilingual-e5-base` is **not Matryoshka-trained**.
Matryoshka representation learning has to be an explicit training objective, and E5 did
not have one — so the first 384 dimensions are not a self-sufficient embedding, they are
half of one. The general advice about cheap truncation comes from models trained for it.

**Do not truncate.** 8 MB of vectors is not a budget worth managing.

## 3. The int8 build is slower *and* much worse, and the CPU is why

`model_qint8_avx512_vnni.onnx` gained **13% throughput** (2.7 → 3.5 chunks/s) and lost
**29 points of dense recall** (81% → 52%).

The throughput half was predictable from the filename: the export targets AVX-512 VNNI,
and this is a Haswell CPU with neither. ONNX Runtime falls back to AVX2 int8 kernels, so
the arithmetic is cheaper but nothing like the 2–4x the quantisation is meant to buy.

The quality half was not predictable and is the real finding. A 29-point collapse is not
"some quantisation loss" — dense retrieval fell below BM25 alone on this fixture set.
Whatever calibration that export used, it does not survive contact with this corpus.

**Do not use the quantised export.** If throughput ever becomes the binding constraint,
quantise from the fp32 weights against this fixture set and re-measure — do not assume a
published int8 artefact preserves retrieval quality.

## 4. bge-m3 is better, and the fixture set is too small to say how much better

This is the result that did not go the way the default assumed. `bge-m3` beats `e5-base`
on every quality measure: 95% vs 90% overall, 90% vs 81% dense-only, and **100% vs 75%
on the Russian questions** — the exact case OQ-02 exists to decide.

It also costs, on this machine:

| | e5-base | bge-m3 |
|---|---:|---:|
| Full rebuild, end to end | **42.8 min** (measured) | **~2.5 h** (extrapolated) |
| Throughput, idle machine | 4.71 chunks/s | 1.37 chunks/s |
| Model on disk | 1.1 GB | **2.2 GB** |
| Resident while serving queries | ~1.5 GB | **~3 GB** |
| Query embed, p95 | 21 ms | ~100 ms |

**Both are inside the gates.** 144 ms is comfortably under the 400 ms retrieval budget,
and 32 GB of RAM absorbs 3 GB.

**The honest reading of the numbers.** Overall, 95% vs 90% is **one fixture** out of 21.
That is noise. The cross-language column is 8/8 vs 6/8 — two fixtures out of eight, which
is more meaningful and sits exactly where this project cares most, but is still n=8. This
fixture set is a *gate*, not an oracle, and it cannot separate these two models
decisively.

**Decision: ship `e5-base`; `bge-m3` is a one-line switch and a rebuild.** Both are
defined in `rag/embedding.py` as `ModelSpec`s, the index records which one built it, and
opening it with the other refuses rather than returning nonsense. The reasoning:

* The measurable gap is within the resolution of the instrument.
* The costs are not: 2.6x indexing time and 2x resident memory are certain.
* The index is disposable, so this is a reversible decision — which is exactly the kind
  that should be made cheaply and revisited with evidence.

**What would change it**, and it is cheap now that the harness exists: expand the Russian
fixtures from 8 to ~25 and re-run. If `e5-base` still misses a quarter of them, the
cross-language gap is real and worth 2.6 hours of rebuild. That is the next measurement,
not a guess to be argued about.

## 5. The 10-minute full-index budget is not achievable ← the important one

The Phase 5 acceptance criteria say *"Full index of all projects + vaults in < 10 min on
this CPU"*. Measured:

| candidate | chunks/s (idle) | 9,385 chunks |
|---|---:|---:|
| e5-small | 7.95 | ~20 min |
| **e5-base** | **4.71** | **~33 min** (measured end to end: **42.8 min**) |
| e5-base int8 | 4.59 | ~34 min |
| bge-m3 | 1.37 | ~1.9 h (end to end: ~2.5 h) |

**The measured full build is 42.8 minutes** — 4x the budget, and the pure-embedding
estimate of 33 min understates it because a real build also walks, hashes, chunks and
writes. Nothing that passes the recall gate comes close to ten minutes. The corpus is
~3.7M tokens; ten minutes would require ~6,200 tokens/s, and e5-base sustains ~1,900 on
24 Haswell threads.

The budget was written before anything was measured, and it was wrong. **The criterion
should be rewritten**, and the honest version is two numbers rather than one:

* **Full rebuild: ~1 hour, background, rare.** It happens on a model change or a corrupt
  index, and it is exactly the case where the index being disposable is the point.
* **Incremental update: < 5 s.** Measured at **4.4 s** for a no-change pass over all
  1,330 documents (walk + hash, no embedding), which is the operation that actually
  happens dozens of times a day.

This makes the incremental path load-bearing rather than a convenience, which is a
different engineering posture than "a rebuild is cheap, don't bother being clever".

## 6. Full-corpus confirmation — and the sample was flattering everyone

The whole corpus, built by the shipped code path (`scripts/index_knowledge.py --full
--measure`), not by the benchmark harness:

```
1,330 documents · 10,287 chunks · 9,385 embedded · 85 MB · 42.8 min
recall@5   81%          gate 80%     PASS, by one point
  crosslang   62%  (5/8)   ← the case this experiment exists for
  semantic    90%  (9/10)
  lexical    100%  (3/3)
latency    p50 149 ms · p95 203 ms   gate p95 < 400 ms   PASS
misses: ru-two-users-one-agent · ru-feature-entitlement · ru-finetuning · en-relay-dockerfile
```

**The 3,000-chunk sample inflated recall by 9 points** — 90% there against 81% here — and
the sampling caveat at the top of this log said it would. What the caveat did *not*
anticipate is where the loss landed: **the cross-language column fell from 75% to 62%.**
On the real corpus, `e5-base` misses three of the eight Russian questions.

This weakens the §4 decision, and it should be said plainly rather than buried:

* The overall margin over the gate is **one fixture**. 81% against 80% is not comfortable.
* The failure is concentrated exactly where RAG.md §8 warned it would be, and where this
  user actually works: Russian question, English codebase.
* On the sample, `bge-m3` scored 100% on that column against e5-base's 75%. It will also
  fall on the full corpus — but it started 38 points higher.

**The decisive measurement has not been run.** `bge-m3` over the full corpus is ~2.5 hours
of CPU. Until it exists, "e5-base is good enough" rests on a sample that has now been shown
to overstate by 9 points. The recommendation in §4 stands as the *default*, not as a
finding — and the case for spending those 2.5 hours is stronger after this run than before.

### One thing that got fixed by measuring it

Retrieval p95 was **348 ms** on first measurement — passing the 400 ms gate with 13% to
spare, which is not passing so much as not yet failing. Profiling the pipeline:

| stage | p50 |
|---|---:|
| embed the query | 19 ms |
| fusion gate (`discriminating_terms`) | 0.2 ms |
| dense KNN over 9,385 vectors | 79 ms |
| **BM25** | **150 ms** |

The lexical half cost twice the brute-force vector scan. The cause was in the query
builder: it OR-ed *every* word of the question, so `the`, `we` and `is` each dragged in a
posting list matching most of the corpus. Restricting the FTS query to the same
discriminating terms the fusion gate already computes — free, since the document
frequencies were being counted anyway — gives:

```
p50 274 ms -> 149 ms      p95 348 ms -> 203 ms      recall unchanged at 81%
```

Recall being *unchanged* is the point: those terms contributed nothing but latency.

## 7. Things that were wrong along the way

Recorded because they were each invisible failures — nothing raised, the numbers were
just worse or slower.

**Length-sorted batching was a no-op for a whole run.** Padding is to the longest text in
a batch, so grouping similar lengths together is worth 1.8x (4.4 → 8.0 chunks/s on
e5-small). The first implementation measured lengths with the *padded* tokenizer, where
every text reports the same length — so the sort did nothing and cost an extra
tokenisation pass. Fixed with a second, unpadded tokenizer kept solely for measuring.

**`Path.rglob` walked `node_modules` before excluding it.** The first corpus walk did not
finish in two minutes. Exclusion has to happen *during* traversal — prune the directory
name as you descend, never filter the paths afterwards. This is the same rule RAG.md §6
already stated for the watcher; it applies to the walker too.

**A 176 KB single-line JSON blob became a single 176 KB "chunk".** A line-oriented
splitter silently emits whatever it cannot split. There is now a hard character cut for
over-long lines, and a test for it.

**~20% of chunks exceed the 512-token model limit and are silently truncated.** Character
budgets under-control token length on identifier-dense code. This is unresolved and now
carries a `TO VERIFY` in `rag/chunking.py`: measure what the truncation costs before
paying for a token-aware splitter.

**The prefix folklore did not reproduce on a single pair.** "Getting `query:`/`passage:`
wrong halves quality" is repeated everywhere including in our own docs. On one
hand-written query/passage pair, the *wrong* pairing scored higher (0.888 vs 0.877). The
effect is distributional, not per-pair; a unit test asserting otherwise was written,
failed correctly, and was replaced by one that only asserts the prefix reaches the model.

## 8. What this changes in the design

| Document | Change |
|---|---|
| [OPEN_QUESTIONS.md](../../docs/OPEN_QUESTIONS.md) | OQ-02 resolved. OQ-08 resolved alongside it ([log](2026-08-22-oq08-fts5-russian.md)) |
| [TECH_STACK.md](../../docs/TECH_STACK.md) | e5-base confirmed; truncation and int8 rejected *with numbers*; Phase 5 dependency ledger added |
| [RAG.md](../../docs/RAG.md) | corpus corrected to 11,268 chunks (the 30k–80k estimate was 3–8x high); walker pruning rule; `deny` list |
| [DATABASE.md](../../docs/DATABASE.md) | `ident` FTS column; "rebuildable in minutes" corrected to ~an hour |
| [RAG.md §5](../../docs/RAG.md) | fusion is now conditional — see below |

**Fusion is not unconditional, and that is a measured change to RAG.md §5.** RRF was
chosen for having no tuned weights. It is still unweighted, but it is now *gated*:

| | dense only | + BM25 via RRF |
|---|---:|---:|
| e5-base | 81% | **90%** (+9) |
| e5-small | 76% | **71%** (−5) |

On a Russian question against an English corpus, BM25 shares no meaningful term with any
document — and still returns thirty ranked results, which RRF treats as a second opinion
of equal standing. They displace correct dense hits out of the top 5. The fix is not to
tune weights but to admit the lexical list only when the query has some lexical purchase
on the corpus at all: at least one term present in fewer than 10% of chunks. Implemented
as `retrieval.has_lexical_purchase`, tested in `tests/test_rag_retrieval.py`.
