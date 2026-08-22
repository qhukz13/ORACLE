# 2026-08-22 — the embedding cache, and what it does to the indexing budget

P5-T2 requirement 1. It was sequenced first because it changes the cost of everything
after it: tree-sitter chunking is next, and without this, every experiment with chunk
boundaries costs a 43-minute re-embed.

**Result: a full rebuild from an empty database went from 42.8 minutes to 37 seconds.**
Recall is unchanged at 81% with the same four misses, which is the check that matters —
a cache that changed the answers would be a bug wearing a speedup's clothes.

## The problem, stated precisely

`chunk_id` is `sha256(path + ordinal + text)`. That is the right key for a *chunk* — it
is what lets an edit at the top of a file leave the chunks below it valid ([RAG.md
§6](../../docs/RAG.md#6-incremental-indexing)). But it means a change to *chunking*
invalidates every id in the corpus, including for text that did not move a byte.

An embedding does not depend on where its text lives. It depends on `(model, text)`. So
the cache is keyed on `sha256(text)` alone, and the omission of the path and the ordinal
*is* the design.

## Why it is a separate file from `knowledge.db`

`D:/ORACLE/data/embeddings-multilingual-e5-base-768.db`, not a table in the index.

The index is disposable by design (ADR-0006) and the project leans on that: "delete it and
rebuild" is the answer to a corrupt index, a bad chunking change, a schema change. But the
expensive part of rebuilding was never the index — it is the forward passes. Putting the
cache inside the thing you are told to delete would mean the disposability promise costs
an hour every time it is used.

Three consequences, all of them wanted:

* Deleting `knowledge.db` now costs a walk and a re-chunk. **37 seconds, measured.**
* `knowledge.db`'s schema does not change, so nothing already built needs rebuilding to
  gain this. (The alternative — a `text_hash` column on `chunks` — would have bumped
  `_SCHEMA_VERSION`, and since that file has no migration runner on purpose, it would have
  forced exactly the 43-minute rebuild this exists to avoid.)
* The cache is independently disposable. A miss costs time and never correctness, which is
  why a model mismatch **resets** it where the index **refuses**: a stale cache entry is
  recomputable, a wrong vector in the index is silently wrong answers.

## Measured

Same corpus, same machine, same code path (`scripts/index_knowledge.py --full`):

| | documents | chunks | embedded | from cache | wall clock |
|---|---:|---:|---:|---:|---:|
| Cold — no cache at all | 1,330 | 10,287 | 9,385 | 0 | **42.8 min** |
| Warm, after a session's edits | 1,359 | 10,526 | 254 | 9,364 (97%) | **2.6 min** |
| Warm, nothing changed | 1,359 | 10,526 | **0** | 9,618 (100%) | **37 s** |

Cache file: 40 MB for 9,637 entries. Retrieval after the warm rebuild: recall@5 **81%**,
crosslang 62%, p95 284 ms — identical to the cold build, which is the point.

**The acceptance criterion was "re-chunking unchanged text costs zero embedding calls."**
Zero, measured, and asserted in `tests/test_rag_cache.py` by counting forward passes
rather than timing them — a timing test cannot tell 43 minutes from 42.

## Seeding it from the index that already exists

Adding a cache normally means the *next* rebuild is fast and the hour already spent is
thrown away. That is a strange thing to ask of anyone, and it was avoidable: the chunk
text and its vector are both already in `knowledge.db`, and the cache key is a hash of
that text. So `warm_from_index` joins `chunks` to `chunk_vectors` and fills the cache in
seconds.

```
seeded 9385 vectors -> 9383 entries
```

The two-row difference is not a bug: two chunks in the corpus have byte-identical text, and
the cache stores one vector for both. That deduplication is the same property that makes it
work at all.

## What this does to the indexing budget — OQ-17 needs rewriting again

[OQ-02 §5](2026-08-22-oq02-embeddings.md) struck the Phase 5 criterion *"full index in
< 10 min"* as unachievable, measured at 42.8 minutes. That was true of the case measured.
It is **not** true of the case that actually happens:

| what happened | rebuild cost |
|---|---|
| First ever build, or a change of embedding model | **~43 min** — unavoidable, every vector is new |
| Chunking changed (tree-sitter, and every experiment after) | **~40 s** |
| Corrupt index, deleted and rebuilt | **~37 s** |
| Nothing changed | 1.4–4.4 s (incremental path, untouched) |

So the honest budget is not one number and not two, but a distinction between a **cold
cache** and a **warm** one. Only the first row is an hour, and it happens once per model.
Every other rebuild — including the disposability path the whole design rests on — is
under a minute, which is *inside* the original 10-minute target that was struck as
impossible.

`bge-m3` re-enters the argument on these terms too. Its ~2.5 h is a **one-time** cost, paid
once, after which its rebuilds are as cheap as e5-base's. That does not settle
[OQ-02](../../docs/OPEN_QUESTIONS.md#oq-02) — the recall question is still open and still
needs more Russian fixtures — but it removes the indexing cost as the reason not to try it.

## What is still true

The cache does not help the **first** build of a new model, does not shrink the corpus, and
does not touch retrieval latency. It converts "changing the chunker is expensive" into
"changing the chunker is free", which is the only thing it was built to do — and the next
task changes the chunker.
