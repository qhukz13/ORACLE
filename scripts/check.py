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
SHELL = UI / "src-tauri"

NPM = shutil.which("npm") or "npm"
UV = shutil.which("uv") or "uv"
CARGO = shutil.which("cargo")

Step = tuple[str, list[str], Path]

STEPS: list[Step] = [
    ("ruff format", [UV, "run", "ruff", "format", "--check", "src", "tests"], ROOT),
    ("ruff lint", [UV, "run", "ruff", "check", "src", "tests"], ROOT),
    ("mypy", [UV, "run", "mypy"], ROOT),
    ("tsc", [NPM, "run", "--silent", "typecheck"], UI),
    # `-v`, not `-q`, and the reason is a failure mode this repo has hit three times
    # (2026-08-26, twice on 2026-09-10): under a CPU-starved gate run a test hangs, and
    # `timeout_method=thread` kills the process before pytest prints a summary. With `-q`
    # what is captured is a stack dump that **names no test**, which is exactly what
    # pyproject's timeout comment means by "expensive to bisect". Verbose prints each
    # nodeid as it starts, so the hung one is the last line before the dump. Output is
    # captured and shown only on failure, so a green run costs nothing for this.
    ("pytest", [UV, "run", "pytest", "-v", "--ignore=tests/security"], ROOT),
    # Merge gate from Phase 2 on (docs/TESTING.md#3). Run as its own step so a
    # security regression is never buried in a wall of ordinary test output.
    ("security", [UV, "run", "pytest", "-v", "tests/security"], ROOT),
    ("vitest", [NPM, "run", "--silent", "test"], UI),
]

# The Tauri shell. Added 2026-09-10 with ADR-0030, which moved real logic into it — the shell
# now decides whether to attach to a resident daemon or start one it owns, and gets that wrong
# by attaching to any process that happens to hold the port. Rust was outside the gate until
# then, which was survivable while `backend.rs` was a `Command::spawn`; it is not survivable for
# a decision with four tests, because a test no gate runs is a test that rots. `dependsOn` is
# this repo's own worked example of that.
#
# **Skipped loudly, never silently, when cargo is absent.** A gate that quietly drops a step on
# a machine missing a toolchain reports green for something it did not check, which is the one
# thing a gate must never do.
if CARGO is not None:
    STEPS.append(("cargo test", [CARGO, "test", "--quiet"], SHELL))

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def run(step: Step, verbose: bool) -> tuple[bool, float, str]:
    _name, cmd, cwd = step
    start = time.monotonic()
    proc = subprocess.run(  # noqa: S603 - fixed command list, no shell
        cmd, cwd=cwd, capture_output=not verbose, text=True, encoding="utf-8", errors="replace"
    )
    elapsed = time.monotonic() - start
    output = "" if verbose else ((proc.stdout or "") + (proc.stderr or ""))
    return proc.returncode == 0, elapsed, output


#: How much of a failing step's output to show. The tail carries the summary for an
#: ordinary failure; the head carries the killed test's name when a hang is what failed.
#: Showing only the tail lost that twice on 2026-09-10.
REPORT_TAIL = 6000
REPORT_HEAD = 1500


def report(output: str) -> str:
    """A failing step's output: the end, plus the beginning when they differ."""
    text = output.strip()
    if len(text) <= REPORT_TAIL + REPORT_HEAD:
        return text
    elided = len(text) - REPORT_HEAD - REPORT_TAIL
    head, tail = text[:REPORT_HEAD], text[-REPORT_TAIL:]
    middle = f"{DIM}   ... {elided} characters elided ...{RESET}"
    return chr(10).join([head, middle, tail])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true", help="stream tool output")
    ap.add_argument("--fail-fast", action="store_true", help="stop at the first failure")
    args = ap.parse_args()

    # The gate reports failures by printing the failing tool's output, and that output routinely
    # contains characters cp1252 cannot encode — pytest's own box-drawing, a source file's em
    # dash, or the U+FFFD that `errors="replace"` already substituted upstream. On this console
    # that made `print()` raise, so **the gate crashed precisely when it had something to say**
    # and the failing step's output was lost. A gate that cannot report a failure is worse than a
    # slow one; it looks like a broken script rather than a broken build.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    if CARGO is None:
        # Not a failure: `cargo` is not needed to work on the Python or the web UI, and
        # demanding it would make the gate unrunnable for most of the repo. But the reader has
        # to know the shell went unchecked, or "check: OK" is a claim about code nobody ran.
        print(f"  {DIM}cargo not found — the Tauri shell was NOT checked (ADR-0030){RESET}")

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
            print(f"\n{RED}--- {name} ---{RESET}\n{report(output)}")
        print(f"\n{RED}check: {len(failures)} step(s) failed{RESET}")
        return 1

    print(f"\n{GREEN}check: OK{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
