# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P1-T1 — Router fixtures and the local model provider**

**Phase:** [1 — Local LLM + agent runtime](ROADMAP.md#phase-1--local-llm--agent-runtime--mvp) · **Scope:** MVP
**Status:** `NOT STARTED` · **Set:** 2026-08-21
**Previous task:** P0-T1 walking skeleton — `DONE`, see [current_report.md](current_report.md)

---

## Objective

Replace the echo agent with a real local-model turn pipeline: `LLMProvider`, schema-constrained
structured output, intent classification, the pre-router, and the per-call-type context budget.

**Build the accuracy fixture set FIRST** — before the router logic. It is the phase's gating risk and
the only thing that can tell us whether `qwen3.5:0.8b` is good enough.

## Why this task exists

The placement and latency half of [OQ-01](OPEN_QUESTIONS.md#oq-01) is answered: `qwen3.5:0.8b` runs
100% on GPU at 16k context, 45 tok/s, ~730 ms TTFT at a 1200-token prompt. **The accuracy half is
not.** If 0.8b cannot classify intent reliably, nothing else fits this 4 GB card and the design needs
to change — so we find that out with data, in the first week of the phase, not after building on it.

## Context

P0 delivered a working transport, event log, persistence, resume, logging with redaction, the Tauri
shell with a verified Job Object, and a green gate (`uv run python scripts/check.py`, 45 tests).
The seam the echo agent occupies is exactly where the real pipeline goes — `EchoAgent.run()` in
[`src/oracle/core/echo.py`](../src/oracle/core/echo.py) emits the same event sequence the real
runtime must.

Established and not to be re-derived:
- Router model: **`qwen3.5:0.8b`, num_ctx 16384, `think: false`** (mandatory — see OQ-01).
- Context budget is **split by call type**: `route` ≤ 1200 · `answer` ≤ 2400 · `reason` ≤ 8000
  ([AGENT_RUNTIME.md §5](AGENT_RUNTIME.md#5-context-budget)).
- Models live at `D:\ORACLE\models` (`OLLAMA_MODELS`). Ollama client 0.32.5.

## Requirements

1. **Fixture set first** — `tests/fixtures/intent/` with **30 cases**, Russian and English, each with
   the expected intent and (where applicable) expected project and tool. Include the hard cases:
   Russian question about an English codebase, ambiguous project reference, a request that should
   route to `delegate`.
2. `LLMProvider` protocol + `OllamaProvider` + `FakeProvider` (records/replays, so the whole pipeline
   becomes deterministic under test — [TESTING.md §1](TESTING.md#1-the-three-properties-that-make-this-testable)).
3. Structured output: JSON Schema → pydantic validate → **one** repair attempt → deterministic
   fallback. Track and expose `structured_output_failure` rate.
4. Pre-router: slash commands + palette action table, no LLM, ordered deterministic matching.
5. Intent classification against the `Intent` schema; `confidence < 0.55` asks instead of guessing.
6. Context Assembler v1: per-call-type budgets, **real tokenizer** counting, band priority, assertion
   that the budget is never exceeded.
7. Ollama supervision: detect not-running / model-not-pulled, emit `system.degraded`, keep slash
   commands working (the UI already renders a degraded banner).
8. Replace `EchoAgent` with the real pipeline behind the same event contract.

## Constraints

- **No tools, no side effects.** The policy gate does not exist until Phase 2
  ([sequencing rule 2](ROADMAP.md#sequencing-rules)). The agent may talk; it may not act.
- **No new dependency without a line in [TECH_STACK.md](TECH_STACK.md).**
- Keep `FakeProvider` on the test path — no test may require Ollama to be running.
- The gate stays green; new code is typed (`mypy --strict` covers `src/oracle`).

## Acceptance criteria

- [ ] Intent classification **≥ 85%** on the 30-case fixture set.
- [ ] Structured-output failure rate **< 2%** over 100 generations.
- [ ] `route` TTFT **< 900 ms p50**, **< 1.5 s p95** with the model resident.
- [ ] Context never exceeds the per-call-type budget — asserted in a test.
- [ ] With Ollama stopped: slash commands work, the UI shows the degraded banner, nothing hangs.
- [ ] Router model stays resident across consecutive turns (no reload; warm load is 7–14 s).
- [ ] Cancel mid-stream stops generation within 500 ms.
- [ ] `uv run python scripts/check.py` green.

## Relevant files

Create: `src/oracle/llm/{__init__,provider,ollama,fake}.py` · `src/oracle/router/{prerouter,intent,pipeline}.py`
· `src/oracle/context/budget.py` · `tests/fixtures/intent/*.yaml`
Replace: `src/oracle/core/echo.py`
Read first: [AGENT_RUNTIME.md](AGENT_RUNTIME.md) · [TECH_STACK.md §3](TECH_STACK.md#3-local-llm) ·
[OQ-01](OPEN_QUESTIONS.md#oq-01) · [`logs/development/2026-08-21-oq01-router-benchmark.md`](../logs/development/2026-08-21-oq01-router-benchmark.md)

## Dependencies

P0-T1 (done). Ollama running with `qwen3.5:0.8b` pulled (already on disk).

## Risks

| Risk | Mitigation |
|---|---|
| **0.8b too weak on intent accuracy** — the gating risk; nothing else fits 4 GB | Fixtures first, so this surfaces in days not weeks. Fallbacks in order: flash-attn + KV quant and a text-only build ([OQ-10](OPEN_QUESTIONS.md#oq-10)) to try to pull `2b` onto the card; accept `2b` hybrid (~20 tok/s, ~3.4 s TTFT); shift load to the pre-router |
| Prompt fiddling consumes the phase | The fixture suite is the stop condition. When the numbers pass, stop |
| Ollama drops Pascal mid-phase ([OQ-03](OPEN_QUESTIONS.md#oq-03)) | Test the CPU fallback path *in this phase*, not in production |
| Tokenizer mismatch inflates the budget silently | Count with the model's real tokenizer; assert in a test |

## Definition of done

All acceptance criteria pass · gate green · benchmark numbers recorded in `logs/development/` ·
[ADR-0004](DECISIONS.md#adr-0004--two-tier-local-model-router--reasoner) updated with accuracy data ·
[OQ-01](OPEN_QUESTIONS.md#oq-01) fully closed or its fallback chosen and recorded ·
`current_report.md` overwritten · this file updated to **P2-T1**.
