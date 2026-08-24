#!/usr/bin/env python
"""Record the Antigravity CLI (`agy`) contract as replayable fixtures (P6-T5 req 2).

    uv run python scripts/record_agy_stream.py --dry-run       # show the payload, send nothing
    uv run python scripts/record_agy_stream.py                 # confirm, then record every step
    uv run python scripts/record_agy_stream.py --step smoke    # one step at a time

Fixtures before function, exactly as `record_claude_stream.py` did for Claude: the
adapter is written against bytes this machine actually produced, and the contract tests
replay them through a stub CLI — offline, deterministic, free. This script *is* the
egress preview for the recording runs: it prints every byte that will leave the machine
and refuses to send until that payload is confirmed at the prompt.

Three of the four steps measure things OQ-05 left explicitly untested, and that
`preflight()` and `cancel()` cannot be written honestly without:

  smoke   the happy path with `--output-format stream-json`, and — because ORACLE does
          NOT pass `--dangerously-skip-permissions` — what a *soft-denied* write looks
          like in the stream. Whether the write lands or is denied, the recording is the
          finding (INTEGRATIONS.md §5).
  unauth  the CLI with its credentials hidden (env redirected to an empty home, the real
          config untouched): does it fail cleanly in a non-TTY, or hang? `preflight()`
          depends on the answer, and OQ-05 marked it UNKNOWN.
  cancel  SIGINT-equivalent mid-run: what stdout carried, what exit code came back, and
          what remains on disk. Cancellation semantics are an acceptance criterion.
  schema  one `--json-schema` call, small, to pin the structured-result shape the
          planning measurement (`verify_agy_planning.py`) then leans on at scale.

Auth is the machine's existing Antigravity login — no key, no token. `--sandbox` is not
used: isolation here is the fresh workdir this script creates, and in production the
scrubbed worktree (INTEGRATIONS.md §7).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures/agents/antigravity"
WORKROOT = ROOT / ".oracle/tmp/record-agy"

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"

#: Trivial on purpose: the contract is what is being recorded, not the model. It still
#: forces a read and a write, so the fixture carries real tool activity, and the output
#: file makes success checkable without trusting the agent's own report.
HELLO = "the quick brown fox jumps over the lazy dog\n"
WORD_COUNT = len(HELLO.split())
SMOKE_PROMPT = (
    "Read hello.txt in this directory, count the words in it, and write exactly that "
    "number (digits only, single line) to count.txt in the same directory. Do nothing else."
)
#: Two dead ends are baked into this prompt. Asking for a file (attempt 1) never got past
#: the permission gate — see `smoke` — so the run died before the interrupt. Asking for
#: one long response (attempt 2) hit a vendor-side "timeout waiting for response" at
#: ~20 s, which is a finding of its own but still measured nothing about SIGINT. Many
#: *short* read-only steps keep the run genuinely busy with no approval and no long
#: single generation in the way.
CANCEL_PROMPT = (
    "Read hello.txt in this directory. Then read it again, and again - read it a total of "
    "eight separate times. After each read, reply with one short line stating how many "
    "words the file contains. Create no files and run no commands."
)
SCHEMA_PROMPT = (
    "Read hello.txt in this directory and report the number of words and the first word."
)
SCHEMA = {
    "type": "object",
    "properties": {"word_count": {"type": "integer"}, "first_word": {"type": "string"}},
    "required": ["word_count", "first_word"],
}
#: Seconds to let the cancel step run before interrupting it.
CANCEL_AFTER_S = 12.0
#: Seconds to wait for the interrupted child to exit before escalating.
CANCEL_GRACE_S = 15.0


def version() -> str:
    out = subprocess.run(
        # Resolved from PATH like everywhere else in this repo.
        ["agy", "--version"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.strip().split()[0]


def command(prompt: str, workdir: Path, *, schema: dict | None = None) -> list[str]:
    """The pinned invocation (INTEGRATIONS.md §5), flag for flag.

    Two facts this order encodes, both cross-checked against Asterim's working
    integration: the prompt is the *value* of `-p` (the opposite of Claude's stdin), and
    `--output-format` is never omitted — default text mode is where issue #76 eats
    stdout when it is not a TTY. `--dangerously-skip-permissions` is deliberately
    absent: a soft-denied approval is a result ORACLE wants to see, not one to skip past.
    """
    cmd = [
        "agy",
        "--output-format",
        "stream-json",
        "--print-timeout",
        "5m",
        "--add-dir",
        str(workdir),
    ]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    cmd += ["-p", prompt]
    return cmd


def fresh(name: str) -> Path:
    workdir = WORKROOT / name
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    (workdir / "hello.txt").write_text(HELLO, encoding="utf-8")
    return workdir


def preview(steps: list[str]) -> None:
    print(f"\n{B}egress preview{X} - everything below leaves this machine (Antigravity/Google)\n")
    for step in steps:
        if step == "smoke":
            print(f"  {Y}[smoke]{X}  {SMOKE_PROMPT}")
            print(f"           {D}+ hello.txt: {HELLO.rstrip()}{X}")
        elif step == "unauth":
            print(f"  {Y}[unauth]{X} {SMOKE_PROMPT}")
            print(f"           {D}+ hello.txt, credentials hidden - expected to send nothing{X}")
        elif step == "cancel":
            print(f"  {Y}[cancel]{X} {CANCEL_PROMPT}")
            print(f"           {D}interrupted after {CANCEL_AFTER_S:.0f}s{X}")
        elif step == "schema":
            print(f"  {Y}[schema]{X} {SCHEMA_PROMPT}")
            print(f"           {D}+ --json-schema {json.dumps(SCHEMA)}{X}")
    print(f"\n  {Y}command shape:{X}")
    for part in command("<prompt>", WORKROOT / "<step>"):
        print(f"    {D}{part}{X}")
    print(
        f"\n  no --dangerously-skip-permissions {D}(approvals stay soft-denied){X}"
        f" - no repo files are read {D}(fresh workdir per step){X}"
    )


def write_fixture(name: str, ver: str, stdout: str, stderr: str) -> Path:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / f"{name}-v{ver}.jsonl"
    path.write_text(stdout, encoding="utf-8")
    if stderr.strip():
        path.with_suffix(".stderr.txt").write_text(stderr, encoding="utf-8")
    return path


def parse(stdout: str) -> list[dict]:
    events: list[dict] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"event": "UNPARSEABLE", "UNPARSEABLE": {"head": line[:200]}})
    return events


def body(event: dict) -> dict:
    """The undocumented envelope: the payload sits under a key named after the event."""
    name = event.get("event")
    value = event.get(name) if isinstance(name, str) else None
    return value if isinstance(value, dict) else {}


def result_of(events: list[dict]) -> dict:
    return next((body(e) for e in events if e.get("event") == "result"), {})


def report(
    path: Path, events: list[dict], elapsed: float, checks: list[tuple[str, bool, str]]
) -> bool:
    kinds = [str(e.get("event")) for e in events]
    print(f"\n{B}recorded{X}  {path.relative_to(ROOT)}  {D}{len(events)} events, {elapsed:.1f}s{X}")
    print(f"  {D}event sequence: {' -> '.join(kinds) or '(empty stdout)'}{X}")
    ok_all = True
    for label, ok, detail in checks:
        mark = f"{G}ok{X}" if ok else f"{R}FAIL{X}"
        print(f"  {label:<28} {mark}  {D}{detail}{X}")
        ok_all = ok_all and ok
    return ok_all


def step_smoke(ver: str) -> bool:
    workdir = fresh("smoke")
    started = time.perf_counter()
    proc = subprocess.run(  # noqa: S603 - fixed list, previewed and confirmed above
        command(SMOKE_PROMPT, workdir),
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.perf_counter() - started
    path = write_fixture("smoke", ver, proc.stdout, proc.stderr)
    events = parse(proc.stdout)
    res = result_of(events)
    count_file = workdir / "count.txt"
    produced = count_file.read_text(encoding="utf-8").strip() if count_file.exists() else None
    denied_note = "  <- soft denial? read the fixture" if produced is None else ""
    ok = report(
        path,
        events,
        elapsed,
        [
            ("stdout survived the pipe", bool(proc.stdout.strip()), f"{len(proc.stdout)} bytes"),
            ("exit code 0", proc.returncode == 0, str(proc.returncode)),
            ("init present", any(e.get("event") == "init" for e in events), ""),
            ("result is terminal", bool(events) and events[-1].get("event") == "result", ""),
            ("status SUCCESS", res.get("status") == "SUCCESS", str(res.get("status"))),
            (
                "work verifiably done",
                produced == str(WORD_COUNT),
                f"count.txt={produced!r}, expected {str(WORD_COUNT)!r}{denied_note}",
            ),
        ],
    )
    print(f"  {D}usage: {res.get('usage')}{X}")
    return ok


def step_unauth(ver: str) -> bool:
    """Credentials hidden by redirecting the environment, never by touching the real
    config: an empty HOME/APPDATA/LOCALAPPDATA is indistinguishable from a fresh machine
    to a CLI that reads its token from disk, and costs nothing to undo.

    Measured 2026-08-24: it is *not* indistinguishable. `agy` authenticated anyway, so
    its credentials do not come from any of those directories — the run proceeded and
    failed later, on a permission denial, for reasons that have nothing to do with auth.
    The step therefore asserts what it can honestly assert: either the failure is
    auth-shaped (the state is observed) or it is not (the state stays UNKNOWN and says
    so). A check that passes because *some* error occurred would be the worst outcome
    here — `preflight()` would be built on it."""
    workdir = fresh("nocreds")  # not "unauth": the path itself used to match the probe
    fake_home = Path(tempfile.mkdtemp(prefix="oracle-agy-nohome-"))
    env = dict(os.environ)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        env[key] = str(fake_home)
    started = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603 - fixed list, previewed and confirmed above
            command(SMOKE_PROMPT, workdir),
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=90,
        )
        timed_out, stdout, stderr, code = False, proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        # A hang IS the finding: preflight() would then need a timeout, not a return code.
        timed_out = True
        stdout = _text(exc.stdout)
        stderr = _text(exc.stderr)
        code = -1
    elapsed = time.perf_counter() - started
    path = write_fixture("unauth", ver, stdout, stderr)
    events = parse(stdout)
    res = result_of(events)
    said = f"{stderr}\n{res.get('error') or ''}".strip()
    # Deliberately narrow. The first version of this list contained the bare substring
    # "auth", and it matched the workdir path (`...\record-agy\unauth\count.txt`) inside
    # an unrelated permission error - reporting the state as observed when nothing of the
    # sort had happened. A probe that can pass by accident is worse than no probe.
    auth_shaped = any(
        word in said.lower()
        for word in (
            "authenticat",
            "unauthorized",
            "unauthenticated",
            "log in",
            "sign in",
            "credential",
            "not logged in",
            "401",
        )
    )
    ran_anyway = any(e.get("event") == "init" for e in events) and not auth_shaped
    ok = report(
        path,
        events,
        elapsed,
        [
            ("did not hang", not timed_out, "timed out at 90s" if timed_out else f"exit {code}"),
            (
                "unauthenticated observed",
                auth_shaped,
                said[:160]
                if auth_shaped
                else "NOT observed: credentials survived the env redirection",
            ),
        ],
    )
    if ran_anyway:
        print(
            f"  {Y}finding{X} agy authenticates from somewhere other than "
            f"HOME/USERPROFILE/APPDATA/LOCALAPPDATA/XDG_CONFIG_HOME.\n"
            f"  {D}The 'present but unauthenticated' preflight state is still UNKNOWN. "
            f"Do not infer it.{X}"
        )
    shutil.rmtree(fake_home, ignore_errors=True)
    return ok


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


def step_cancel(ver: str) -> bool:
    workdir = fresh("cancel")
    # CREATE_NEW_PROCESS_GROUP is what makes CTRL_BREAK deliverable to the child on
    # Windows - the same flag the adapter uses, so this measures the adapter's path.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    started = time.perf_counter()
    proc = subprocess.Popen(  # noqa: S603 - fixed list, previewed and confirmed above
        command(CANCEL_PROMPT, workdir),
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )
    # Read stdout on a thread, stamping each line. Without timestamps the decisive
    # question of this step - does the terminal `result` arrive *because* of the
    # interrupt, or did the run die on its own beforehand? - cannot be answered from the
    # fixture, and two earlier attempts were misread for exactly that reason.
    timeline: list[tuple[float, str]] = []

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            timeline.append((time.perf_counter() - started, line))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    time.sleep(CANCEL_AFTER_S)
    already_done = proc.poll() is not None
    escalated = "none"
    if not already_done:
        interrupt = signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT
        proc.send_signal(interrupt)
        escalated = "interrupt"
        try:
            proc.wait(timeout=CANCEL_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.terminate()
            escalated = "interrupt -> terminate"
            try:
                proc.wait(timeout=CANCEL_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                escalated = "interrupt -> terminate -> kill"
    reader.join(timeout=5)
    _, stderr = proc.communicate()
    elapsed = time.perf_counter() - started
    stdout = "".join(line for _, line in timeline)
    path = write_fixture("cancel", ver, stdout, stderr)
    path.with_suffix(".timing.txt").write_text(
        "".join(f"{at:7.2f}s  {line}" for at, line in timeline)
        + f"\n# interrupt sent at {CANCEL_AFTER_S:.2f}s, child exited at {elapsed:.2f}s\n",
        encoding="utf-8",
    )
    events = parse(stdout)
    left = ", ".join(p.name for p in workdir.iterdir() if p.name != "hello.txt") or "nothing new"
    terminal = next((body(e) for e in reversed(events) if e.get("event") == "result"), None)
    result_at = next(
        (at for at, line in timeline if '"event":"result"' in line.replace(" ", "")), None
    )
    before_interrupt = sum(1 for at, _ in timeline if at < CANCEL_AFTER_S)
    ok = report(
        path,
        events,
        elapsed,
        [
            ("child exited", proc.returncode is not None, f"exit {proc.returncode}"),
            (
                "run was still live",
                not already_done,
                "it ended before the interrupt - nothing was measured" if already_done else "",
            ),
            ("interrupt alone sufficed", escalated == "interrupt", escalated),
            (
                "was actually working",
                before_interrupt > 1,
                f"{before_interrupt} events before the interrupt",
            ),
        ],
    )
    # Not pass/fail, but the whole point of the step: what the vendor does with a SIGINT
    # is the semantics INTEGRATIONS.md section 5 has to state.
    print(f"  {D}escalation: {escalated} - left in the workdir: {left}{X}")
    print(
        f"  {D}result line at: "
        f"{f'{result_at:.2f}s' if result_at is not None else 'never'} "
        f"(interrupt at {CANCEL_AFTER_S:.2f}s, exit at {elapsed:.2f}s){X}"
    )
    print(f"  {D}terminal result on interrupt: {terminal}{X}")
    print(f"  {D}stderr tail: {stderr.strip()[-200:]!r}{X}")
    return ok


def step_schema(ver: str) -> bool:
    workdir = fresh("schema")
    started = time.perf_counter()
    proc = subprocess.run(  # noqa: S603 - fixed list, previewed and confirmed above
        command(SCHEMA_PROMPT, workdir, schema=SCHEMA),
        cwd=workdir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.perf_counter() - started
    path = write_fixture("schema", ver, proc.stdout, proc.stderr)
    events = parse(proc.stdout)
    res = result_of(events)
    response = res.get("response")
    try:
        parsed = json.loads(response) if isinstance(response, str) else None
    except json.JSONDecodeError:
        parsed = None
    conforms = (
        isinstance(parsed, dict)
        and parsed.get("word_count") == WORD_COUNT
        and isinstance(parsed.get("first_word"), str)
    )
    ok = report(
        path,
        events,
        elapsed,
        [
            ("exit code 0", proc.returncode == 0, str(proc.returncode)),
            ("status SUCCESS", res.get("status") == "SUCCESS", str(res.get("status"))),
            ("response parses as JSON", isinstance(parsed, dict), f"{str(response)[:120]!r}"),
            ("matches the schema", conforms, str(parsed)),
        ],
    )
    print(f"  {D}keys of the result body: {sorted(res)}{X}")
    return ok


STEPS = {"smoke": step_smoke, "unauth": step_unauth, "cancel": step_cancel, "schema": step_schema}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show the payload, send nothing")
    parser.add_argument(
        "--step", choices=[*STEPS, "all"], default="all", help="record one step, or all of them"
    )
    args = parser.parse_args()

    if shutil.which("agy") is None:
        print(f"{R}agy is not on PATH{X}")
        return 1
    ver = version()
    steps = list(STEPS) if args.step == "all" else [args.step]
    print(f"{B}agy{X} v{ver}  {D}(OQ-05 recorded v1.1.14 - re-verifying the contract here){X}")
    preview(steps)

    if args.dry_run:
        print(f"\n{Y}dry run{X} - nothing sent.")
        return 0
    if input(f"\nSend this payload? Type {G}yes{X} to proceed: ").strip().lower() != "yes":
        print("Cancelled. Nothing sent.")
        return 1

    results = {name: STEPS[name](ver) for name in steps}
    print(f"\n{B}summary{X}")
    for name, ok in results.items():
        print(f"  {name:<8} {G + 'ok' + X if ok else R + 'FAIL - read the fixture' + X}")
    print(
        f"\n{D}The fixtures are the contract now. Anything that FAILED is a finding for "
        f"INTEGRATIONS.md section 5, not a reason to retry until it passes.{X}"
    )
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
