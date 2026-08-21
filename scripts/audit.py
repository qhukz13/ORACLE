#!/usr/bin/env python
"""Verify the audit chain.  `uv run python scripts/audit.py verify`

The audit log is only worth having if tampering is detectable, so verification is a
command anyone can run rather than an internal detail.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oracle.config import get_settings
from oracle.policy.audit import AuditLog

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["verify", "tail"])
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("-n", type=int, default=20)
    args = ap.parse_args()

    path = args.path or get_settings().audit_path
    if not path.exists():
        print(f"{DIM}no audit log at {path} (nothing has been executed yet){RESET}")
        return 0

    log = AuditLog(path)

    if args.command == "tail":
        for rec in log.records()[-args.n :]:
            print(
                f"  {rec['seq']:>5} {rec['ts']}  {rec.get('tool', '-'):<16} "
                f"{rec.get('decision', '-'):<18} {rec.get('rule', '')}"
            )
        return 0

    breaks = log.verify()
    print(f"{path}  ({log.seq} records)")
    if not breaks:
        print(f"{GREEN}chain intact{RESET}")
        return 0
    for b in breaks:
        print(f"{RED}  seq {b.seq}: {b.detail}{RESET}")
        print(f"{DIM}    expected {b.expected[:24]}…  found {b.found[:24]}…{RESET}")
    print(f"\n{RED}AUDIT CHAIN BROKEN — {len(breaks)} problem(s){RESET}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
