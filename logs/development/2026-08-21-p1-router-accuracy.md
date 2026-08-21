# 2026-08-21 — P1-T1: getting a 0.8B model to route reliably

Closes the accuracy half of [OQ-01](../../docs/OPEN_QUESTIONS.md#oq-01).
**Result: 93.3% intent accuracy, 0% structured-output failures, gate was 85%.**

Setup: `qwen3.5:0.8b`, num_ctx 16384, `think: false`, 30 fixture cases (RU + EN),
`scripts/eval_intent.py`.

## The progression, and what each step actually bought

| # | Change | Intent acc. | Struct. failures |
|---|---|---|---|
| 0 | first run | **23.3%** | **27.9%** |
| 1 | `confidence` float → enum | 63.3% | **0%** |
| 2 | few-shot examples in the system prompt | 83.3% | 0% |
| 3 | deterministic rules for named agents + pipeline names | 90.0% | 0% |
| 4 | stop truncating the example block | **93.3%** | 0% |

Nothing here was model tuning. Every gain came from changing what we ask for and where
the decision is made.

## Finding 1 — Ollama enforces enums, not numeric ranges

The first run looked like a catastrophic model failure. It was a schema bug of mine.

`confidence: float = Field(ge=0.0, le=1.0)` became `minimum`/`maximum` in the JSON
Schema, and **Ollama's constrained decoding ignores them**. The model emitted `95`
meaning 95%, which failed pydantic validation on **12 of 30 cases**.

Enums *are* enforced at the token level. Switching to
`Literal["high","medium","low"]` took structured failures from 27.9% to **0%** and
accuracy from 23.3% to 63.3% in one change.

> **Rule for this codebase:** express constraints the decoder can enforce — enums,
> required fields, types. Never rely on `minimum`, `maximum`, `pattern`, or
> `minLength` to hold. Validate them, but do not *depend* on them.

A three-value enum is also a more honest ask: a 0.8B model has no calibrated notion of
0.73 confidence, and we only ever compare against a threshold.

## Finding 2 — few-shot examples are the biggest lever (63% → 83%)

Twenty short examples covering the boundaries that actually confused the model, with
Russian on the pairs that degraded most. Worth more than every prompt-wording change
combined.

## Finding 3 — the prompt cache does NOT survive a changed user message

I initially assumed the example block was free, because repeating an *identical*
request showed prompt-eval dropping 761 ms → 41 ms. **That assumption was wrong**, and
I had already written it into a code comment before testing it properly.

Same system prefix, different user message each time:

```
[0] prompt_eval 766 ms (883 tok)   <- cold
[1] prompt_eval 566 ms (881 tok)
[2] prompt_eval 569 ms (881 tok)
[3] prompt_eval 569 ms (883 tok)
```

Ollama reuses its cache only for byte-identical requests; it does not reuse a shared
prefix across differing suffixes. **The few-shot block therefore costs ~380 ms on every
routed turn.** Still worth it at +30 accuracy points, but it is a trade, not a freebie.
The comment in `context/budget.py` now says so.

## Finding 4 — a constant ~600 ms per-request floor

A two-token prompt generating **zero** tokens:

```
minimal call: wall 638 ms | prompt_eval 44 ms | gen 0 ms | unaccounted 594 ms
raw GET /api/tags: 4.9 ms
```

~600 ms is Ollama's own per-request overhead — not the network (5 ms), not the model,
not the grammar (with-schema and without-schema are identical).

So routed-turn latency decomposes as: **~600 ms fixed + ~570 ms prompt + ~330 ms
generation ≈ 1.5 s**, which matches the measured p50 of 1542 ms.

**The 900 ms p50 route gate in ROADMAP Phase 1 was mis-derived.** It came from OQ-01's
prompt-eval numbers alone and never budgeted for generation or per-request overhead. It
is not reachable on this stack at any prompt size. Restated honestly in the roadmap.

## Finding 5 — three decisions the model should never have been asked to make

The confusion table kept pointing at `delegate`. But "is this a small edit or a big
refactor" is an **effort estimate**, not a classification, and a 0.8B model has no basis
for it. Three cases were deterministic facts of the sentence all along:

| Case | Model said | Now decided by |
|---|---|---|
| "ask Claude to fix the migration" | `investigate` | pre-router regex: verb + agent name |
| "run the asterim-check pipeline" | `run` | pre-router: registered pipeline names |
| "остановись" | `question` | pre-router: bare stop word (already existed) |

This is ADR-0011 doing its job. Each is now 100% reliable, costs **~5 ms instead of
~1500 ms**, and works with the model offline. Measured live: 5–6 ms for pre-routed
turns vs 1503–3150 ms for model-routed ones.

The remaining `modify`/`delegate` boundary is deliberately left to a **later,
better-informed step**: once retrieval knows how many files are involved, escalation is
a deterministic decision. The router only needs to know a code change was requested.

## Finding 6 — two bugs the work surfaced

- **The test suite was calling a live Ollama.** `_build_state` ran `preflight()`, so the
  whole suite quietly depended on a running daemon — exactly what docs/TESTING.md
  forbids. Fixed with `Settings.llm_enabled`, default True, False in the fixture. Suite
  time fell from 20.8 s to 2.5 s, and hermeticity is now asserted.
- **A `since_seq` ahead of the server hung the stream.** After a database reset, a
  client's stored seq exceeds `last_seq`; every subsequent event was filtered as a
  duplicate, leaving the socket open, live and permanently silent. Now triggers a
  `session.resync`. Found by a live smoke test, not by the unit suite — worth
  remembering when deciding how much to trust green tests.

## Final numbers

```
intent accuracy    93.3%  (28/30)   gate 85%   PASS
project accuracy   90.0%  (27/30)
clarify behaviour 100.0%  (30/30)
pre-routed             3 cases with no model call
structured output  attempts=28 repairs=0 failures=0  rate 0.00%   gate <2%  PASS
route latency      p50 1542 ms  p95 1868 ms          gate 900 ms  MISS (see Finding 4)
route prompt       1227 est / 908 real tokens        budget 2000
```

Remaining errors: `run`→`delegate` ×1, `delegate`→`modify` ×1. Both on the
modify/delegate boundary discussed above.

## Dead ends

- **Shrinking the schema to cut latency.** Dropping `targets`/`needs_plan` reduced
  output to ~19 tokens; generation is only ~330 ms of a ~1500 ms turn, so the remaining
  win is small. The fixed 600 ms overhead dominates and is not ours to optimise.
- **Assuming prefix caching.** Cost me a wrong code comment. Measure, then comment.
- **Prompt wording alone.** Rewriting the intent definitions without examples moved
  accuracy by only a few points; examples moved it by twenty.

## Follow-ups

- `ApproxCounter` over-estimates by ~35% (1227 estimated vs 908 real). Conservative is
  the correct direction, but the margin is wasteful — calibrate against `ExactCounter`.
- Try `/api/generate` with a pre-rendered prompt to see whether the ~600 ms floor is
  specific to `/api/chat`.
- Re-run this eval with `qwen3.5:2b` for comparison. It costs ~2× latency at 36%/64%
  CPU offload, but its accuracy ceiling is unknown and worth knowing.
