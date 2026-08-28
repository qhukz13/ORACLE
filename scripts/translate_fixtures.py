#!/usr/bin/env python
"""Translate the retrieval fixtures' Russian questions with the resident router model.

    python scripts/translate_fixtures.py --out logs/measurements/oq18-translations.json

[OQ-18](docs/OPEN_QUESTIONS.md#oq-18) measured query translation at its **ceiling**: the
`q_en` values in `tests/fixtures/retrieval/cases.yaml` are human translations, and they
buy +12.0 points of Russian recall@5. Whether the 0.8B model ORACLE actually keeps
resident reaches that ceiling is a separate and much cheaper question, and this script is
the half of it that costs a model call.

Two properties make the answer worth having:

  * **It calls the shipped translator.** `oracle.rag.translate.translate_to_english` is
    the function the packet path would use, prompt and schema and rejection rules
    included. This project has now had four measurements describe code it does not run
    (`logs/development/2026-08-26-oq18-chunking.md`); a script with its own prompt would
    have been the fifth.
  * **It writes the translations down.** The scoring half (`eval_embeddings.py
    --translations`) reads this file, so the arm is reproducible without a GPU and the
    model's actual output is auditable rather than summarised.

Needs Ollama running with the router model. Nothing else.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = ROOT / "tests/fixtures/retrieval/cases.yaml"


async def run(model: str, out: Path, only_translatable: bool) -> int:
    from oracle.llm.ollama import OllamaProvider
    from oracle.llm.types import ProviderUnavailable
    from oracle.rag.translate import looks_translatable, translate_to_english

    cases = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))["cases"]
    targets = [c for c in cases if c.get("q_en")]
    if only_translatable:
        targets = [c for c in targets if looks_translatable(c["q"])]

    provider = OllamaProvider(model=model)
    try:
        await provider.preflight()
    except ProviderUnavailable as exc:
        print(f"ollama: {exc.reason} — {exc.remedy}")
        return 2

    rows: list[dict[str, object]] = []
    failures = 0
    for case in targets:
        t0 = time.perf_counter()
        english = await translate_to_english(case["q"], provider)
        ms = (time.perf_counter() - t0) * 1000
        if english is None:
            failures += 1
        rows.append(
            {
                "id": case["id"],
                "q": case["q"],
                "q_en_human": case["q_en"],
                "q_en_model": english,
                "ms": round(ms, 1),
            }
        )
        mark = "  " if english else "!!"
        print(f"{mark} {case['id']:<24} {ms:7.0f} ms  {english or '<none>'}")

    await provider.aclose()

    latencies = sorted(float(r["ms"]) for r in rows)  # type: ignore[arg-type]
    payload = {
        "model": model,
        "cases": len(rows),
        "failures": failures,
        "ms_p50": latencies[len(latencies) // 2] if latencies else 0.0,
        "ms_max": latencies[-1] if latencies else 0.0,
        "translations": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(rows)} translated, {failures} failed, p50 {payload['ms_p50']:.0f} ms → {out}")
    return 1 if failures == len(rows) else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:0.8b", help="the resident router model")
    ap.add_argument("--out", default="logs/measurements/oq18-translations.json")
    ap.add_argument(
        "--all",
        action="store_true",
        help="translate every case with a q_en, not only the ones `looks_translatable` "
        "would send to the model on the shipped path",
    )
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    return asyncio.run(run(args.model, ROOT / args.out, not args.all))


if __name__ == "__main__":
    sys.exit(main())
