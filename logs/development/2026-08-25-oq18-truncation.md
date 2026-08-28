# OQ-18, lever two: 10% of the corpus is invisible, and it is not why Russian fails

**Date:** 2026-08-25 · **Task:** P9-T1 · **Script:** `scripts/measure_truncation.py`
**Answers:** [OQ-18](../../docs/OPEN_QUESTIONS.md#oq-18) lever 2 · **Resolves:** the `TO VERIFY`
in `src/oracle/rag/chunking.py`

OQ-18 named two levers for the 61%-against-an-80%-gate recall problem and said which to measure
first, and why:

> **Measure the second first** — it is a property of the corpus that can be counted without
> building anything, and it would change what the first experiment means.

Counted. It changes what the first experiment means, but not in the direction the question
expected.

---

## The numbers

Corpus as declared in `config/collections.yaml`, chunked by the shipped chunker, tokenized with
`bge-m3`'s own tokenizer. No ONNX inference — tokenizing *is* the measurement, so the whole thing
costs about a minute rather than the tens of minutes `eval_embeddings.py` costs.

| | |
|---|---|
| documents | 1,493 |
| chunks | 12,648 |
| **chunks over the 512-token window** | **2,545 (20.1%)** |
| of those: median / max | 615 / 2,370 tokens |
| **tokens never embedded** | **506,146 of 5,013,020 (10.10%)** |
| chunks over `MAX_CHARS` (1800) | 2,146 (17.0%), longest 3,687 chars |

By content kind, and this is where it stops being uniform:

| kind | over 512 | total | rate |
|---|---|---|---|
| config | 826 | 940 | **88%** |
| text | 87 | 116 | 75% |
| markdown | 535 | 3,263 | 16% |
| code | 1,097 | 8,329 | 13% |

The `TO VERIFY` in `chunking.py` guessed "a minority of code chunks still truncate". The estimate
was right about code and wrong about the shape: **config files are almost entirely invisible past
their first ~512 tokens**, and they are the files most likely to hold the answer to "how is this
configured".

## The bug the measurement found on the way

`MAX_CHARS = 1800` is documented as the cap. **17% of chunks are longer than it**, the longest by
more than double (3,687 characters). `_pack` and `_window` treat it as a target, not a bound.

This is a plain bug in the splitter that already exists, independent of tokenizers and models, and
it is part of why the "~500 tokens" estimate was optimistic: the estimate was computed against a
cap that is not applied. It is recorded rather than fixed here — see below for why.

## What it means for OQ-18, which is the point

**Truncation is not what keeps the Russian cases out of the candidate list.**

OQ-18's key observation is that *seven of the 25 Russian cases never enter the thirty candidates at
all* — no reranker, no wider top-k and no further fusion can reach them. Those seven are exactly
the seven Russian fixtures that point at the **notes** collection. Their expected source files:

| fixture | expected file | tokens never embedded |
|---|---|---|
| ru-vector-search | `08 - Transformers and LLMs/Vector Search.md` | **0%** |
| ru-finetuning | `08 - Transformers and LLMs/Fine-Tuning.md` | **0%** |
| ru-bias-variance | `02 - Mathematics/Bias-Variance Tradeoff.md` | **0%** |
| ru-cross-validation | `04 - Classical Machine Learning/Cross-Validation.md` | **0%** |
| ru-learning-rate | `05 - Optimization/Learning Rate.md` | **0%** |
| ru-nonlinearity | `06 - Neural Networks/Activation Functions.md` | **0%** |
| ru-model-degrades | `11 - MLOps/Data Drift and Concept Drift.md` | **0%** |

Every one of them fits inside the window with room to spare — the longest chunk across all seven is
496 tokens. Across all 25 Russian fixtures, only five expected files contain *any* truncated chunk,
and the worst of those loses **11%** of its tokens (`Asterim/Dockerfile.relay`, a two-chunk file).

So: the second lever is measured, and it is **not the cause**. Query translation is the hypothesis
that remains, and it now has to be run on its own merits rather than after a cheaper fix.

**This is the useful half of "measure the cheap one first".** Had truncation been the cause, the
translation experiment would have been measuring a corpus with a hole in it and its result would
have meant nothing. It is not, so the translation experiment is now interpretable — which is
exactly what OQ-18 said the ordering was for.

## The methodology mistake I made, since it is the reusable part

The first run of this script reported **seven Russian fixtures as "NOT IN THE CORPUS"** — and they
were the same seven. That is a spectacular-looking finding: *the unreachable cases are unreachable
because nobody indexed their documents.* It was wrong.

`eval_embeddings.hit()` matches a fixture to a corpus path with `e in f or f.endswith(e)`, because
the notes collection keys documents relative to its root and the vault directory
(`AI-ML-Vault/`) is a prefix the fixtures do not carry. My script used a dict lookup on the exact
path. Two different rules for "which file is this" in the same measurement, and the second one
produced a headline.

The lesson is narrow and worth keeping: **a measurement that resolves identity differently from the
system it measures is measuring itself.** The fix was to call the same rule; `resolve()` in the
script now says so in its docstring, because the wrong version was convincing.

## Why nothing is fixed here

Two real defects are recorded and neither is repaired in this task:

1. the character cap is not enforced;
2. 10% of the corpus is never embedded, concentrated in config files.

Both are corpus repairs with a measurable payoff, and both change chunk boundaries — which
invalidates the index and every recall number measured against it, including the one OQ-18 is
about. Doing that inside a task about *memory* would mean the next retrieval measurement could not
be compared with the last one, and the comparison is the only thing that makes these numbers worth
anything. They belong to whichever task next touches retrieval, together, followed by one reindex
and one re-run of `eval_embeddings.py`.

## Reproducing

```
uv run python scripts/measure_truncation.py --json out.json
```

Needs `tokenizers` and `D:/ORACLE/models/embeddings/bge-m3/onnx/tokenizer.json`. Not in
`scripts/check.py`: it needs the real corpus and the model on disk, and a gate that cannot run on
a fresh checkout is not a gate.
