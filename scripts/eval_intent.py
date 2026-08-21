#!/usr/bin/env python
"""Intent-accuracy evaluation against a real local model.

    uv run python scripts/eval_intent.py [--model qwen3.5:0.8b] [--repeat 1]

This is the gate for P1-T1 (>= 85%) and the answer to the open half of OQ-01. It is a
*measurement script*, not a unit test: it needs Ollama running and it costs real
inference time, so it stays out of `scripts/check.py`.

Prints a per-case table and a confusion summary, because the aggregate number alone
does not tell you whether the model is failing on Russian, on `delegate`, or on
project resolution — and those have different fixes.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oracle.llm.ollama import OllamaProvider
from oracle.llm.structured import StructuredOutputError, StructuredStats
from oracle.logsink import configure
from oracle.router.intent import IntentClassifier
from oracle.router.prerouter import PreRouteKind, pre_route

PIPELINES = frozenset({"asterim-check", "oracle-selfcheck"})

FIXTURES = Path(__file__).resolve().parent.parent / "tests/fixtures/intent/cases.yaml"
PROJECTS = [
    "Asterim",
    "AsterimDesign",
    "GameRecs",
    "GrowAMonster",
    "MonsterGarden",
    "Source2DemViewer",
    "asterim-pipeline",
]

G, R, Y, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:0.8b")
    ap.add_argument("--repeat", type=int, default=1, help="runs per case, to see variance")
    ap.add_argument("--num-ctx", type=int, default=16384)
    args = ap.parse_args()

    configure(None, "error")  # keep the table readable

    cases: list[dict[str, Any]] = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))["cases"]
    provider = OllamaProvider(model=args.model, num_ctx=args.num_ctx)
    await provider.preflight()

    stats = StructuredStats()
    clf = IntentClassifier(provider, projects=PROJECTS, stats=stats)

    # Warm-up so the first case doesn't absorb the 7-14 s model load.
    await clf.classify("hello")

    intent_ok = project_ok = clarify_ok = prerouted = 0
    total = 0
    latencies: list[float] = []
    prompt_tokens: list[int] = []
    failures: list[tuple[str, str]] = []
    usages: list[Any] = []
    confusion: dict[tuple[str, str], int] = {}

    print(
        f"\n{D}model={args.model} ctx={args.num_ctx} cases={len(cases)} repeat={args.repeat}{X}\n"
    )
    print(f"{'case':<24} {'expected':<12} {'got':<12} {'proj':<14} {'conf':>6} {'ms':>6}")
    print("-" * 82)

    for case in cases:
        for _ in range(args.repeat):
            total += 1
            t0 = time.perf_counter()
            pre = pre_route(case["text"], pipelines=PIPELINES)
            if pre.kind is not PreRouteKind.NONE and pre.kind is not PreRouteKind.COMMAND:
                got = {
                    PreRouteKind.HALT: "control",
                    PreRouteKind.DELEGATE: "delegate",
                    PreRouteKind.PIPELINE: "pipeline",
                }[pre.kind]
                # The deterministic path handles this; the model is never consulted.
                # Measuring the classifier alone here would misrepresent the system.
                prerouted += 1
                ok = case.get("intent") == got
                intent_ok += ok
                project_ok += case.get("project") is None
                clarify_ok += not case.get("clarify")
                mark = G if ok else R
                print(
                    f"{case['id']:<24} {case.get('intent')!s:<12} {mark}{got:<12}{X} "
                    f"{'-':<14} {'--':>6} {'0':>6}  {D}pre-router: {pre.reason}{X}"
                )
                continue
            try:
                result = await clf.classify(case["text"])
            except StructuredOutputError as exc:
                failures.append((case["id"], str(exc)[:80]))
                print(f"{case['id']:<24} {case['intent']!s:<12} {R}STRUCT-FAIL{X}")
                continue
            elapsed = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed)
            prompt_tokens.append(result.tokens_used)
            if result.usage:
                usages.append(result.usage)

            exp_intent = case.get("intent")
            got_intent = result.intent.intent
            exp_proj = case.get("project")
            got_proj = result.resolved_project
            wants_clarify = bool(case.get("clarify"))

            i_ok = exp_intent is None or got_intent == exp_intent
            p_ok = exp_proj == got_proj
            c_ok = (not wants_clarify) or result.needs_clarification

            intent_ok += i_ok
            project_ok += p_ok
            clarify_ok += c_ok

            if not i_ok and exp_intent:
                confusion[(exp_intent, got_intent)] = confusion.get((exp_intent, got_intent), 0) + 1

            mark = G if (i_ok and p_ok and c_ok) else (Y if i_ok else R)
            flag = "" if c_ok else f" {Y}(should clarify){X}"
            print(
                f"{case['id']:<24} {exp_intent!s:<12} {mark}{got_intent:<12}{X} "
                f"{got_proj!s:<14} {result.intent.confidence:>6} {elapsed:>6.0f}{flag}"
            )

    print("-" * 82)
    pct = lambda n: 100.0 * n / total if total else 0.0  # noqa: E731
    print(f"\n  intent accuracy    {pct(intent_ok):5.1f}%   ({intent_ok}/{total})   gate: 85%")
    print(f"  project accuracy   {pct(project_ok):5.1f}%   ({project_ok}/{total})")
    print(f"  clarify behaviour  {pct(clarify_ok):5.1f}%   ({clarify_ok}/{total})")
    print(f"  pre-routed         {prerouted} case(s) resolved with no model call")
    if latencies:
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(f"\n  route latency      p50 {p50:.0f} ms   p95 {p95:.0f} ms   gate: 900/1500")
        print(f"  route prompt       {statistics.mean(prompt_tokens):.0f} tokens avg (budget 1200)")
        if usages:
            print(
                f"  breakdown          prompt_eval {statistics.mean([u.prompt_eval_ms for u in usages]):.0f} ms | "
                f"gen {statistics.mean([u.eval_ms for u in usages]):.0f} ms | "
                f"out {statistics.mean([u.completion_tokens for u in usages]):.0f} tok | "
                f"real prompt {statistics.mean([u.prompt_tokens for u in usages]):.0f} tok"
            )
    print(
        f"\n  structured output  attempts={stats.attempts} repairs={stats.repairs} "
        f"failures={stats.failures} rate={stats.failure_rate:.2%}   gate: <2%"
    )

    if confusion:
        print(f"\n  {D}confusions (expected -> got){X}")
        for (exp, got), n in sorted(confusion.items(), key=lambda kv: -kv[1]):
            print(f"    {exp:<12} -> {got:<12} x{n}")
    for cid, err in failures:
        print(f"    {R}{cid}{X}: {err}")

    await provider.aclose()
    passed = pct(intent_ok) >= 85.0 and stats.failure_rate < 0.02
    print(f"\n  {(G + 'PASS' + X) if passed else (R + 'FAIL' + X)}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
