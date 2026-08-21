# Tool selection: 83.3% → 100%, and the truncation nobody would have noticed

**Date:** 2026-08-21 · **Task:** P3-T1 requirement 10

---

## The miss that started it

The first end-to-end run of the acceptance criterion *"commit my changes in Asterim with
message add the feature"* did this:

```
ROUTED COMMIT -> tool: ['git.add'] | said: 'Staged 3 file(s). (undo: u_f9768d3ab725)'
HEAD subject now: 'initial'
```

It staged the files and did not commit. No error, no warning — a plausible, adjacent,
wrong action, reported as a success. That is the characteristic failure of a small model
in this seat, and it is far more dangerous than a crash because it looks like it worked.

## Measure before fixing

Eyeballing one case is not evidence, so the first change was a harness, not a fix:
`scripts/eval_selection.py`, 18 cases, bilingual, including two where the correct answer
is **no tool at all**. Same shape as the existing `eval_intent.py`, and for the same
reason: it needs Ollama, so it is a measurement script and not a test.

**Baseline: 15/18 = 83.3%**, p50 1186 ms.

```
MISS [status] is Asterim clean          -> git.diff   (want git.status)
MISS [modify] delete all the log files  -> git.diff   (want none)
MISS [run   ] send this to the printer  -> dev.build  (want none)
```

Two of the three misses are the same failure: **the model never chose "none".** Offered
a menu, it picked the nearest item rather than declining. In a tool-calling agent that
is the expensive kind of wrong — the user gets an action they did not ask for.

Note the original commit-vs-add miss did *not* reproduce in the harness. It is a
boundary the model is unstable on rather than one it always gets wrong, which is worse,
not better: an intermittent wrong action is one nobody can reproduce on demand.

## Two levers, both already established in this repo

**1. Few-shot examples.** The intent classifier's own notes call this "the single
highest-leverage accuracy lever for a 0.8B classifier, and cheap" — prompt processing
runs ~1700 tok/s on this GPU while generation runs at ~45. The examples were chosen to
cover the boundaries that actually failed, not to be tidy:

- `add` vs `commit`, in both languages;
- `status` vs `diff` — "is it clean" wants a verdict, not a patch;
- **"none" twice**, because a model that never sees a refusal never produces one.

**2. Sharper summaries.** The summary is the entire basis for the choice, which is why
the registry refuses a contract without one. They were written to describe the tool;
they are now written to *distinguish* it from its neighbour:

| tool | before | after |
|---|---|---|
| `git.add` | "Stage a file or directory…" | "Stage files so a later commit can include them. **Does NOT create a commit.**" |
| `git.commit` | "Commit staged changes with a message." | "**Record** the staged changes as a commit. **Requires a message.**" |
| `git.status` | "Branch, ahead/behind, and which files are…" | "**Whether the repository is clean**… **Does not show the changes themselves.**" |
| `git.diff` | "Show changes as a patch plus a per-file stat." | "The actual **line-by-line** changes… **For whether anything changed at all, use git.status.**" |

**Result: 18/18 = 100%**, p50 1157 ms — accuracy up 16.7 points at no latency cost,
because the added tokens are prompt tokens and the generated output is still ~10.

And the live criterion now passes:

```
ROUTED COMMIT -> tool: ['git.commit'] | said: 'Committed 59eef851 on main — 3 file(s), +10/-0.'
HEAD subject now: 'add the feature'
UNDO -> commit 59eef851 undone; the changes are staged again
HEAD after undo: 'initial'   still staged: ['feature.py', 'pyproject.toml', 'test_thing.py']
```

## The bug the fix exposed

Adding the examples produced this, on every call:

```
context.never_evict_truncated  band=TOOLS  available=200  needed=211
```

**The tool descriptions were being cut off before the model ever saw them.** Selection
had been borrowing `CallType.ROUTE`'s budget, whose bands are sized for the intent
classifier: a 1600-token SYSTEM band full of few-shot examples and a 200-token TOOLS
band. Selection is the *inverse* shape — a small fixed system prompt, and a TOOLS band
that carries the thing the decision is made from.

It still scored 100%, which is exactly why this was worth chasing: a silent truncation
that does not visibly break anything is one that stays until the catalogue grows and
accuracy quietly falls off.

Fixed by giving selection its own `CallType.SELECT`, sized from measurement rather than
from symmetry:

```
system prompt      180 tokens
examples           229 tokens
descriptions   31–211 tokens (7 candidates at the widest intent)
--------------------------------
SELECT budget     1200, TOOLS band 500
```

500 leaves room for the catalogue to reach the 40-tool cap without a summary being cut
off mid-sentence.

## What the model is allowed to decide

Worth restating, because 100% on 18 cases is not a licence to trust it:

- it picks a **name from an enum** built out of the intent-filtered catalogue, so an
  off-menu answer is unspellable rather than validated after the fact (ADR-0017);
- it supplies **one string** that is inherently text — a commit message, a test filter;
- it never writes a path. The project path is composed from a root this process owns,
  after the classifier has checked the name against the registry.

There is a test that puts `C:\Windows\System32` in the model's free-text field and
demonstrates it goes nowhere. That property does not depend on the accuracy number, and
it is the one that has to hold when the number is worse than 100%.
