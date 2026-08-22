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

## The decision this task hands back

**Does tree-sitter ship?** It is built, tested, and one constant from being on. The measurement, four
builds, same corpus, same fixtures, same `measure()`:

| | recall@5 | crosslang | semantic | anchors |
|---|---|---|---|---|
| line matcher (**shipping**) | **81%** | 62% (5/8) | 90% | `equal` 548, `useEffect` 219 — both *calls* |
| tree-sitter | 71-76% | 50-62% | 80% | no keyword, no call; real ancestry |

Exactly **two fixtures** separate them, and in both the line matcher wins by accident: it packs
neighbouring text, so a file's header prose lands beside the code it describes and a conceptual
question matches the paragraph. tree-sitter separates those on purpose — right for citing a symbol,
wrong for that question.

Twenty-one cases at 4.8 points each cannot settle a two-case difference in *either* direction. My
recommendation is to leave it off and let requirement 6's expanded fixture set decide, which costs
nothing extra because that work is already scheduled. Say so if you would rather ship the better
anchors now and accept the measured recall — it is one line.

## The one decision this task opened with

**Mostly answered by requirement 1, which is done.** The `< 10 min` criterion was one number for two
very different operations, and separating them is the answer:

| | measured | how often |
|---|---|---|
| Cold rebuild — first build, or a change of embedding model | **42.8 min** | once per model |
| Warm rebuild, index deleted, chunking unchanged | **37 s** (100% cache hit) | every other time |
| Warm rebuild after a chunking change | **2.5 min – 20 min** | when the chunker changes |
| Incremental, nothing changed | 1.4–4.4 s | dozens of times a day |

**The middle row is new and it corrects the proposal below.** Requirement 2 produced four rebuilds,
and a chunking change moves chunk *text*, which is what the cache is keyed on — so the hit rate fell
to 45% on the first tree-sitter build and the rebuild took **19.9 min**, not 37 s. Later builds hit
71%, 95%, 97% as successive changes moved less text. The 37 s figure was real but it was the
best case, and quoting it as "warm rebuild" would have made a budget out of a lucky number.

Proposed wording, already written into ROADMAP.md and [OQ-17](OPEN_QUESTIONS.md#oq-17) — **confirm or
correct it**:

- Cold rebuild **≤ 60 min**, background, explicitly initiated, with the cost stated before it starts.
- Warm rebuild, chunking unchanged **< 2 min**.
- Warm rebuild after a chunking change **≤ 25 min** — a developer-facing number, not a user-facing
  one: it happens when this repo changes `chunking.py`, never on the user's machine.
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
2. ~~**tree-sitter code chunking**~~ **BUILT 2026-08-22, AND OFF.** `chunking.SYNTAX_AWARE = False`.
   It wins the anchor criterion outright — no control-flow keyword and no call expression is an
   anchor anywhere in the corpus, against `equal` (548) and `useEffect` (219) for the line matcher —
   and it *loses* recall@5 by two fixture cases, consistently, across four builds. **This needs your
   call; see "The decision this task hands back".**
   [Log](../logs/development/2026-08-22-treesitter-chunking.md).
3. ~~**Re-run the fixture set after chunking changes.**~~ **DONE 2026-08-22**, and it changed the
   answer to requirement 2. It also found a leak: `cases.yaml` had become indexable when the phase-5
   work was committed, and a file containing all 21 questions verbatim held a top-5 slot in 12 of
   them. `measure()` now discards it before ranking. The `bge-m3` comparison is untouched, because
   the chunker did not change.
4. ~~**Wire the watcher into the daemon.**~~ **DONE 2026-08-22.** `rag/service.py`, spawned through
   `AppState.spawn` so HALT already reaches it and `resume` restarts it; `knowledge.state` events
   drive a status line in the shell. Measuring it found a real defect: the filter ran at 0.27 ms per
   event *on the event loop*, because `fnmatch` normcases both arguments and on Windows that is a
   `LCMapStringEx` syscall. Compiling the patterns once: **3.1x**, and the corpus walker shares it.
   [Log](../logs/development/2026-08-22-watcher-daemon.md).
5. ~~**PDF parsing** via `pypdfium2`~~ **DONE 2026-08-22.** Text layer, no OCR, page anchors
   (`p. 12`), every PDF marked `local_foreign`. Verified on the real files first: 510 pages and 956k
   characters out of the 33 MB textbook in **1.3 s**. Measured against an otherwise identical index:
   adding it changes recall by **nothing at all** — same 55%, same misses — so a 510-page textbook
   does not crowd out the user's own notes. [Log](../logs/development/2026-08-22-pdf.md).
6. **Settle `bge-m3` vs `e5-base`.** **Half done, and the half that is done changed the question.**

   The Russian fixtures are expanded, 8 -> **25** (38 total), ground truth read from the files rather
   than retrieved. On that set `e5-base` scores **36% crosslang, 55% overall** — the eight original
   Russian fixtures had overstated it by **26 points**. The 81% this task opened with was a number
   about a fixture set, not about the corpus.

   Two things were ruled out before blaming the model, both measured:

   - **Fusion is not the lever.** A denominator bug meant Russian stopwords read as discriminating
     (`как` is 0.8% of the corpus and 10% of the Russian in it), so every Russian query pulled in
     GrowAMonster's Russian docs. Fixed — ranks improved, retrieval got faster, and **recall did not
     move**. Turning the lexical half off entirely for Russian would put the *same nine* cases in the
     top 5. [Log](../logs/development/2026-08-22-fusion-denominator.md).
   - **It is not a ranking problem.** Of 25 Russian cases: 9 in the top 5, **0 in ranks 6-10**, 4 in
     11-30, and **12 never enter the candidate set at all**. The empty 6-10 bucket is the shape of the
     result — a nearly-right model misses by a little, and this one either finds the document or does
     not come close.

   **What remains is the `bge-m3` full-corpus run (~2.5 h), and it needs your go-ahead.** It is no
   longer the marginal call it was when this task was written: the only remaining lever is the
   embedding, and `bge-m3` beat `e5-base` on Russian by 25 points on the sample where `e5-base` was
   flattered by 26.

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
- [x] tree-sitter chunks name real symbols: no control-flow keyword and no call expression appears as
      an anchor across the whole corpus. Asserted over the real corpus, not a fixture. **Met.**
- [ ] Fixture recall@5 **≥ 80%** on the **full** corpus after re-chunking, and no worse than the
      pre-tree-sitter number. **Not met — 71-76% against 81%, so the line matcher still ships and the
      shipped path is unchanged.** Four builds, same corpus, same measurement code. Those figures are
      all on the **21-case** set; the chunker comparison predates the expansion and is internally
      consistent. On the 38-case set the same shipped path scores 55%, for the reasons in
      requirement 6 — that is the fixture set changing, not a regression.
- [x] The watcher runs under the daemon: a file saved in an indexed project is retrievable within
      10 s, and an `npm install` does not stall the event loop. Measured in
      `tests/test_rag_service.py`, not asserted by inspection.
- [x] ~25 Russian fixtures — **25, ground truth read from the files, not retrieved.**
- [ ] A recorded decision on `bge-m3` vs `e5-base` based on them. **Blocked on the ~2.5 h run, which
      is yours to authorise.** What the fixtures now say: `e5-base` is at **36% on Russian**, not the
      62% the old set reported.
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
