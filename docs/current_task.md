# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P9-T3 — One fixture: measure the translator, and close OQ-18**

**Phase:** [9 — memory & context engine](ROADMAP.md#phase-9--memory--context-engine--supervisor-arc) · **Scope:** Supervisor arc
**Status:** `SET — not started` · **Set:** 2026-08-26
**Previous task:** P9-T2 — **done except the translator measurement**; see
[`current_report.md`](current_report.md), [OQ-18](OPEN_QUESTIONS.md#oq-18) and the
[dev log](../logs/development/2026-08-26-oq18-chunking.md).

---

## Why this task exists

[OQ-18](OPEN_QUESTIONS.md#oq-18) has been open since 2026-08-24 and is the oldest promise in the
project. P9-T2 measured both of its levers and a third it did not name. The shipped path went from
68.4% to 71.1%, and an English probe takes it to **78.9% — 30 of 38 fixtures, one short of the
80% gate.**

Everything expensive is done. What remains is one cheap measurement and one decision, and the
reason they are a task rather than a footnote is that getting them wrong is how a system acquires a
feature that works in the log and not on the machine.

## What the previous task hands you

1. **The numbers, per case.** `logs/measurements/oq18-{before,after}.json` and the miss lists in the
   `.txt` beside them. Any configuration can be composed per-case without re-embedding.
2. **The ceiling.** +12.0 RU points from an English probe, using **human** translations, recorded
   as `q_en` in `tests/fixtures/retrieval/cases.yaml` and labelled there as a ceiling.
3. **The cost.** A second dense probe is 63 ms p50 / 97 ms p95 — measured, and already more than
   the interactive budget's ~70 ms of headroom, before any generation call.
4. **A working harness.** `eval_embeddings.py` now uses the shipped chunker and scores `dense_xl`
   and `rrf_xl` alongside the rest. A full run is ~2 hours; `--sample` is there for faster
   comparisons and inflates every arm equally.
5. **One structural miss.** `en-relay-dockerfile` expects a config file, which is indexed lexically
   and never embedded. No dense probe can retrieve it.

## Requirements

1. **Measure the resident model's Russian.** Start Ollama, translate the 25 `q` values with the
   router model, and score the translated arm with *those* translations instead of the human ones.
   The question is one number: how much of the +12.0 RU ceiling survives.
2. **Decide, and say so with the number.** If most of it survives, ship translation on the
   **Handoff Packet's** retrieval path, where seconds are free — not on the interactive answer path,
   where P9-T2 measured that it does not fit. If little survives, refuse it and record what a better
   translator would have to be worth.
3. **A degraded path that still answers.** No Ollama, no model, a translation that fails or times
   out: retrieval thins to the native probe. `tests/test_rag_degradation.py` is where that goes.
4. **The structural miss.** `en-relay-dockerfile` measures fusion, and the script rule turns fusion
   off for the queries around it. Either the lexical half must reach config files for that query, or
   the fixture is testing two things with one number and should say which. Decide it; do not leave a
   fixture nobody can pass.
5. **Resolve OQ-18 or re-argue the gate.** With the translator measured, the evidence is complete —
   a third outcome that leaves it open is not acceptable. If 80% is wrong for this corpus, the
   argument must be about what the number is *for*, with the fixture-level evidence, not about where
   the measurements landed.

## Constraints

- **Do not move the gate to where the numbers are.** 6.3 points on 38 fixtures is 2.4 cases.
- **Do not ship the mechanism on the ceiling measurement.** That is the whole reason it was held
  back; repeating it here would waste the holding.
- The fixture set is versioned. A change to it is a change to the claim and belongs in the same
  commit as its argument — including any change to `en-relay-dockerfile`.
- Interactive latency is a measured budget, not a preference. Anything that spends it must show the
  number it bought.
- Do not touch the memory subsystem, the chunker, or the planner ladder.

## Acceptance criteria

- [ ] The router model's translations are measured against the same fixtures, and the surviving
      share of the +12.0 RU ceiling is written down.
- [ ] Translation is shipped on the packet path or refused, on that number, with the decision and
      its evidence in RAG.md.
- [ ] Retrieval degrades to the native probe when translation is unavailable; a test says so.
- [ ] `en-relay-dockerfile` is decided — reachable, or re-scoped with an argument.
- [ ] [OQ-18](OPEN_QUESTIONS.md#oq-18) is resolved, or the gate re-argued with fixture-level
      evidence.
- [ ] `make check` green.

## Relevant files

Modify: `src/oracle/rag/retrieval.py` (the second probe, if it ships) ·
`src/oracle/handoff/gather.py` and `src/oracle/api/app.py` (`_curate` passes the translator) ·
`scripts/eval_embeddings.py` (a model-translated arm beside the human one) ·
`tests/fixtures/retrieval/cases.yaml` · `tests/test_rag_degradation.py` ·
`docs/RAG.md` §5, `docs/OPEN_QUESTIONS.md`.
Read first: the [P9-T2 dev log](../logs/development/2026-08-26-oq18-chunking.md) ·
[the bge-m3 log](../logs/development/2026-08-24-oq02-bge-m3.md) ·
`discriminating_terms` in `retrieval.py` (the script rule is why the shipped path is dense-only).

## Dependencies

Ollama running locally with the router model. Nothing else outstanding.

## Risks

| Risk | Mitigation |
|---|---|
| The model's translations are scored by hand and flatter themselves | Score with the same harness and the same fixtures; the human arm stays in the file as the ceiling to compare against |
| Translation lands on the interactive path because it is convenient | The latency is already measured and does not fit. Packet path or nothing |
| The last fixture gets "fixed" by editing the fixture | A change to the set is a change to the claim, in the same commit, with the argument |

## Definition of done

All acceptance criteria · `make check` green · RAG.md §5 corrected to as-built · OQ-18 resolved or
re-argued · a dev log with the translation measurement · `current_report.md` overwritten · this file
set to **P10-T1**, or to whatever Phase 9's remaining state warrants.

---

## Carried over, not forgotten

- **One supervised live run** of the Phase 8 scenario on a real project, every preview
  human-approved — deliberately left for a person
  ([P8-T3 dev log](../logs/development/2026-08-25-p8t3-ladder.md)).
- **A memory friction**: a correction typed while a graph runs is refused, because "never mid-plan"
  is implemented literally. The fix, when somebody hits it, is a queue — not an exception.
- **Band 6 is not on the interactive answer path.** P9-T2 measured why: the embedding alone exceeds
  the latency headroom. Revisit only with a number.
