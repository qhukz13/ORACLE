"""A stub Antigravity CLI: replays a recorded `agy` stream-json fixture, byte for byte.

The same discipline as `stub_claude.py`, and for the same reason (ROADMAP Phase 6):
adapter contract tests run against recorded output — no network, no quota, no cost,
deterministic. Behaviour is driven by environment variables so the adapter under test
spawns this exactly the way it spawns the real binary, argv and all:

    STUB_FIXTURE      path to the .jsonl to replay (required unless --version)
    STUB_STDERR       path to a file whose contents go to stderr (default: none)
    STUB_EXIT         exit code after replaying (default 0)
    STUB_TRUNCATE_AT  emit only the first N lines (default: all)
    STUB_HANG         "1" = sleep after emitting, for cancellation tests

One difference from the Claude stub, and it is the contract's difference: `agy` takes
its prompt as the *value* of `-p` rather than on stdin, so this asserts the prompt is
there — a regression that moved it back to stdin would otherwise pass silently.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    if "--version" in sys.argv:
        print(os.environ.get("STUB_VERSION", "1.1.19"))
        return 0
    if "models" in sys.argv:
        # preflight()'s authentication probe: a real vendor round trip that costs no
        # model tokens. STUB_MODELS_EXIT/STUB_MODELS_STDERR replay the unauthenticated
        # answer, which is the one preflight has to tell apart from "ready".
        sys.stderr.write(os.environ.get("STUB_MODELS_STDERR", ""))
        code = int(os.environ.get("STUB_MODELS_EXIT", "0"))
        if code == 0:
            print("gemini-3.1-pro-high\tGemini 3.1 Pro (High)")
        return code
    if "-p" not in sys.argv or sys.argv.index("-p") == len(sys.argv) - 1:
        sys.stderr.write("stub_agy: the prompt must be the value of -p (INTEGRATIONS.md 5)\n")
        return 2
    stderr_path = os.environ.get("STUB_STDERR")
    if stderr_path:
        sys.stderr.write(Path(stderr_path).read_text(encoding="utf-8"))
        sys.stderr.flush()
    fixture = os.environ.get("STUB_FIXTURE")
    if fixture:
        lines = Path(fixture).read_text(encoding="utf-8").splitlines(keepends=True)
        limit = int(os.environ.get("STUB_TRUNCATE_AT", len(lines)))
        for line in lines[:limit]:
            sys.stdout.write(line)
        sys.stdout.flush()
    if os.environ.get("STUB_HANG") == "1":
        time.sleep(300)
    return int(os.environ.get("STUB_EXIT", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
