# 2026-08-21 — OQ-01: router model benchmark on GTX 1050 Ti

Resolves the placement/latency half of [OQ-01](../../docs/OPEN_QUESTIONS.md#oq-01).
**The accuracy half is still open** — it needs the fixture set, which is Phase 1 code.

## Setup

```
GPU              GTX 1050 Ti · 4096 MiB · compute 6.1 · driver 582.28
Ollama           0.32.5, OLLAMA_MODELS=D:\ORACLE\models
Models           qwen3.5:0.8b (1.0 GB), qwen3.5:2b (2.7 GB)
Defaults         no flash-attention / KV-quant env vars set (Ollama defaults)
Method           /api/generate, stream=false, warm-up call before each measurement
```

## Result 1 — placement: does it fit?

| model | num_ctx | placement | notes |
|---|---|---|---|
| **qwen3.5:0.8b** | 8192 | **100% GPU** | |
| **qwen3.5:0.8b** | 16384 | **100% GPU** | still fully resident |
| qwen3.5:2b | 4096 | 36%/64% CPU/GPU | |
| qwen3.5:2b | 8192 | 36%/64% CPU/GPU | **same split as 4096** |

`ollama ps` reported 3.1 GB for `2b`; `nvidia-smi` showed 3770 / 4096 MiB in use.

**The split does not change between 4k and 8k context.** That means the *weights* don't fit, not the
KV cache — so shrinking the context window cannot rescue `2b`. Lowering context is not a lever here.

**→ `qwen3.5:0.8b` is the router.** The [ADR-0004](../../docs/DECISIONS.md#adr-0004--two-tier-local-model-router--reasoner)
arithmetic (2.7 GB weights + 0.46 GB q8 KV ≈ 3.2 GB inside ~3.5 GB usable) was **too optimistic** — it
ignored Ollama's compute buffers and its conservative headroom policy. Measurement beat arithmetic,
which is why this experiment was gated ahead of the router code.

## Result 2 — throughput (2371-token prompt, realistic router size)

| model | placement | prompt eval | generation |
|---|---|---|---|
| qwen3.5:0.8b @ 16k | 100% GPU | 1566 ms | **45.4 tok/s** |
| qwen3.5:2b @ 8k | 36%/64% | 3352 ms | **20.4 tok/s** |

Partial CPU offload costs `2b` ~55% of its generation speed and roughly doubles prompt latency.

## Result 3 — TTFT vs prompt size (0.8b, 100% GPU) ← **the important one**

| prompt tokens | prompt eval (ms) | prompt tok/s |
|---|---|---|
| 227 | 173 | 1313 |
| 627 | 421 | 1490 |
| 1227 | **726** | 1690 |
| 2427 | **1168** | 2078 |
| 4827 | 2216 | 2179 |

Near-linear, ~1300→2200 tok/s (throughput improves with batch size). Extrapolated: a full **8k-token
prompt ≈ 3.7 s of prompt processing alone.**

**Prompt processing, not generation, is the TTFT bottleneck on Pascal.** Generation at 45 tok/s is
fine; feeding the model is what costs.

## Result 4 — Qwen3.5 is a thinking model, and it matters

First smoke test, `qwen3.5:2b`, prompt "Reply with exactly: hello":

```
eval_count : 229 tokens
response   : ""        ← empty
```

229 tokens burned reasoning about how to say "hello", with the visible `response` field empty because
the output went to the thinking channel. Passing **`think: false`** fixed it: 2 tokens, `response`
= `"hello"`.

**→ Every router call must set `think: false`.** Undocumented in the design until now. Left on, it
would have destroyed TTFT, blown the token budget, and returned empty strings that look like a
provider bug.

## Design consequences

1. **Router = `qwen3.5:0.8b` @ 16k context, 100% GPU resident, `think: false`.**
2. **The context budget must be split by call type**, not one global 8k number. A 1200-token router
   prompt costs ~730 ms; an 8k one costs ~3.7 s. The router cannot afford the documented budget.
   → `router` ≤ 2000 tok (~1.1 s TTFT) · `reason/summarize` ≤ 8k, occasional.
3. **`qwen3.5:2b` is not the router** but remains a candidate reasoner (20 tok/s hybrid is usable for
   occasional deliberate work).
4. **Model load is 7–14 s** from D: even warm — confirms ADR-0004's "keep the router resident"; a
   per-turn reload would make the system feel broken.
5. Tool schemas in every prompt are now measurably expensive: ~1200 tokens of tool descriptions
   ≈ 730 ms of latency **per turn**. Intent-based tool pre-filtering
   ([TOOLS.md rule 2](../../docs/TOOLS.md#rule-2--fewer-tools-than-you-think)) moves from
   "good hygiene" to "load-bearing".

## Not yet answered

- **Accuracy.** Intent classification and tool-selection rates for `0.8b` are unmeasured — needs the
  30-case fixture set (Phase 1). **If 0.8b proves too weak, there is no fallback that fits this GPU**,
  and the options become: accept `2b` at 36% CPU offload (~20 tok/s, ~3.4 s TTFT), or restructure so
  the pre-router carries more load. Flagged as the main Phase 1 risk.
- **Flash attention / KV quantization** (`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`) were
  not tested. Irrelevant for `0.8b` (already 100% GPU at 16k), but they are the one lever that might
  pull `2b` fully onto the card. Worth one experiment before writing off `2b`.
- Whether a text-only Qwen3.5 build exists ([OQ-10](../../docs/OPEN_QUESTIONS.md#oq-10)) — these tags
  include a vision tower we never use, and dropping it is the other lever for `2b`.

## Dead ends

- **Shrinking `num_ctx` to make `2b` fit.** 4096 and 8192 produce an identical 36%/64% split. The
  weights are the problem. Do not retry this.
- **Measuring tok/s with a 2-token generation.** First attempts produced 21–72 tok/s of pure noise.
  Use ≥ 80 generated tokens and a warm-up call.
