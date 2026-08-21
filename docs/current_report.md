# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P1-T1 — Router fixtures and the local model provider
**Status:** `DONE` — accuracy gate passed, latency gate missed with cause · **Date:** 2026-08-21

---

## What was done

The echo agent is gone. ORACLE now classifies real intent with a local model, answers conversational
turns by streaming from it, and resolves a growing set of turns without touching the model at all.
Still **no tools and no side effects** — the policy gate is Phase 2.

Also this session: the repo was published to `github.com/qhukz13/ORACLE` (private), and
[OQ-05](OPEN_QUESTIONS.md#oq-05) was resolved, promoting Antigravity to a **Supported** integration.

## Headline result

```
intent accuracy    93.3%  (28/30)   gate 85%    PASS
clarify behaviour 100.0%  (30/30)
structured output  0.00% failures   gate <2%    PASS
pre-routed             3 cases at ~5 ms, no model call
route latency      p50 1542 ms                  gate 900 ms   MISS — see below
```

Progression, none of it model tuning: **23.3% → 63.3% → 83.3% → 90.0% → 93.3%**.
Full write-up: [`logs/development/2026-08-21-p1-router-accuracy.md`](../logs/development/2026-08-21-p1-router-accuracy.md).

## The four findings that mattered

1. **Ollama's constrained decoding enforces enums, not numeric ranges.** The first run looked like the
   0.8B model was hopeless (23.3%, 27.9% structured failures). It was my schema bug: `confidence:
   float(ge=0, le=1)` renders as `minimum`/`maximum`, which is ignored, so the model emitted `95` and
   failed validation on 12 of 30 cases. Switching to a three-value enum took structured failures to
   **0%**. Recorded as [ADR-0017](DECISIONS.md#adr-0017--constrain-what-the-decoder-can-enforce):
   *express constraints the decoder can enforce; never depend on `minimum`/`pattern`/`minLength`.*

2. **Few-shot examples are the biggest single lever** — 63.3% → 83.3%. Twenty short examples covering
   the boundaries that actually confused the model, with Russian on the pairs that degraded worst.

3. **The prompt cache does not survive a changed user message.** I assumed the example block was free
   because repeating an *identical* request dropped prompt-eval 761 ms → 41 ms — **and I wrote that
   assumption into a code comment before testing it properly.** With a different user message, prompt
   eval stays at ~570 ms. The block costs ~380 ms on every routed turn. Worth it at +30 accuracy
   points, but a trade, not a freebie. The comment now says so.

4. **Three decisions never belonged to the model.** The confusion table kept pointing at `delegate`,
   but "small edit or big refactor" is an *effort estimate*, not a classification. Naming an agent
   ("ask Claude to…"), naming a registered pipeline, and bare stop words are all facts of the
   sentence. Moved to the deterministic pre-router: **~5 ms instead of ~1500 ms**, 100% reliable, and
   they work with the model offline. That is ADR-0011 earning its place.

## Why the latency gate was missed

Measured decomposition of a routed turn:

| component | cost | ours? |
|---|---|---|
| Ollama fixed per-request overhead | **~600 ms** | no |
| prompt processing (~900 tok) | ~570 ms | yes, but it buys the accuracy |
| generation (~19 tokens) | ~330 ms | marginally |

The 600 ms floor is real and not ours to fix: a **2-token prompt generating zero tokens still costs
638 ms**, while raw HTTP to the same daemon is 5 ms.

**The 900 ms gate was mis-derived by me in the Phase 1 plan** — it came from OQ-01's prompt-eval
numbers alone and never budgeted for generation or per-request overhead. It is unreachable on this
stack at any prompt size. Restated honestly in the roadmap and tracked as
[OQ-15](OPEN_QUESTIONS.md#oq-15). The real mitigation already works: pre-routed turns cost ~5 ms.

## Two bugs this work surfaced

- **The test suite was calling a live Ollama.** `_build_state` ran `preflight()`, so the whole suite
  silently depended on a running daemon — precisely what [TESTING.md](TESTING.md) forbids. Fixed with
  `Settings.llm_enabled`; hermeticity is now asserted. Suite time fell **20.8 s → 2.5 s**.
- **A `since_seq` ahead of the server hung the stream.** After a database reset, a client's stored seq
  exceeds `last_seq`; every later event was filtered as a duplicate, leaving the socket open, live and
  permanently silent. Now triggers a `session.resync`. **Found by a live smoke test, not by the unit
  suite** — worth remembering when deciding how much a green suite proves.

## What changed

```
src/oracle/llm/          types · provider · ollama · fake · structured
src/oracle/context/      tokens · budget          (per-call-type budgets, taint carried)
src/oracle/router/       prerouter · intent · pipeline
src/oracle/core/         projects.py              (registry; echo.py deleted)
src/oracle/api/app.py    TurnPipeline wired, llm_enabled, resync-when-ahead
scripts/eval_intent.py   the accuracy gate (needs Ollama; NOT in check.py)
tests/                   test_router.py · test_context.py · fixtures/intent/cases.yaml
```

**89 Python + 14 TS tests. `uv run python scripts/check.py` green.**

## Verified live

```
> /help                                  [    6 ms | pre-router]
> остановись                             [    5 ms | pre-router]  -> "Stopped."
> ask Claude to fix the migration        [    5 ms | pre-router]  -> routed to delegate
> run the tests for Asterim              [ 1977 ms | model] intent=run proj=Asterim conf=high
> почему сломалась авторизация в Asterim [ 1695 ms | model] intent=investigate proj=Asterim
> what is a WebSocket, in one sentence   [ 3150 ms | model] streamed a real answer
```

## Unresolved / deliberately deferred

- **Latency** — [OQ-15](OPEN_QUESTIONS.md#oq-15). Try `/api/generate` instead of `/api/chat`; try
  llama.cpp directly (ADR-0009's escape hatch).
- **`ApproxCounter` over-estimates ~35%** (1227 estimated vs 908 real tokens). Conservative is the
  correct direction, but the margin wastes budget — calibrate against `ExactCounter`.
- **The modify/delegate boundary** is still the model's weakest point (both remaining errors). By
  design it should move to a deterministic step once retrieval can count the affected files.
- **Cancellation mid-stream** is wired through `asyncio` but has no dedicated test yet.
- **Generated TS types** still not done; `protocol.ts` remains hand-written and marked as such.
- **`qwen3.5:2b` was never re-benchmarked for accuracy** — unnecessary once 0.8b passed, but its
  ceiling is unknown.

## Recommended next action

**Start [P2-T1](current_task.md) — the tool system and policy gate.** It is the phase that makes
everything after it safe, and the roadmap forbids any write tool before it exists.

Resolve [OQ-04](OPEN_QUESTIONS.md#oq-04) (does `realpath` resolve Windows junctions?) **first**: the
path canonicaliser is the foundation of the filesystem sandbox, and a junction that resolves wrongly
is a sandbox escape.

```
uv run python scripts/check.py          # the gate
uv run python scripts/eval_intent.py    # accuracy (needs Ollama running)
uv run oracled                          # backend
npm --prefix apps/desktop run dev       # UI at http://localhost:5273
```
