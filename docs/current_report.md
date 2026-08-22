# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P5-T1 — Project knowledge (RAG)
**Status:** `IN PROGRESS` — OQ-02 and OQ-08 resolved; the subsystem is built, measured against the
real corpus, and the gate is green. One acceptance criterion is **measured as unachievable**, and the
model choice is **less settled than the sample suggested** — both need a human decision.
**Date:** 2026-08-22

---

## What was asked, and what happened

The task had one gate and eight requirements. The gate — [OQ-02](OPEN_QUESTIONS.md#oq-02), the
embedding-model choice — is **resolved and recorded**, which was the thing blocking everything else.
[OQ-08](OPEN_QUESTIONS.md#oq-08) (FTS5 and Russian) was resolved alongside it because it was cheap
and the two answers interact.

The subsystem underneath is built: collection registry, walker, chunker, embedder, `knowledge.db`
with sqlite-vec + FTS5, hybrid retrieval, incremental indexer, watcher, four `know.*` tools with
policy rules, and the UI for citations and index health.

**Two things are not done and one is not possible.** They are listed in "What is left", below, and
neither is hidden in a footnote.

## The measurements that mattered

Full write-ups: [OQ-02](../logs/development/2026-08-22-oq02-embeddings.md) ·
[OQ-08](../logs/development/2026-08-22-oq08-fts5-russian.md).

Recall on a 3,000-chunk sample (identical chunks per candidate); throughput from a dedicated
idle-machine run:

| candidate | dim | dense r@5 | hybrid r@5 | **RU→EN r@5** | chunks/s |
|---|---:|---:|---:|---:|---:|
| BM25 only | — | — | 62% | **0%** | — |
| e5-small | 384 | 76% | 71% | 38% | 7.95 |
| **e5-base** ← ships | **768** | 81% | 90% | 75% | **4.71** |
| e5-base → 384 | 384 | 71% | 81% | 62% | 4.71 |
| e5-base int8 | 768 | 52% | 76% | 62% | 4.59 |
| bge-m3 | 1024 | 90% | 95% | 100% | 1.37 |

**Full corpus, `e5-base`, shipped code path — the number that counts:**

```
1,330 documents · 10,287 chunks · 9,385 embedded · 85 MB · 42.8 min
recall@5   81%  (gate 80%)     crosslang 62% (5/8) · semantic 90% · lexical 100%
latency    p50 149 ms · p95 203 ms  (gate 400 ms)
```

Four results worth carrying forward:

1. **BM25 scores 0% on the Russian questions.** Eight for eight. Lexical search is not weak at
   cross-language retrieval, it is blind to it — which is the whole justification for the dense half.
2. **Matryoshka truncation costs 9 points**, not "minimal". E5 is not Matryoshka-trained. Saving: 4 MB.
3. **The published int8 export loses 29 points** and gains 13% throughput on this CPU, which is
   Haswell — no AVX-512, no VNNI, so its kernels never apply. It fell below BM25 alone.
4. **`bge-m3` is better than what ships** (95% vs 90%; 100% vs 75% on Russian) at **3.4x** the
   indexing time — ~2.5 h against 43 min for a full build. See the next section: the full-corpus
   run weakened the case for the model I chose.

### The recommendation I made, and what the full corpus did to it

I chose `e5-base` on the 3,000-chunk sample, reasoning that its 5-point deficit to `bge-m3` was one
fixture and inside the noise. **The full-corpus run makes that reasoning look thin, and it should be
said rather than left in a table:**

| | sample | full corpus |
|---|---:|---:|
| overall recall@5 | 90% | **81%** (gate: 80%) |
| cross-language (RU→EN) | 75% | **62%** — 3 of 8 missed |

The sample flattered it by 9 points overall and 13 on the cross-language column — and cross-language
is the case this whole experiment exists for, and the one this user works in daily. The margin over
the gate is now **one fixture**.

`bge-m3` started 25 points higher on that column. It will also fall on the full corpus, but nobody
knows by how much, because **the decisive run has not been made** — it is ~2.5 hours of CPU. Until it
exists, "e5-base is good enough" rests on a sample now shown to overstate. The default stands; the
confidence behind it should not be overstated in turn.

### The criterion that cannot be met

> *"Full index of all projects + vaults in **< 10 min** on this CPU"*

**Measured end to end: 42.8 minutes** — 4x over, and nothing that passes the recall gate comes close.
The corpus is ~3.7M tokens; ten minutes would need ~6,200 tokens/s and `e5-base` sustains ~1,900 on
24 Haswell threads. The budget was written before anything was measured.

The incremental path — the one that runs dozens of times a day — is **1.4–4.4 s** for a no-change
pass over all 1,330 documents, against a `< 5 s` target.

**This needs a decision, not a workaround.** The honest rewrite is two numbers instead of one: a rare
background rebuild of ~1 hour, and an incremental update under 5 s. That reframes the incremental
path from a convenience into the product, which is a different engineering posture. Raised as
[OQ-17](OPEN_QUESTIONS.md#oq-17).

## What was built

| Piece | Where |
|---|---|
| Collection registry, deny list, pruning walker | `src/oracle/rag/collections.py`, `config/collections.yaml` |
| Chunking — heading-aware Markdown, symbol-aware code, Obsidian links and tags | `src/oracle/rag/chunking.py` |
| Embeddings — ONNX on CPU, length-sorted batching, required query/passage role | `src/oracle/rag/embedding.py` |
| `knowledge.db` — sqlite-vec + FTS5, one file, one transaction | `src/oracle/rag/store.py` |
| Hybrid retrieval — dense + BM25 + **gated** RRF, boosts, diversity, taint | `src/oracle/rag/retrieval.py` |
| Incremental indexer — content-hash gated | `src/oracle/rag/indexer.py` |
| Watcher — debounced, filters before hashing | `src/oracle/rag/watcher.py` |
| `know.search`, `know.search_code`, `know.read_context`, `know.reindex` | `src/oracle/tools/knowledge.py`, `config/policy.yaml` |
| Citations and index health in the UI | `apps/desktop/src/components/Citations.tsx`, `KnowledgeHealth.tsx` |
| Benchmark and build scripts | `scripts/eval_embeddings.py`, `scripts/index_knowledge.py`, `scripts/fetch_embedding_models.py` |

**The gate is green** — `ruff format · ruff lint · mypy --strict · tsc · pytest · security · vitest`.
**469 Python + 122 TypeScript tests**, including `tests/security/test_collections.py`,
`test_injection.py` and `test_know_tools.py`. Tool count **33**, under the cap of 40.

### Design changes the measurements forced

* **Fusion is now conditional.** Unweighted RRF added 9 points to `e5-base` and *removed 5* from
  `e5-small`. On a Russian question BM25 has nothing to say and says it in thirty ranked results,
  which displace correct dense hits. RRF stays unweighted; its *input* is gated on the query having
  some lexical purchase on the corpus. ([RAG.md §5](RAG.md#5-hybrid-retrieval))
* **`ObsidianNotes` contains a `Passwords/` folder** holding `Passwords.md` and `Bank accounts.md`,
  and it was a declared notes root. There is now a top-level `deny` list, matched on the path before
  a file is opened, that no per-collection include can override.
* **The corpus is 11,268 chunks, not 30k–80k.** The estimate was 3–8x high.
* **`unicode61` handles Cyrillic fine** — the claim in DATABASE.md that it does not was wrong. What
  it cannot do is stem (handled by prefix-expanding Cyrillic query terms) or split camelCase (handled
  by an `ident` column written at index time).
* **`know.summarize` was not built.** TOOLS.md specifies five `know.*` tools and the fifth "uses the
  local model" — which a tool-host handler cannot do without L7 re-entering L3–L6. Four shipped;
  the fifth needs an ADR, not a quiet violation.

## What is left

1. **tree-sitter chunking.** The code chunker is a documented regex approximation. Its limits are
   measured, not guessed: `equal` and `useEffect` are still mistaken for declarations in test files.
   Better boundaries lift every model, so the absolute recall numbers move when this lands.
2. **PDF parsing.** `pypdfium2` is still a deferred dependency; the one 32 MB PDF is classified,
   counted and skipped rather than silently failing.
3. **Wiring the watcher into the daemon.** `Watcher` and `debounce` are built and tested; nothing
   starts them at boot yet.
4. **`bge-m3` over the full corpus** — ~2.5 h of CPU, and the only thing that settles the model
   choice. See above.
5. **More Russian fixtures.** Eight is too few to carry a decision this expensive; ~25 is cheap now
   that the harness exists.

## A flaky terminal test, pre-existing

`tests/security/test_terminal.py::TestNothingIsLostOnTheWayOut::test_a_long_burst_arrives_complete`
failed twice during this task, losing 165 of 300 lines from a ConPTY burst.

**It is not caused by anything here, and that was checked rather than assumed.** Both failures
happened while the `bge-m3` benchmark was saturating all 24 threads with a 3 GB model resident. Re-run
later under lighter load — with every change from this task still in place — it **passes**. Every
change in this task is additive and none of it touches the terminal.

So the finding is load-sensitivity, not a regression: a test that reads 300 lines out of a real PTY
within a fixed deadline fails when the machine is starved. That is a **pre-existing flake worth
fixing** — a timing-sensitive test that only fails under load will eventually fail in CI and be
blamed on whatever landed that day. It should wait on a condition rather than a deadline, the same
lesson [OQ-09](OPEN_QUESTIONS.md#oq-09) already recorded for terminal readiness.

Otherwise the suite is green: **469 Python passed, 1 skipped**; **122 TypeScript passed**.

## Where to pick up

Read [OQ-02](../logs/development/2026-08-22-oq02-embeddings.md) §5 first — the indexing budget is the
open decision, and everything else in this phase is smaller than it.
