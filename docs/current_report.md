# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P9-T2 — retrieval: the hypothesis that is left, and the corpus it is measured against.
**Done: four and a half of six acceptance criteria.** Both of OQ-18's levers are measured. The
gate is **not** met and **not** moved: the best measured configuration is **78.9%** against 80% —
**30 of 38 fixtures, one short**.
**Status:** The chunk budget is enforced and calibrated; recall improved on every strategy;
`make check` green.
**Date:** 2026-08-26

---

## The headline

Two full runs over the real corpus, ~4.3 hours of CPU, identical except for the chunker.
Composed per-case for the path that actually ships:

| | overall recall@5 | RU recall@5 |
|---|---|---|
| before this task | 68.4% | 56.0% |
| **after the chunker repair** | **71.1%** | **60.0%** |
| **+ an English probe** | **78.9%** | **72.0%** |
| the gate | 80% — 31 of 38 | |

| lever | effect on the shipped path |
|---|---|
| 2 · truncation, fixed | +2.7 overall, +4.0 RU |
| 1 · query translation, at its ceiling | +7.8 overall, +12.0 RU |
| 3 · not fusing BM25 on a crosslingual query | already correct in `retrieval.py`, worth 12–20 RU points, and the **harness had it wrong** |

## The correction that matters most

**OQ-18's "44% on the 25 Russian fixtures" was measuring a code path ORACLE does not run.**

`retrieve()`'s `discriminating_terms` drops minority-script terms at any frequency, so a Russian
query returns no lexical terms and takes the **dense-only** path — it never fuses BM25. The eval
harness's `gated` strategy tests only document frequency, with no script rule. So every
crosslingual number this project has recorded described a fusion that does not happen. What ships
scored **56%** on those fixtures before this task, not 44%.

That is the fourth instrument discrepancy in three days, after the chunker copy, the config
denominator and the off-by-one summary header. They share a shape and it is worth naming: **the
measurements were fine and the things around them were not.** The harness now calls the shipped
chunker, prints both denominators, and has a header that lines up with its rows.

## The chunk budget

Two defects, both real, neither about tokenizers on its own:

- **The cap was never enforced.** `MAX_CHARS` was documented as the ceiling on a chunk and applied
  to the *body* — `_pack` counted block bodies while emitting `header + anchor + body`, `_window`
  counted lines while emitting `prefix + lines`, and the overlap path dropped a newline per line.
  The longest "1,800-character" chunk in the corpus was **4,055 characters**. The test that should
  have caught it asserted `<= MAX_CHARS * 2`; a budget with a factor-of-two tolerance is not a
  budget.
- **It was calibrated against the wrong corpus.** "~500 tokens at 3.6 chars/token" — bge-m3
  tokenizes this corpus at 3.05 (code) / 3.33 (markdown) median and 2.34 / 2.42 at the 1st
  percentile. **27.1%** of embedded chunks overflowed the window.

`MAX_CHARS` is 1200 now. Truncation is **0.7%** of embedded chunks and 0.10% of embedded tokens. It
costs 43% more chunks and 20 MB of index — and made indexing *faster* in wall-clock (7,523 s
against 8,032 s), because attention cost is superlinear in sequence length.

**`CHUNKER_VERSION` is recorded in the index** and checked like the embedding model. Incremental
indexing does not rebuild rows it already has, so a boundary change leaves an index half cut each
way with nothing failing. **Your `knowledge.db` will now report "not stale, wrong" and ask for a
reindex** — that is the guard working, not a break.

## The suite hang, fixed at the cause

`make check` hung on me three times. Eleven `async for event in eventlog.stream(0)` loops in tests
had no deadline: when the awaited event never arrives they do not fail, they hang, and with no
global pytest timeout they hang the whole run. P8-T1's report predicted the trigger — *"the helper
should probably move into `helpers_delegation.py` when a fourth suite needs it"*. A fourth suite
needed it. `wait_event(eventlog, match, timeout, what)` exists, `wait_for`/`wait_state` are
one-liners over it, and all eleven are converted: a missing event now fails in 30 s with
`waited 30s for the graph card and it never arrived (last_seq=…)`.

## What is not done, and why

**Query translation is measured but not implemented.** Two things block it and neither is typing:

1. **The translator is unmeasured.** +12.0 RU is what a *human* translation buys — deliberately the
   ceiling, so a negative result would have killed the idea outright. Whether the resident 0.8B
   model's Russian reaches it is the next measurement. **Ollama was not running on this machine**,
   so it could not be answered here.
2. **The latency does not fit the interactive path.** A second dense probe costs one more query
   embedding — 63 ms p50 / 97 ms p95, measured — against ~70 ms of headroom, before the generation
   call. Where it fits is the Handoff Packet, where a delegation takes minutes.

Shipping the mechanism on a ceiling measurement is how a system acquires a feature that works in
the log and not on the machine, so it waits for one cheap measurement.

**OQ-18 is therefore narrowed, not resolved.** The gate stays at 80% — 6.3 points on 38 fixtures is
2.4 cases, and a gate re-argued to sit just below where the numbers landed measures nothing.

One of the eight remaining misses is structural rather than ranking: `en-relay-dockerfile` expects a
**config** file, which is indexed lexically and never embedded. No dense probe of any quality can
retrieve it.

## Also landed

The carried-over `verifier` + `verdict` inconsistency is cleared — the accurate spelling was
rejected while the misleading one was accepted. The shipped template uses the honest spelling.

## Next

**P9-T3** ([current_task.md](current_task.md)): measure the translator and close the last fixture.
Both remaining steps are small and named; the expensive part of this question is done.
