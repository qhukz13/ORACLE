# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P5-T2 — Finish project knowledge: tree-sitter, the watcher, and a budget that is true
**Status:** `IN PROGRESS` — five of six requirements done, the gate green. The sixth needs a decision
from you, and it is a bigger decision than the task file thought.
**Date:** 2026-08-22

---

## The headline

**The Russian half of retrieval is much worse than we believed, and we believed it because the
fixture set was too small to know better.**

`e5-base` was chosen in [OQ-02](OPEN_QUESTIONS.md#oq-02) partly on **62% recall over eight Russian
questions**. Expanding that set to 25 — ground truth read from the source files, not retrieved — puts
it at **36%**. The eight had overstated it by 26 points, on top of the 13 the 3,000-chunk sample had
already overstated before that.

Nothing regressed. The number was always this; the instrument was too coarse to show it. OQ-02 is
**reopened**.

## What landed

| | |
|---|---|
| **Embedding cache** (req 1) | Full rebuild 42.8 min → **37 s**, zero forward passes, recall unchanged. |
| **tree-sitter chunking** (req 2) | Built, tested, and **switched off**. See below. |
| **Re-measured after re-chunking** (req 3) | Done, and it found a benchmark leak worth more than the chunker result. |
| **Watcher under the daemon** (req 4) | Running, HALT-aware, publishing `knowledge.state`. Measuring it found a 3.1× filter defect. |
| **PDF** (req 5) | Text layer, no OCR, page anchors, `local_foreign`. Costs **nothing** in recall. |
| **Russian fixtures 8 → 25** (req 6, half) | Done. It is what produced the headline. |

## The three findings worth your time

**1. The benchmark was in the corpus.** `config/collections.yaml` roots a collection at
`C:/Projects`, which contains ORACLE, and the walk skips untracked files — so *committing* the
phase-5 work made `tests/fixtures/retrieval/cases.yaml` indexable. A file containing every fixture
question verbatim took a top-5 slot in 12 of 21 cases, and it silently moved the baseline between two
runs I was about to compare. `measure()` now discards it before ranking. This will recur: the corpus
is the developer's own machine, so every dev log and report about retrieval joins it.

**2. tree-sitter names symbols far better and retrieves worse.** No control-flow keyword and no call
expression appears as an anchor anywhere in the corpus, against `equal` (548 occurrences) and
`useEffect` (219) for the line matcher — both *calls*. And on the same corpus with the same fixtures
it loses recall@5 by two cases, across four builds. The line matcher wins by accident: it packs
neighbouring text, so a file's header prose lands beside the code it describes and a conceptual
question matches the paragraph. `chunking.SYNTAX_AWARE = False`, and the decision is yours — see
"What needs you".

Three real defects turned up while chasing that, each fixed, each verified by reading the bytes, and
**none of which moved the number**: a doc comment being severed from what it documents, a file-level
comment being glued to the first constant below it, and a class field's trailing `;` becoming its own
anchored block. The gap between "visibly better" and "measurably better" is the reason the criterion
is a number.

**3. Wiring the watcher up found a defect that inspection had missed for a whole task.** The filter
ran at 0.27 ms per event *on the event loop* — 1.3 s for an `npm install` — because `fnmatch`
normcases both arguments and on Windows `normcase` is a `LCMapStringEx` syscall. 160,000 trips
through the OS locale mapper for 5,000 events. Compiling the patterns once: **3.1×**, and the corpus
walker shares the function. The module docstring had claimed "everything cheap happens first" since
P5-T1; it was true about *what* the filter did and false about what it cost.

## Why the Russian result is the model, and not something cheaper

Two alternatives were eliminated by measurement before blaming the embedding:

* **A real bug in the fusion gate** — document frequency measures rarity *in the corpus*, which only
  means "uninformative" when corpus and query share a language. `как` was in 0.8% of this corpus and
  read as highly discriminating, so every Russian question retrieved GrowAMonster's Russian docs,
  matching on `как`, `внутри`, `она`. Fixed by counting against the Cyrillic sub-corpus. Ranks
  improved, retrieval got **faster** — and recall did not move at all.
* **It is not a ranking problem.** Of 25 Russian cases: 9 in the top 5, **0 in ranks 6–10**, 4 in
  11–30, and **12 never enter the candidate set**. Turning the lexical half off entirely would put
  the same nine in the top 5. The empty 6–10 bucket is the shape of the answer — a nearly-right model
  misses by a little; this one either finds the document or does not come close.

## What needs you

1. **The `bge-m3` run, ~2.5 h of CPU.** No longer marginal: the embedding is the only remaining
   lever, and `bge-m3` scored 100% on the same eight Russian questions where `e5-base` scored 75%.
   Both numbers are inflated; the gap is not the kind a fusion tweak closes.
2. **Does tree-sitter ship?** One constant. My recommendation is to leave it off and let the expanded
   fixture set decide, because 21 cases at 4.8 points each cannot settle a two-case difference in
   *either* direction — including the direction I would have preferred.
3. **Confirm the indexing budget wording** in ROADMAP.md — and note I corrected my own number: "warm
   rebuild = 37 s" was the best case quoted as the rule. A chunking change moves chunk *text*, which
   is the cache key, so the first tree-sitter build hit 45% of the cache and took **19.9 min**.

## State

Gate green — ruff, mypy, tsc, pytest, security, vitest. Branch `phase5-knowledge`.
Fast-forward with `git checkout main && git merge --ff-only phase5-knowledge`.

Logs: [tree-sitter](../logs/development/2026-08-22-treesitter-chunking.md) ·
[watcher](../logs/development/2026-08-22-watcher-daemon.md) ·
[PDF](../logs/development/2026-08-22-pdf.md) ·
[fusion denominator](../logs/development/2026-08-22-fusion-denominator.md) ·
[embedding cache](../logs/development/2026-08-22-embedding-cache.md)
