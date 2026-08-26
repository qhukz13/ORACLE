# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P9-T3b — collect the scheduled corpus run, and close OQ-18**

**Phase:** [9 — memory & context engine](ROADMAP.md#phase-9--memory--context-engine--supervisor-arc) · **Scope:** the last of it
**Status:** `WAITING ON A SCHEDULED JOB` · **Set:** 2026-08-26
**Previous task:** P9-T3 (translator measured, mechanism shipped) and **Phase 10 — done**; see
[`current_report.md`](current_report.md), the
[translation dev log](../logs/development/2026-08-26-oq18-translation.md) and the
[Phase 10 dev log](../logs/development/2026-08-26-p10-pipelines.md).

---

## Do this first: the numbers are probably already on disk

A Windows scheduled task, **`ORACLE-OQ18-eval`**, runs
[`scripts/run_oq18_eval.cmd`](../scripts/run_oq18_eval.cmd) at **2026-08-27 07:12** and takes about
three hours. It needs nothing running — not even Ollama, because the router model's translations
were measured on 2026-08-26 and are read from
[`logs/measurements/oq18-translations.json`](../logs/measurements/oq18-translations.json).

```
logs/measurements/oq18-translated.txt     # the arms, with per-case miss lists
logs/measurements/oq18-translated.json    # the same, structured
D:/ORACLE/scratch/oq18-vectors-bge-m3.npz # the forward pass, reusable via --load-vectors
```

Check it ran, then remove the task:

```powershell
Get-ScheduledTask -TaskName ORACLE-OQ18-eval
Unregister-ScheduledTask -TaskName ORACLE-OQ18-eval -Confirm:$false
```

If it did not run, start it by hand — `scripts/run_oq18_eval.cmd` — and do something else for three
hours. **Do not re-run it after editing a tracked file in this repository**: `C:/Projects` contains
ORACLE, so an edit changes the corpus and invalidates the saved forward pass, which is what made
the 2026-08-26 attempt worth abandoning.

## Why this task exists

Everything expensive about [OQ-18](OPEN_QUESTIONS.md#oq-18) is now done. What is left is reading
eight already-computed numbers and making two decisions that were deliberately held back until
there was a number to make them on.

The run scores eight arms. The two that matter, and the difference between them:

| arm | what it measures |
|---|---|
| `dense_xl` | the **ceiling** — a human translation, +12.0 RU points as measured by P9-T2 |
| `dense_mt` | the **mechanism** — what `qwen3.5:0.8b` actually produced, 5 of 25 refused by the guard |

Each is also scored with the answer key eligible, printed as a `with the answer key eligible` line,
so every number recorded before 2026-08-26 stays comparable to every number after it.

## Requirements

1. **Write down how much of the +12.0 ceiling survived.** `dense_mt` against `dense_xl`, on the
   Russian subset, composed per-case for the shipped path (`dense` for RU, `gated` for EN — the
   script rule makes a Russian query dense-only). The composition method is in the
   [P9-T2 dev log](../logs/development/2026-08-26-oq18-chunking.md); do not re-derive it differently.
2. **Decide whether translation stays on.** It is already shipped on the packet path behind
   `Settings.translate_queries`, defaulting on, on the *strength of the ceiling*. If `dense_mt` is
   close to `dense_xl`, that default is earned and RAG.md §5 says so with the number. **If it is
   not, turn the default off** and record what a better translator would have to be worth — the
   code stays, because the measurement is what changed, not the mechanism.
3. **Decide `en-relay-dockerfile` on the corrected numbers.** With the answer key excluded it may
   now pass, and the P9-T2 claim that it is *structurally* unreachable is already known to be
   wrong. Whatever the arms say, the fixture's `kind: semantic` is the label to argue about: its
   answer is a config file that RAG.md §2 says is **never embedded**, so "a dense model should win"
   cannot apply to it. Either re-label it `lexical` with that argument, or leave it and say why.
4. **Resolve OQ-18, or re-argue the gate with fixture-level evidence.** The evidence is complete
   after this run. A third outcome that leaves it open is not acceptable.
5. **Report the answer-key correction as a correction.** Every recall number this project recorded
   before 2026-08-26 was computed with the answer key eligible. Say so where those numbers are
   quoted — OQ-18 and RAG.md §8 — rather than quietly replacing them.

## Constraints

- **Do not move the gate to where the numbers are.** 6.3 points on 38 fixtures is 2.4 cases.
- **Do not edit a tracked file before the run finishes.** It changes the corpus.
- The fixture set is versioned. A change to it is a change to the claim and belongs in the same
  commit as its argument.
- Do not touch the memory subsystem, the chunker, or the planner ladder.

## Acceptance criteria

- [ ] The surviving share of the +12.0 RU ceiling is written down, composed for the shipped path.
- [ ] `Settings.translate_queries`' default is confirmed or flipped, on that number, in RAG.md §5.
- [ ] `en-relay-dockerfile` is decided.
- [ ] [OQ-18](OPEN_QUESTIONS.md#oq-18) is resolved, or the gate re-argued with fixture-level evidence.
- [ ] The answer-key correction is stated wherever pre-2026-08-26 recall numbers are quoted.
- [ ] The scheduled task is removed once collected.
- [ ] `make check` green.

## Relevant files

Read first: [`logs/measurements/oq18-translated.txt`](../logs/measurements/oq18-translated.txt) ·
the [translation dev log](../logs/development/2026-08-26-oq18-translation.md) ·
`ANSWER_KEY` in `scripts/eval_embeddings.py`.
Modify: `docs/RAG.md` §5 · `docs/OPEN_QUESTIONS.md` OQ-18 · possibly
`tests/fixtures/retrieval/cases.yaml` · possibly `src/oracle/config.py`.

## Definition of done

Acceptance met · `make check` green · a dev log with the numbers · `current_report.md` overwritten ·
this file set to **P11-T1**.

---

## Then: Phase 11

[Phase 11](ROADMAP.md#phase-11--execution-visualisation--advanced-ui--capability-arc) is the next
phase and the largest remaining one. It opens with a measurement, not a view:
[OQ-22](OPEN_QUESTIONS.md#oq-22) — offline layout cost, canvas-vs-SVG at corpus scale, semantic-edge
readability — **before** the knowledge graph is built on top of the answers, per sequencing rule 6.
[OQ-14](OPEN_QUESTIONS.md#oq-14) is a genuine go/no-go that could *remove* the orbital view from
scope, which makes it worth answering early rather than late.

---

## Carried over, not forgotten

- **A merge-gate test that fails under CPU starvation.**
  `tests/security/test_terminal.py::TestNothingIsLostOnTheWayOut::test_a_long_burst_arrives_complete`
  lost 189 lines of a ConPTY burst twice on 2026-08-26, both times with all 24 threads saturated by
  the corpus run — and at `HEAD` as well, so it belongs to nothing recent. **Idle, it passes in 6 s
  and the gate is green.** Left open rather than closed because of what it is a test *of*: if it
  reappears, decide whether the reader genuinely drops output under starvation (a real bounded-buffer
  bug) or whether its own deadline is too tight (a test fix). Those are different repairs, and
  guessing between them is how a data-loss test gets a tolerance instead of an answer.
- **`TO VERIFY`: how much of the corpus contamination is on the *dense* side.** `cases.yaml` is
  config and never embedded, so it cannot pollute a dense ranking — but ORACLE's markdown *is*
  embedded, and `docs/RAG.md`, `docs/OPEN_QUESTIONS.md` and `docs/current_*.md` all discuss the
  fixtures at length. Phase 10 added more of it. Cheap to answer once the saved forward pass exists:
  rank each fixture query and count ORACLE documents in the top 5.
- **One supervised live run of the Phase 8 scenario** on a real project, every preview
  human-approved — deliberately left for a person
  ([P8-T3 dev log](../logs/development/2026-08-25-p8t3-ladder.md)).
- **One supervised live run of `oracle-selfcheck`.** It is priced against the real policy by a test,
  but nothing has executed it end to end with a person approving the card. Same reasoning as above.
- **A memory friction**: a correction typed while a graph runs is refused, because "never mid-plan"
  is implemented literally. The fix, when somebody hits it, is a queue — not an exception.
- **Band 6 is not on the interactive answer path.** P9-T2 measured why: the embedding alone exceeds
  the latency headroom. Revisit only with a number.
- **Scheduled pipeline runs** are post-MVP, and PIPELINES.md §5's rule for them — nothing above T1
  while nobody is watching — is not yet enforced, because nothing schedules anything. The hook
  exists: `check(..., max_tier=Tier.T1)`.
