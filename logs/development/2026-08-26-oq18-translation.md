# OQ-18, the last measurement: does the resident model's Russian reach the ceiling?

**Task:** P9-T3 · **Date:** 2026-08-26 · **Status:** in progress — numbers land when the run does

Prior: [P9-T2](2026-08-26-oq18-chunking.md) measured both of OQ-18's levers and left one
question: `q_en` in the fixture file is a **human** translation worth +12.0 RU points, and
nobody had asked whether `qwen3.5:0.8b` reaches it. Ollama was not running on that machine.
It is running on this one.

---

## 1. What the router model actually produces

`scripts/translate_fixtures.py`, calling **the shipped translator**
(`oracle.rag.translate.translate_to_english`) rather than a prompt invented for the
occasion — this project has now had four measurements describe code it does not run, and a
script with its own prompt would have been the fifth.

25 Russian fixture questions, `qwen3.5:0.8b`, `think: false`, temperature 0, constrained
decoding against a one-field schema. Raw output in
[`oq18-translations-unguarded.json`](../measurements/oq18-translations-unguarded.json).

**Cost: 1.6 s p50 per question**, once the model is resident. Not a factor either way —
the packet path spends minutes.

**Quality: 5 of 25 were not translated at all.** They came back in Russian: valid JSON,
inside the length cap, and not a translation.

| id | the model's "translation" |
|---|---|
| `ru-pairing-bruteforce` | Что мешает подобрать ключ для подключения устройства через пробой? |
| `ru-air-gap` | Как можно полностью заблокировать доступ к сети для приложения? |
| `ru-migrations` | Как базовая система определяет текущую версию схемы? |
| `ru-symbol-extraction` | Как из исходных файлов имя классов и функций выводятся? |
| `ru-cross-validation` | Как оценивать качество модели при отсутствии большого количества данных? |

This is the **worst shape a failure can take**. Unguarded, the second probe embeds the same
question twice, RRF fuses a ranking with itself, and every log line says translation
succeeded. Nothing is red, nothing is slower, and the mechanism does nothing.

So `translate_to_english` rejects an output that still carries the source script — the
mirror of the `looks_translatable` pre-check. A rejection, not a repair: the repair the
model needs is a better model, and a second roll at 1.6 s each is latency spent on a die.

**A quieter defect the guard cannot catch.** `ru-token-refresh` came back as *"How does a
token refresh work in **Asteris**?"* — the project is called Asterim. The prompt says to
keep names exactly as written and the model renamed one anyway. A corrupted identifier
still embeds, still retrieves, and retrieves the wrong neighbourhood. There is no cheap
test for it; it is recorded here as the reason the *shipped* number and the *ceiling*
number are different quantities even where translation "worked".

---

## 2. The answer key was in the corpus  `the finding that outlives this task`

Investigating the one fixture P9-T2 called structurally unreachable turned up something
that changes what every OQ-18 number means.

`en-relay-dockerfile` asks *"where do we configure the relay Dockerfile"* and expects
`Asterim/Dockerfile.relay`. Its lexical top-5, by file, on the real corpus:

```
 0  ORACLE/tests/fixtures/retrieval/cases.yaml     ← the fixture file itself
 1  ORACLE/docs/RAG.md                             ← a document about the fixture set
 2  ORACLE/docs/current_task.md                    ← a document about this fixture
 3  ORACLE/docs/current_report.md                  ← a document about this fixture
 4  Asterim/Dockerfile.relay                       ← the answer
```

`config/collections.yaml` roots a collection at `C:/Projects`, which contains ORACLE. The
walk respects gitignore, so *committing* the fixture file is what made it indexable.
It contains all 38 questions verbatim beside their expected paths, and it is therefore
**the strongest lexical match for 37 of the 38 queries that measure this system.**

Measured: `answer-key documents in the lexical candidates of 37/38 queries`.

**This was found before, and fixed in the wrong file.** Commit `b660172` (2026-08-22),
*"eval: stop the fixture file from answering its own questions"*, diagnosed it exactly —
12 of 21 cases at the time — and put the guard in `scripts/index_knowledge.py`.
`scripts/eval_embeddings.py` never got it, and `eval_embeddings.py` is the script that
produced **every number OQ-18 records**.

That is the fifth instrument defect in five days, and the fourth of the same shape: two
implementations of one idea, and the fix reaching one of them. The others were the chunker
copy, the fusion denominator, the config denominator and the off-by-one summary header.
The pattern is worth stating plainly: **this project's measurements have been fine and the
things around them have not.**

The guard now lives in `ANSWER_KEY` in `eval_embeddings.py`, applied to the *ranking*
rather than to the corpus, so a saved forward pass stays valid.

**What is deliberately *not* excluded:** ORACLE's prose documentation. `docs/RAG.md`
quoting a fixture question is a real document that a real query could really want, and a
corpus that omits it is a corpus nobody has. Only the file whose purpose is to list the
answers comes out. The residual bias — ORACLE writing about its own fixtures, three of
which crowd `en-relay-dockerfile` — is real, is *increased by this very task*, and is
recorded rather than removed.

---

## 3. Scores

_Pending the full corpus run._

---

## 4. Decisions

_Pending._
