#!/usr/bin/env python
"""The quality gate. `uv run python scripts/check.py`

This is the real implementation; the Makefile delegates here. GNU make is not on this
machine (Git for Windows does not ship it), and a gate that cannot be run is not a gate
— so the canonical entry point is a script, not a Makefile target.

From Phase 2 the security suite joins this list and is not skippable.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "apps" / "desktop"

NPM = shutil.which("npm") or "npm"
UV = shutil.which("uv") or "uv"

Step = tuple[str, list[str], Path]

STEPS: list[Step] = [
    ("ruff format", [UV, "run", "ruff", "format", "--check", "src", "tests"], ROOT),
    ("ruff lint", [UV, "run", "ruff", "check", "src", "tests"], ROOT),
    ("mypy", [UV, "run", "mypy"], ROOT),
    ("tsc", [NPM, "run", "--silent", "typecheck"], UI),
    ("pytest", [UV, "run", "pytest", "-q"], ROOT),
    ("vitest", [NPM, "run", "--silent", "test"], UI),
]

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def run(step: Step, verbose: bool) -> tuple[bool, float, str]:
    name, cmd, cwd = step
    start = time.monotonic()
    proc = subprocess.run(  # noqa: S603 - fixed command list, no shell
        cmd, cwd=cwd, capture_output=not verbose, text=True, encoding="utf-8", errors="replace"
    )
    elapsed = time.monotonic() - start
    output = "" if verbose else ((proc.stdout or "") + (proc.stderr or ""))
    return proc.returncode == 0, elapsed, output


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true", help="stream tool output")
    ap.add_argument("--fail-fast", action="store_true", help="stop at the first failure")
    args = ap.parse_args()

    failures: list[tuple[str, str]] = []
    for step in STEPS:
        name = step[0]
        print(f"  {name:<14} ", end="", flush=True)
        ok, elapsed, output = run(step, args.verbose)
        if ok:
            print(f"{GREEN}ok{RESET} {DIM}{elapsed:.1f}s{RESET}")
        else:
            print(f"{RED}FAIL{RESET} {DIM}{elapsed:.1f}s{RESET}")
            failures.append((name, output))
            if args.fail_fast:
                break

    if failures:
        for name, output in failures:
            print(f"\n{RED}--- {name} ---{RESET}\n{output.strip()[-4000:]}")
        print(f"\n{RED}check: {len(failures)} step(s) failed{RESET}")
        return 1

    print(f"\n{GREEN}check: OK{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
