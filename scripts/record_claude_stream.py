#!/usr/bin/env python
"""Record the Claude Code CLI stream-json contract as replayable fixtures (P6-T1 req 1).

    uv run python scripts/record_claude_stream.py            # show payload, confirm, record
    uv run python scripts/record_claude_stream.py --dry-run  # show payload only, send nothing

One supervised live run of `claude -p --output-format stream-json` on a trivial,
self-verifying task. The captured stdout becomes `tests/fixtures/claude_stream/`, which the
adapter's contract tests replay through a stub CLI — deterministic, offline, free. This
script *is* the egress preview until the P6-T2 UI exists: it prints every byte that will
leave the machine and refuses to run until that payload is confirmed at the prompt.

Auth is the machine's existing Claude subscription login — no key, no token. `--bare` is
deliberately absent: measured 2026-08-23, it ignores the OAuth login and is unusable here
(INTEGRATIONS.md §3). Its isolation is replaced by `--setting-sources user` +
`--strict-mcp-config`, and in production by the worktree scrub; the recording workdir is
created fresh by this script, so there is nothing to scrub.

What it verifies against the doc, on the installed CLI version:

  * `system/init` arrives (not necessarily first — user-level hooks precede it)
  * assistant / user events stream; `result` carries `total_cost_usd` and is the
    terminal *semantic* event — `system/*` housekeeping may trail it
  * exit code 0 on success; the produced file proves the run actually did the work
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures/claude_stream"
WORKDIR = ROOT / ".oracle/tmp/record-smoke"

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"

#: The task is trivial on purpose: the contract is what is being recorded, not the model.
#: It still exercises a Read and a Write tool call, so the fixture contains real
#: `tool_use` events, and the output file makes success checkable without trusting
#: the agent's own report — the same principle collect() will rely on.
HELLO = "the quick brown fox jumps over the lazy dog\n"
PROMPT = (
    "Read hello.txt in this directory, count the words in it, and write exactly that "
    "number (digits only, single line) to count.txt in the same directory. Do nothing else."
)
RESULT_SCHEMA = {
    "type": "object",
    "properties": {"word_count": {"type": "integer"}},
    "required": ["word_count"],
}


def build_command() -> list[str]:
    return [
        "claude",
        "-p",
        PROMPT,
        "--setting-sources",
        "user",
        "--strict-mcp-config",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(RESULT_SCHEMA),
        "--allowedTools",
        "Read,Write",
        "--permission-mode",
        "dontAsk",
        "--add-dir",
        str(WORKDIR),
    ]


def preview(cmd: list[str]) -> None:
    print(f"\n{B}egress preview{X} — everything below leaves this machine (api.anthropic.com)\n")
    print(f"  {Y}prompt:{X} {PROMPT}")
    print(f"  {Y}files the agent can read:{X} {WORKDIR / 'hello.txt'}")
    print(f"    {D}{HELLO.rstrip()}{X}")
    print(f"  {Y}command:{X}")
    for part in cmd:
        print(f"    {D}{part}{X}")
    print(f"\n  allowed tools: Read, Write · permission mode: dontAsk · scope: {WORKDIR}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show the payload, send nothing")
    args = parser.parse_args()

    version = subprocess.run(
        ["claude", "--version"],  # noqa: S607 - resolved from PATH like everywhere else
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]
    cmd = build_command()
    preview(cmd)

    if args.dry_run:
        print(f"\n{Y}dry run{X} — nothing sent.")
        return 0
    if input(f"\nSend this payload? Type {G}yes{X} to proceed: ").strip().lower() != "yes":
        print("Cancelled. Nothing sent.")
        return 1

    if WORKDIR.exists():
        shutil.rmtree(WORKDIR)
    WORKDIR.mkdir(parents=True)
    (WORKDIR / "hello.txt").write_text(HELLO, encoding="utf-8")
    FIXTURES.mkdir(parents=True, exist_ok=True)
    out_path = FIXTURES / f"smoke-v{version}.jsonl"

    started = time.perf_counter()
    proc = subprocess.run(  # noqa: S603 - fixed command list, previewed and confirmed above
        cmd, cwd=WORKDIR, capture_output=True, text=True, encoding="utf-8"
    )
    elapsed = time.perf_counter() - started

    out_path.write_text(proc.stdout, encoding="utf-8")
    if proc.stderr.strip():
        out_path.with_suffix(".stderr.txt").write_text(proc.stderr, encoding="utf-8")

    # Contract checks — each one is a claim INTEGRATIONS.md §3 makes about the stream.
    events = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    kinds = [e.get("type") for e in events]
    init = next(
        (e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None
    )
    result = next((e for e in events if e.get("type") == "result"), None)
    count_file = WORKDIR / "count.txt"
    produced = count_file.read_text(encoding="utf-8").strip() if count_file.exists() else None

    print(
        f"\n{B}recorded{X}  {out_path.relative_to(ROOT)}  {D}{len(events)} events, {elapsed:.1f}s{X}"
    )
    for label, ok, detail in [
        ("exit code 0", proc.returncode == 0, str(proc.returncode)),
        (
            "system/init present",
            bool(init),
            f"session {init.get('session_id') if init else '—'}",
        ),
        (
            "result is semantic end",
            bool(result) and all(k == "system" for k in kinds[kinds.index("result") + 1 :]),
            f"cost ${result.get('total_cost_usd', '?')}" if result else "missing",
        ),
        (
            "work verifiably done",
            produced == str(len(HELLO.split())),
            f"count.txt = {produced!r}, expected {len(HELLO.split())}",
        ),
    ]:
        mark = f"{G}ok{X}" if ok else f"{R}FAIL{X}"
        print(f"  {label:<22} {mark}  {D}{detail}{X}")

    failed = proc.returncode != 0 or not init or not result or produced != str(len(HELLO.split()))
    if failed:
        print(
            f"\n{R}The contract drifted or the run failed — read the fixture before writing adapter code.{X}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
