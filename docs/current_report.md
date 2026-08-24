# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** OQ-02 carry-over — the `bge-m3` run. **Done.** Phase 6's own remaining item is still
**one supervised live run**, untouched: it needs the owner present.
**Status:** OQ-02 resolved, a retrieval bug found and fixed, [OQ-18](OPEN_QUESTIONS.md#oq-18) opened.
Gate green.
**Date:** 2026-08-24

---

## What happened

The scheduled `bge-m3` run finally produced artefacts, and the answer was the opposite of the
expected one twice over.

**First reading: `bge-m3` loses.** Full corpus, 38 fixtures, shipped retrieval code — 53% recall@5
against `e5-base`'s 55%, and 32% against 36% on the cross-language column OQ-02 exists to decide. At
2.3x the build time and 2x the memory. It also failed the latency gate at p95 401 ms.

**Second reading: the gate was scoring it.** `discriminating_terms` — which decides whether BM25's
thirty results join the fusion — was admitting them on **38 questions out of 38**, including all 25
Russian ones, for which BM25 returned the corpus's one Russian-documented project whatever the
question was about. Fusion can only displace a correct dense hit *that exists*, so the damage scaled
with how good the dense half was: twelve points off `bge-m3`, nothing off `e5-base`.

With the gate fixed:

| embedding | fusion gate | recall@5 | crosslang | p95 |
|---|---|---:|---:|---:|
| `e5-base` | as shipped | 55% | 36% | 271 ms |
| `e5-base` | fixed | 55% | 36% | 260 ms |
| `bge-m3` | as shipped | 53% | 32% | 401 ms (fails) |
| **`bge-m3`** | **fixed** | **61%** | **44%** | 332 ms |

No configuration regresses; `bge-m3` gains eight points on the column that matters, and the latency
failure goes away because BM25 stops running on every query.

## What changed in the design

- **The fusion policy and the embedding choice are not independent.** RAG.md §5 already recorded
  that fusion was worth +9 to `e5-base` and −5 to `e5-small` and read it as a curiosity. It is a
  rule: the stronger the dense retriever, the more an unfiltered lexical list costs. A model
  comparison run through a leaky gate measures the gate.
- **The 2026-08-22 denominator fix was the right diagnosis and the wrong remedy.** Scoping Cyrillic
  terms to the Cyrillic sub-corpus dropped `как` and moved recall not at all — because `если` (4% of
  the Russian) survives, and the genuinely rare Russian words match the wrong project anyway. That
  null result was then read as "the Russian failures are the embedding", through the same instrument.
- **Two rules now gate the lexical half**, both chosen off a measured plateau rather than argued:
  a term must be in the script the corpus is written in (*minority*, not Cyrillic — a Russian corpus
  gates out Latin), and the survivors must cover 40% of the question's answerable terms. A bare
  `PairingService` lookup is 100% of its question and still fuses.
- **One test assertion reversed on evidence.** `test_a_rare_russian_word_still_counts` asserted the
  opposite of what ships; its docstring now records what it used to claim and why the measurement
  refuted the premise.

## The decision waiting for the owner

**`DEFAULT` is still `multilingual-e5-base`.** The evidence favours `bge-m3` — six points overall,
eight on Russian — and the switch is one line (`DEFAULT = BGE_M3`) plus one rebuild. It costs a
~2.5 h cold build and takes resident memory from ~1.5 GB to ~3 GB. That is a resource commitment on
this machine rather than a measurement, so it is left flagged rather than taken.

## What is left

1. **One supervised live run** — unchanged from the last report, and still Phase 6's only open item.
   `uv run python scripts/verify_mcp_live.py` (`--dry-run` shows the payload and sends nothing).
   Closes P6-T3 requirement 1 and P6-T4 requirement 5. Needs the owner's go-ahead, like every egress.
2. **[OQ-18](OPEN_QUESTIONS.md#oq-18), opened by this work.** 61% is nineteen points under the
   Phase 5 recall gate, and 7 of 25 Russian cases never enter the candidate set at all — a shape no
   reranker reaches. Two untried levers: query translation, and the ~20% of chunks silently
   truncated at 512 tokens. **Count the truncation first** — it is free and it changes what the
   other experiment means.
3. `AntigravityAdapter` — still the deliberate gap in Phase 6's Definition of Done, to close or to
   record in ROADMAP.md as out of scope.

## Standing state

Branch `phase6-integration`. Gate green: ruff, mypy, tsc, pytest, security, vitest — `check: OK`.
Both measurement indexes are on disk (`D:/tmp/bge.db`, `D:/tmp/e5.db`) if anyone wants to re-run a
column; `knowledge.db` itself was never touched.

Logs: [OQ-02 / bge-m3](../logs/development/2026-08-24-oq02-bge-m3.md) ·
[capstone](../logs/development/2026-08-24-p6t4-capstone.md) ·
[MCP server](../logs/development/2026-08-24-p6t3-mcp-server.md) ·
[egress approval](../logs/development/2026-08-24-p6t2-egress-approval.md) ·
[the denominator, superseded](../logs/development/2026-08-22-fusion-denominator.md)
