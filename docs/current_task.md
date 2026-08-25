# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P9-T2 — Retrieval: the hypothesis that is left, and the corpus it is measured against**

**Phase:** [9 — memory & context engine](ROADMAP.md#phase-9--memory--context-engine--supervisor-arc) · **Scope:** Supervisor arc
**Status:** `SET — not started` · **Set:** 2026-08-25
**Previous task:** P9-T1 — **done except the recall gate**; see
[`current_report.md`](current_report.md), [MEMORY.md §8](MEMORY.md#8-as-built--p9-t1-2026-08-25)
and the [OQ-18 dev log](../logs/development/2026-08-25-oq18-truncation.md).

---

## Why this task exists

The Phase 5 recall criterion has been unmet since 2026-08-24 and is the oldest open promise in the
project. P9-T1 removed one of the two hypotheses: **truncation is real, large, and not the cause**
— the seven Russian cases that never reach the candidate list point at documents that fit the model
window with room to spare.

That leaves query translation, which OQ-18 has always named and nobody has run. It also leaves two
measured defects in the chunker, and they belong in *this* task rather than a later one for a
specific reason: **both change chunk boundaries**, and a recall number measured across a boundary
change cannot be compared with the one before it. Fix the corpus and run the experiment in one
pass, or spend the next measurement arguing about which change moved it.

## What the earlier phases hand you

1. **The gap is characterised, not guessed.** 61% recall@5 overall, 44% on the 25 Russian fixtures,
   with seven that never enter the thirty candidates ([OQ-18](OPEN_QUESTIONS.md#oq-18)).
2. **The corpus defects are counted**: 20.1% of chunks over the 512-token window, 10.1% of all
   tokens never embedded, 88% of `config` chunks affected, and `MAX_CHARS` not enforced at all —
   17% of chunks exceed it, the longest by more than double. Numbers and method in the dev log.
3. **`scripts/measure_truncation.py`** re-runs that count in about a minute, so the repair has a
   before/after that costs nothing.
4. **`scripts/eval_embeddings.py`** is the recall harness, with the fixture set and the same
   corpus walk. It costs tens of minutes and needs the ONNX models on disk.
5. **The router model is already resident and already sees every query**, which is what makes
   translation a plausible marginal cost rather than a new dependency.

## Requirements

1. **Fix the chunker's two defects, together, and re-measure:**
   - enforce `MAX_CHARS` (it is a bound, not a target — `_pack` and `_window` currently exceed it);
   - make the cap **token-aware** against the fixed model (`bge-m3`, OQ-02), so "~500 tokens" is a
     measurement rather than an English-prose average. Chunking then depends on the tokenizer,
     which is the trade `chunking.py` said was worth making once the model was fixed. It now is.
2. **Reindex and re-run both measurements**, in that order: `measure_truncation.py` to show the
   corpus is whole, then `eval_embeddings.py` for the recall number that the rest of the task is
   compared against. **Record the baseline before touching anything** — a repair with no before is
   an opinion.
3. **Query translation, as OQ-18 specifies it**: embed an English translation of a Russian question
   as a second dense probe and fuse the two candidate lists. The router model is resident and
   already sees the query; the translation is one short generation.
4. **Measure what it costs, not only what it buys.** RAG.md §5 has so far kept a model call off the
   retrieval path, and the latency budget has ~70 ms of headroom at `bge-m3`'s p95. If translation
   spends more than that, it ships behind a decision — for the `crosslang` case only, or not at
   all — and the decision is written down with its numbers.
5. **Resolve OQ-18 or re-argue the gate in writing.** Both are acceptable outcomes; a third
   measurement that leaves it open is not. If 80% is the wrong number for this corpus, say what the
   right one is and why, with the fixture-level evidence.
6. **A degraded path that still answers.** No translation model, no ONNX, no index: retrieval
   thins, it does not fail. The rule curation already follows.

## Constraints

- **Do not move the gate to where the numbers are.** Re-arguing it means an argument, not an edit.
- The fixture set is versioned: a change to it is a change to the claim, and belongs in the same
  commit with its reasoning. A fixture set adjusted until it passes measures nothing.
- Chunk-boundary changes invalidate the index. One reindex, one before, one after — not a sequence
  of small changes each with its own half-comparable number.
- Do not touch the memory subsystem, the planner ladder, or the replan budget.

## Acceptance criteria

- [ ] `MAX_CHARS` is enforced and the chunker is token-aware; `measure_truncation.py` reports a
      truncation rate under 2%, with the before number recorded beside it.
- [ ] The recall harness is re-run on the repaired index and the new baseline is written down,
      whatever it says.
- [ ] Query translation is implemented, measured for recall **and** latency, and shipped or
      refused on those numbers.
- [ ] [OQ-18](OPEN_QUESTIONS.md#oq-18) is resolved, or the 80% gate is re-argued in writing with
      fixture-level evidence.
- [ ] Retrieval degrades rather than failing when the model or the index is missing; a test says so.
- [ ] `make check` green.

## Relevant files

Modify: `src/oracle/rag/chunking.py` (both defects) · `src/oracle/rag/retrieval.py` (the second
probe and the fusion) · `scripts/eval_embeddings.py` (if the harness needs the translation arm) ·
`docs/RAG.md` (§5 as-built), `docs/OPEN_QUESTIONS.md` (OQ-18).
Read first: the [OQ-18 dev log](../logs/development/2026-08-25-oq18-truncation.md) ·
[the bge-m3 log](../logs/development/2026-08-24-oq02-bge-m3.md) ·
`scripts/measure_truncation.py` (for how the corpus is walked and matched).

## Dependencies

None outstanding. P9-T1's memory subsystem is independent of this and does not block it.

## Risks

| Risk | Mitigation |
|---|---|
| The repair and the experiment get tangled and neither number is attributable | Repair, reindex, measure, *then* experiment. Two recorded baselines, in that order |
| Translation lands on the latency path and quietly costs every turn | Measure p50/p95 before shipping, and scope it to `crosslang` if it does not fit |
| The fixture set drifts towards passing | It is versioned; a change to it goes in the same commit as its argument |
| A token-aware chunker makes chunking depend on the tokenizer | That is the trade, taken deliberately now the model is fixed. State it in RAG.md rather than letting it be discovered |

## Definition of done

All acceptance criteria · `make check` green · RAG.md corrected to as-built · OQ-18 resolved or
re-argued · a dev log with both measurements and their method · `current_report.md` overwritten ·
this file set to **P9-T3** or **P10-T1**, whichever the state of Phase 9 warrants.

---

## Carried over, not forgotten

- **One supervised live run** of the Phase 8 scenario on a real project, every preview
  human-approved — deliberately left for a person
  ([P8-T3 dev log](../logs/development/2026-08-25-p8t3-ladder.md)).
- **A validator inconsistency**: `verifier` + `verdict` is rejected while `reviewer` + `verdict`
  produces the identical deterministic task. Worth fixing in the task that next touches
  `plan.validate()`.
- **A memory friction**: a correction typed while a graph runs is refused, because "never mid-plan"
  is implemented literally. The fix, when somebody hits it, is a queue — not an exception.
