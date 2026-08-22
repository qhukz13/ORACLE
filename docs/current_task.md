# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P5-T2 — Finish project knowledge: tree-sitter, the watcher, and a budget that is true**

**Phase:** [5 — Project knowledge (RAG)](ROADMAP.md#phase-5--project-knowledge-rag--post-mvp) · **Scope:** Post-MVP
**Status:** `NOT STARTED` · **Set:** 2026-08-22
**Previous task:** P5-T1 — **partially done**. The gate is closed and the subsystem is built; see
[current_report.md](current_report.md) for exactly what landed and what did not.

---

## What P5-T1 settled, so it is not re-litigated

- **[OQ-02](OPEN_QUESTIONS.md#oq-02) is resolved**: `multilingual-e5-base`, 768d, no truncation, no
  int8. `bge-m3` measured *better* — and by more than the sample suggested once the full corpus was
  run — but costs **3.4x** the indexing time. It is one `ModelSpec` away. See requirement 6.
- **[OQ-08](OPEN_QUESTIONS.md#oq-08) is resolved**: `unicode61` is fine for Cyrillic. It cannot stem
  (handled by prefix-expanding Cyrillic query terms) and cannot split camelCase (handled by the
  `ident` FTS column).
- **Fusion is conditional.** Unweighted RRF *cost* 5 points on a weaker dense model. The lexical list
  is admitted only when the query has lexical purchase on the corpus ([RAG.md §5](RAG.md#5-hybrid-retrieval)).
- **The schema is written and tested.** `knowledge.db`, sqlite-vec + FTS5, one file, one transaction,
  no migration runner — a disposable index does not need one.
- **Four `know.*` tools ship, not five.** `know.summarize` "uses the local model", and a tool-host
  handler cannot call the LLM layer without L7 re-entering L3–L6. It needs an ADR.

## The one decision this task must open with

**Mostly answered by requirement 1, which is done.** The `< 10 min` criterion was one number for two
very different operations, and separating them is the answer:

| | measured | how often |
|---|---|---|
| Cold rebuild — first build, or a change of embedding model | **42.8 min** | once per model |
| Warm rebuild — chunking changed, or the index was deleted | **37 s** | every other time |
| Incremental, nothing changed | 1.4–4.4 s | dozens of times a day |

Proposed wording, already written into ROADMAP.md and [OQ-17](OPEN_QUESTIONS.md#oq-17) — **confirm or
correct it**:

- Cold rebuild **≤ 60 min**, background, explicitly initiated, with the cost stated before it starts.
- Warm rebuild **< 2 min**.
- Incremental update after one file changes **< 5 s**.

Note this also removes indexing cost as the argument against `bge-m3`: its ~2.5 h is paid once.

If a rebuild has to be faster than that, the levers are in [OQ-17](OPEN_QUESTIONS.md#oq-17) and the
first one is the real fix: cache embeddings by chunk-text hash, so re-chunking only re-embeds chunks
whose text actually changed. That matters because **tree-sitter is in this task**, and changing chunk
boundaries currently invalidates every embedding in the index.

## Objective

Close the four gaps P5-T1 left, in this order — the first one changes the cost of the second.

## Requirements

1. ~~**Embedding cache keyed by chunk-text hash.**~~ **DONE 2026-08-22.** A full rebuild from an
   empty database went from **42.8 min to 37 s**, with zero forward passes and recall unchanged at
   81%. Kept in its own file so deleting `knowledge.db` — the disposability promise — no longer costs
   an hour. `warm_from_index` seeds it from an index already built, so the 43 minutes already spent
   were not thrown away. [Log](../logs/development/2026-08-22-embedding-cache.md).
2. **tree-sitter code chunking**, replacing the regex approximation in `rag/chunking.py`. The current
   limits are measured, not guessed: `equal` (548 occurrences) and `useEffect` (219) are still
   mistaken for declarations because a call taking a callback is indistinguishable from a definition
   to a line matcher. Justify `tree-sitter` in TECH_STACK.md before adding it.
3. **Re-run the fixture set after chunking changes.** Better boundaries lift every model, so the
   OQ-02 comparison may no longer hold. Re-run `scripts/eval_embeddings.py`; if `bge-m3`'s margin
   survives better chunking, revisit the model choice.
4. **Wire the watcher into the daemon.** `Watcher` and `debounce` are built and tested; nothing
   starts them. Needs a lifecycle owner, a HALT path, and an event so the UI can show indexing state.
5. **PDF parsing** via `pypdfium2` — text layer only, no OCR. One 32 MB PDF is currently classified,
   counted and skipped.
6. **Settle `bge-m3` vs `e5-base`.** On the full corpus `e5-base` scores **81%** — one point over the
   gate — and **62% on the Russian questions**, missing three of eight. The sample overstated it by
   9 points overall and 13 on cross-language. Two steps, cheapest first: expand the Russian fixtures
   from 8 to ~25 (n=8 cannot carry this decision), then run `bge-m3` over the full corpus (~2.5 h).

## Constraints

- **Every new dependency gets a line in the TECH_STACK.md Phase 5 ledger**, next to the four that are
  already there. `tree-sitter` and `pypdfium2` are both deferred *in that ledger* pending this task.
- Chunking changes invalidate the index. Requirement 1 exists so that is affordable; do not reorder.
- Retrieved text stays untrusted. `tests/security/test_injection.py` must keep passing unchanged —
  if a chunking change makes it pass for a different reason, that is a regression.
- Tool count is 33 against a cap of 40. `know.summarize` would be 34 and still needs its ADR first.

## Acceptance criteria

- [x] The indexing budget in ROADMAP.md is replaced with the measured numbers — **awaiting your
      confirmation of the wording**.
- [x] Re-chunking a file with unchanged text costs **zero** embedding calls. Asserted by counting
      forward passes in `tests/test_rag_cache.py`, and measured end to end at 37 s.
- [ ] tree-sitter chunks name real symbols: no control-flow keyword and no call expression appears as
      an anchor across the whole corpus. Asserted over the real corpus, not a fixture.
- [ ] Fixture recall@5 **≥ 80%** on the **full** corpus after re-chunking, and no worse than the
      pre-tree-sitter number.
- [ ] The watcher runs under the daemon: a file saved in an indexed project is retrievable within
      10 s, and an `npm install` does not stall the event loop. Measured, not asserted by inspection.
- [ ] ~25 Russian fixtures, and a recorded decision on `bge-m3` vs `e5-base` based on them.
- [ ] The gate green including the security suite.

## Relevant files

Modify: `src/oracle/rag/chunking.py` · `src/oracle/rag/indexer.py` (cache) · `src/oracle/rag/watcher.py`
(lifecycle) · `docs/TECH_STACK.md` (ledger) · `docs/ROADMAP.md` (budget) ·
`tests/fixtures/retrieval/cases.yaml`
Read first: [OQ-02 log §5](../logs/development/2026-08-22-oq02-embeddings.md) ·
[OQ-17](OPEN_QUESTIONS.md#oq-17) · [RAG.md §3](RAG.md#3-chunking)

## Dependencies

P5-T1's subsystem, which is built. Nothing here is blocked by an open question — [OQ-17](OPEN_QUESTIONS.md#oq-17)
is an assumption to be resolved *by* this task, not a gate on starting it.

## Risks

| Risk | Mitigation |
|---|---|
| tree-sitter changes chunk boundaries and silently moves recall | Requirement 3: re-run the fixture set, compare against the recorded number, do not accept "it looks better" |
| A re-chunk costs an hour of CPU every iteration | Requirement 1, first, for exactly this reason |
| tree-sitter grammar packaging on Windows | Verify the wheel before writing code against it — the same rule that caught `pywinpty` in OQ-09 |
| The watcher storms during `npm install` | Already filtered before hashing and debounced at 2 s; requirement 4 says *measure* it rather than trust it |

## Definition of done

All acceptance criteria · the indexing budget corrected everywhere it appears · every new dependency
justified in TECH_STACK.md · the gate green including the security suite · `current_report.md`
overwritten · this file updated to **P6-T1**.
