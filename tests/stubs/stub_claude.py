"""A stub Claude CLI: replays a recorded stream-json fixture, byte for byte.

The testing rule from ROADMAP.md Phase 6: adapter contract tests run against recorded
output — no network, no cost, deterministic. Behaviour is driven by environment
variables so the adapter under test spawns this exactly the way it spawns the real
binary, argv and all:

    STUB_FIXTURE      path to the .jsonl to replay (required unless --version)
    STUB_EXIT         exit code after replaying (default 0)
    STUB_TRUNCATE_AT  emit only the first N lines (default: all)
    STUB_HANG         "1" = sleep after emitting, for cancellation tests
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    if "--version" in sys.argv:
        print("2.1.238 (Claude Code)")
        return 0
    lines = Path(os.environ["STUB_FIXTURE"]).read_text(encoding="utf-8").splitlines(keepends=True)
    limit = int(os.environ.get("STUB_TRUNCATE_AT", len(lines)))
    for line in lines[:limit]:
        sys.stdout.write(line)
    sys.stdout.flush()
    if os.environ.get("STUB_HANG") == "1":
        time.sleep(300)
    return int(os.environ.get("STUB_EXIT", "0"))


if __name__ == "__main__":
    raise SystemExit(main())
