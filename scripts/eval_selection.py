"""Measure tool-selection accuracy against the real router model.

Not a test — it needs Ollama, and the suite must stay hermetic (docs/TESTING.md). This
is the counterpart to `eval_intent.py`: it answers "does the 0.8B model actually pick
the right tool", which is a *measurement*, not an assertion.

It exists because of a real miss. Live, `"commit my changes in Asterim with message X"`
selected `git.add` and no commit was made — a wrong-but-plausible choice, which is the
failure mode a small model has and the one nobody notices until it matters.

Run:  uv run python scripts/eval_selection.py
      uv run python scripts/eval_selection.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oracle.llm.ollama import OllamaProvider  # noqa: E402
from oracle.llm.types import ProviderUnavailable  # noqa: E402
from oracle.router.selection import ToolSelector  # noqa: E402
from oracle.tools import build_registry  # noqa: E402

PROJECT = Path("C:/Projects/Asterim")

#: (request, intent, expected tool). `None` means "should choose nothing".
#: Bilingual on purpose — the machine this runs on is used in both languages, and the
#: intent classifier degraded most on exactly these pairs.
CASES: list[tuple[str, str, str | None]] = [
    # the miss this file was written for, and its neighbours
    ("commit my changes in Asterim with message add the feature", "modify", "git.commit"),
    ("commit what I staged, message: fix the login redirect", "modify", "git.commit"),
    ("закоммить мои изменения с сообщением почини редирект", "modify", "git.commit"),
    ("stage everything in Asterim", "modify", "git.add"),
    ("add my files to the index", "modify", "git.add"),
    ("push my changes to origin", "modify", "git.push"),
    ("отправь изменения на сервер", "modify", "git.push"),
    # tests
    ("run the tests for Asterim", "run", "dev.run_tests"),
    ("запусти тесты", "run", "dev.run_tests"),
    ("run only the login tests", "run", "dev.run_tests"),
    ("build the project", "run", "dev.build"),
    ("lint it", "run", "dev.lint"),
    # status
    ("is Asterim clean", "status", "git.status"),
    ("what branch am I on", "status", "git.status"),
    ("what changed since the last commit", "status", "git.diff"),
    ("show me the recent commits", "status", "git.log"),
    ("how much RAM is free", "status", "sys.info"),
    ("what is in the Asterim folder", "status", "fs.list"),
    # things no offered tool does — the model must be able to say no
    ("delete all the log files", "modify", None),
    ("send this to the printer", "run", None),
]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--model", default="qwen3.5:0.8b")
    args = parser.parse_args()

    provider = OllamaProvider(model=args.model, num_ctx=16384)
    try:
        await provider.preflight()
    except ProviderUnavailable as exc:
        print(f"ollama unavailable: {exc.reason}")
        return 2

    selector = ToolSelector(build_registry(), provider)
    hits = 0
    latencies: list[float] = []
    misses: list[str] = []

    try:
        for text, intent, expected in CASES:
            started = time.perf_counter()
            selection = await selector.select(text, intent, project_path=PROJECT)
            latencies.append((time.perf_counter() - started) * 1000)
            got = selection.tool
            ok = got == expected
            hits += ok
            mark = "ok  " if ok else "MISS"
            if not ok:
                misses.append(f"{text!r}: expected {expected}, got {got} ({selection.reason})")
            if args.verbose or not ok:
                print(f"{mark} [{intent:9}] {text[:52]:52} -> {got} (want {expected})")
    finally:
        await provider.aclose()

    latencies.sort()
    total = len(CASES)
    print()
    print(f"accuracy: {hits}/{total} = {hits / total:.1%}")
    print(f"latency:  p50 {latencies[len(latencies) // 2]:.0f} ms  p95 {latencies[-1]:.0f} ms")
    if misses:
        print("\nmisses:")
        for m in misses:
            print(" -", m)
    return 0 if hits == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
