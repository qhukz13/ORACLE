"""OQ-15 — where a routed turn's seconds actually go, and whether any of them are ours.

[OQ-15](../docs/OPEN_QUESTIONS.md#oq-15) records a decomposition and three things worth trying:
`/api/generate` with a pre-rendered prompt instead of `/api/chat`, a llama.cpp server directly, and
trimming the few-shot block. This measures the first and third; the second needs a different server
and is out of scope for a script that talks to Ollama.

**The claim under test is the ~600 ms fixed per-request overhead.** If it is genuinely fixed — paid
before any token is looked at — then no amount of prompt trimming reaches the 900 ms gate and the
only real lever is the pre-router, which already handles the turn in ~5 ms. If instead it scales
with anything, it is not overhead and the decomposition is wrong.

**`think` is off in every row, because the router sets `think=False`
(`router/pipeline.py`).** Qwen3.5 is a thinking model and left on it spends hundreds of tokens
reasoning before it answers — the first run of this script omitted the flag and measured a
configuration nothing in ORACLE uses, with single calls taking tens of seconds. Same rule as
`tests/test_eval_gate_matches_production.py`: a measurement of a setting the product does not ship
measures nothing anybody experiences.

Run: `uv run python -u scripts/measure_turn_latency.py`  (`-u`: Python block-buffers a redirected
stdout, and this repo has already lost a long run's progress to that.)
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any

import httpx

BASE = "http://127.0.0.1:11434"

#: A prompt shaped like the router's: a system line, a few-shot block, then the user's turn. The
#: content does not matter to latency, only its token count, so this is filler with realistic
#: structure rather than the real prompt — which would make this script a second copy of a prompt
#: that lives in `router/`, and copies drift.
FEWSHOT_UNIT = (
    "user: commit my changes in Asterim with message add the feature\n"
    'assistant: {"intent": "modify", "tool": "git.commit"}\n'
)


def prompt_of(shots: int) -> str:
    return (
        "You route a user's turn to one intent and at most one tool.\n"
        + FEWSHOT_UNIT * shots
        + "user: why is Asterim auth broken\nassistant:"
    )


def stats(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "p50": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
        "min": ordered[0],
    }


def timed(client: httpx.Client, path: str, body: dict[str, Any], reps: int) -> dict[str, Any]:
    """Wall-clock per call, plus whatever Ollama says it spent. The gap between them is the
    number this whole script exists to find: time nobody is accounting for."""
    wall: list[float] = []
    load: list[float] = []
    prompt_eval: list[float] = []
    eval_: list[float] = []
    counts: list[int] = []
    for _ in range(reps):
        t0 = time.perf_counter()
        r = client.post(f"{BASE}{path}", json=body, timeout=120.0)
        wall.append((time.perf_counter() - t0) * 1000)
        r.raise_for_status()
        d = r.json()
        load.append(d.get("load_duration", 0) / 1e6)
        prompt_eval.append(d.get("prompt_eval_duration", 0) / 1e6)
        eval_.append(d.get("eval_duration", 0) / 1e6)
        counts.append(d.get("prompt_eval_count", 0))
    s = stats(wall)
    inside = statistics.median(load) + statistics.median(prompt_eval) + statistics.median(eval_)
    return {
        **s,
        "prompt_tokens": statistics.median(counts),
        "load_ms": statistics.median(load),
        "prompt_eval_ms": statistics.median(prompt_eval),
        "eval_ms": statistics.median(eval_),
        "unaccounted_ms": s["p50"] - inside,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:0.8b")
    ap.add_argument("--reps", type=int, default=12)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    client = httpx.Client()
    results: dict[str, Any] = {"model": args.model, "reps": args.reps}

    # The floor of the floor: an HTTP round trip to the same daemon that does no model work at
    # all. Anything above this in the numbers below is Ollama, not the network or the client.
    raw: list[float] = []
    for _ in range(args.reps):
        t0 = time.perf_counter()
        client.get(f"{BASE}/api/tags", timeout=30.0).raise_for_status()
        raw.append((time.perf_counter() - t0) * 1000)
    results["raw_http"] = stats(raw)
    print(f"raw HTTP to the same daemon      p50 {results['raw_http']['p50']:6.1f} ms")

    # Exactly what the router sends, minus the prompt: `think=False` and a zero temperature.
    keep: dict[str, Any] = {"keep_alive": "30m", "think": False}
    opts = {"num_ctx": 4096, "temperature": 0.0}

    # Warm the model so `load_duration` stops dominating; a cold first call is a different
    # question (OQ-17's territory) and would make every row below meaningless.
    client.post(
        f"{BASE}/api/chat",
        json={
            "model": args.model,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            **keep,
            "options": {**opts, "num_predict": 1},
        },
        timeout=300.0,
    ).raise_for_status()

    print()
    print(
        "                                   p50      p95   prompt_tok  load  p_eval   eval  unaccounted"
    )

    def row(name: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        d = timed(client, path, body, args.reps)
        print(
            f"{name:<32} {d['p50']:6.1f}  {d['p95']:6.1f}   {d['prompt_tokens']:>8.0f}"
            f" {d['load_ms']:5.0f}  {d['prompt_eval_ms']:6.1f} {d['eval_ms']:6.1f}"
            f"      {d['unaccounted_ms']:6.1f}"
        )
        return d

    # 1. The claim: a request that does as close to nothing as the API allows, from a 2-token
    #    prompt. Whatever this costs is paid before any real work exists, on every single turn.
    #
    #    `num_predict: 1`, not 0, and that is a bug fix rather than a preference: `/api/generate`
    #    with `raw: true` ignores `num_predict: 0` and generates until it stops on its own — the
    #    first run of this script produced a 16-second row for it and the number meant nothing.
    #    One token costs ~24 ms, which is inside the noise of everything else here.
    results["floor_chat"] = row(
        "chat, 2-token prompt, 1 token out",
        "/api/chat",
        {
            "model": args.model,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            **keep,
            "options": {**opts, "num_predict": 1},
        },
    )
    # `/api/generate` with `raw: true` bypasses the chat template entirely, which is also what
    # suppresses the thinking preamble there — the `think` key rides along harmlessly.
    results["floor_generate"] = row(
        "generate raw, same, 1 token out",
        "/api/generate",
        {
            "model": args.model,
            "prompt": "hi",
            "stream": False,
            "raw": True,
            **keep,
            "options": {**opts, "num_predict": 1},
        },
    )

    # 2. Does the endpoint matter at a realistic prompt size? `/api/generate` with `raw: true`
    #    skips template rendering, which is the mechanism OQ-15 hoped would help.
    # Up to 32 shots (~1000 prompt tokens) on purpose: OQ-15's table puts prompt processing
    # at ~570 ms for a ~900-token few-shot prompt, and a sweep that stopped at 519 could not
    # honestly contradict it.
    for shots in (0, 4, 8, 16, 32):
        text = prompt_of(shots)
        results[f"chat_{shots}shot"] = row(
            f"chat, {shots}-shot prompt, 20 out",
            "/api/chat",
            {
                "model": args.model,
                "messages": [{"role": "user", "content": text}],
                "stream": False,
                **keep,
                "options": {**opts, "num_predict": 20},
            },
        )
        results[f"generate_{shots}shot"] = row(
            f"generate raw, {shots}-shot, 20 out",
            "/api/generate",
            {
                "model": args.model,
                "prompt": text,
                "stream": False,
                "raw": True,
                **keep,
                "options": {**opts, "num_predict": 20},
            },
        )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
