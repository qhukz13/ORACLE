# 2026-08-22 — `как` is a stopword, and the corpus could not tell

Requirement 6 of [P5-T2](../../docs/current_task.md) expanded the Russian fixtures from 8 to 25. The
first run of the larger set said `e5-base` scores **36%** on Russian, not the 62% the eight had
reported. Chasing that turned up a real bug on the way — and, more usefully, ruled out the thing I
assumed was the problem.

## The symptom

Fused rank of the correct answer was worse than the **dense** rank in every Russian case where the
two differed:

| | dense | fused |
|---|---|---|
| `ru-migrations` | 20 | not in top 30 |
| `ru-workspace-permissions` | 22 | not in top 30 |
| `ru-bias-variance` | 15 | 24 |
| `ru-two-users-one-agent` | 15 | 23 |
| `ru-learning-rate` | 13 | 18 |

Fusion was supposed to be *gated* against exactly this: `discriminating_terms` returns nothing when
BM25 cannot help, and a Russian question against an English corpus was the case it was written for.

## The bug

The gate was opening, and what BM25 returned explains why:

```
как проверяются права пользователя внутри рабочего пространства
  discriminating terms: ['как', 'проверяются', 'права', 'внутри']
     как            df=90
     проверяются    df=1
     права          df=10
     внутри         df=45
  BM25 top hits:  GrowAMonster/docs/UI_SYSTEM.md
                  GrowAMonster/3d_models/_generator/gen_readme.py
                  GrowAMonster/reports/task_report.md
```

GrowAMonster is documented in Russian. So a Russian question about *Asterim's* RBAC retrieved
*GrowAMonster's* documentation — matching on `как` ("how"), `внутри` ("inside"), `она`, `имеет`.

**Document frequency measures rarity in the corpus, not uninformativeness in the language, and those
are the same thing only when the corpus and the query share one.** `the` is in most of an English
corpus and is correctly dropped. `как` is the same kind of word and was in 0.8% of this corpus, so it
read as *highly discriminating*. The denominator was wrong.

Fixed by counting against the sub-corpus the term could possibly match: 872 of 11,413 chunks contain
Cyrillic, so `как` is 10% of the Russian rather than 0.8% of everything, and it drops out. The
majority-language path is untouched — for a Latin term the denominator is still the whole corpus, so
every English and identifier fixture behaves exactly as before.

The count costs a full scan (~1.3 s), so it is computed once per build and stored in `meta`. An index
built before the census returns `None` and the caller keeps the old behaviour: an older index is not
a broken one.

## What it was worth: better ranks, and **no change to recall at all**

Recall@5 stayed at 55% overall and 36% on Russian, with the identical set of misses. Retrieval got
slightly *faster* — 243 ms p50 against 302 with the census removed, measured back to back in one
process, because the FTS query carries fewer terms.

So it is a bug fix that pays in noise removed and latency, and it moved no case across the threshold.
That is the honest accounting, and the reason why is worth more than the fix:

## What this ruled out

Of the 25 Russian fixtures, the correct document sits at:

| fused rank | cases |
|---|---|
| 1–5 (a hit) | 9 |
| 6–10 | **0** |
| 11–30 | 4 |
| never in the top 30 | **12** |

**Twelve of twenty-five never enter the candidate set**, so no re-ranking, no larger top-k and no
fusion change can reach them. And reading the dense column directly: turning the lexical half off
entirely for Russian would put the *same nine* in the top 5. Fusion is neither helping nor hurting
recall@5 here — it only shuffles ranks below the cut.

That leaves exactly one lever. The Russian failures are the embedding, and
[OQ-02](../../docs/OPEN_QUESTIONS.md#oq-02) chose `e5-base` on a Russian sample of eight that
overstated it by 26 points. `bge-m3` scored 100% on that same eight against e5-base's 75%; both
numbers are inflated, but the gap is not the kind that a fusion tweak closes.

**The empty 6–10 bucket is the shape of the result.** A model that was nearly right would put answers
just below the cut. This one either finds the document or does not come close, which is what a
cross-language embedding failure looks like rather than a ranking one.
