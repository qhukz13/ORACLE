"""`python -m oracle.mcp` — the entry point a delegate's `--mcp-config` names."""

from __future__ import annotations

import asyncio
import sys

from oracle.mcp.bridge import main

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
