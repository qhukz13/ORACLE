#!/usr/bin/env python
"""Execute one task from a real Antigravity plan through ORACLE's delegation path (P6-T5 req 4).

    uv run python scripts/verify_plan_task_live.py --dry-run   # show the payload, send nothing
    uv run python scripts/verify_plan_task_live.py --list      # what the ledger offers
    uv run python scripts/verify_plan_task_live.py             # confirm, then run it

This is the smallest true positive available for the whole supervisor architecture:

    a plan authored by one vendor, executed by another, verified by ORACLE itself.

Nothing here is new machinery. The task comes out of `verify_agy_planning.py`'s ledger —
a plan Antigravity actually returned — and everything after that is the delegation
lifecycle that has existed since P6-T1: render the packet, price the egress at the gate,
ask the owner, cut a scrubbed worktree, run Claude in it, collect, and verify with
ORACLE's own diff and tests rather than the delegate's prose. If this works, the seam
between "who plans" and "who codes" is real and not a diagram.

Two properties worth stating because they are easy to lose:

* **The plan is untrusted input** (ADR-0021). Its task is rendered into a packet and
  priced by the policy engine like any other egress; it buys no authority. A plan that
  asked for `git push` would get a confirmation card, not a push.
* **The worktree is disposable.** The delegate's changes are examined and thrown away
  unless the owner keeps them: this run is evidence about the *architecture*, not a
  shortcut for landing Phase 7 code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oracle.core.approvals import ApprovalStore
from oracle.core.eventlog import EventLog
from oracle.delegation.service import DelegationService, Outcome, PacketInputs
from oracle.integrations.claude import ClaudeCodeAdapter
from oracle.integrations.types import HandoffPacket
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.storage.db import connect, migrate
from oracle.toolhost import ToolHost
from oracle.tools import ToolExecutor, build_registry

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "logs/integrations/agy-planning-ledger.jsonl"
SCRATCH = ROOT / ".oracle/tmp/plan-task"

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"

#: Read, write, and the test command — the minimum a `coder` task needs to produce a diff
#: this script can verify. Deliberately no `Bash(git *)`: the worktree is ORACLE's to
#: manage, and a delegate that commits has hidden its own diff.
ALLOWED_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash(uv run pytest *)")
#: What the delegate must report, in its own structured field. ORACLE ignores the claim
#: and reads the diff; the field exists so a *disagreement* between claim and evidence is
#: visible, which is the Asterim lesson about headless agents that exit 0 having done
#: nothing.
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "tests_run": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["files_changed", "summary"],
}


def plans() -> list[dict[str, Any]]:
    if not LEDGER.exists():
        return []
    rows = [
        json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return [r for r in rows if r.get("valid") and r.get("structured")]


def coder_tasks(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        t
        for t in record["structured"]["tasks"]
        if t.get("role") == "coder" and t.get("acceptance") and t.get("expected_outcome") == "diff"
    ]


def choose(record_key: str | None, task_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Default to the *smallest* coder task on offer. The point of this run is the seam,
    not the size of the change, and a smaller task fails for fewer reasons."""
    candidates = [
        (record, task)
        for record in plans()
        if record_key is None or record["key"] == record_key
        for task in coder_tasks(record)
        if task_id is None or task["id"] == task_id
    ]
    if not candidates:
        raise SystemExit(
            f"{R}no valid plan with a coder task in {LEDGER.relative_to(ROOT)}{X}\n"
            f"run scripts/verify_agy_planning.py first"
        )
    return min(candidates, key=lambda pair: len(json.dumps(pair[1])))


def packet_for(record: dict[str, Any], task: dict[str, Any]) -> HandoffPacket:
    constraints = [
        *task.get("constraints", []),
        # The fence P6-T5 puts around this run, restated where the delegate will read it.
        "This is a spike: change as little as possible and do not restructure the repo.",
        "Do not commit, push, or run any git command; ORACLE owns the worktree.",
    ]
    return HandoffPacket(
        task_id=f"p6t5-{record['objective']}-{task['id']}".lower(),
        task=task["objective"],
        acceptance=tuple(task.get("acceptance", ())),
        constraints=tuple(constraints),
        allowed_tools=ALLOWED_TOOLS,
        result_schema=RESULT_SCHEMA,
    )


def preview(record: dict[str, Any], task: dict[str, Any], packet: HandoffPacket) -> None:
    print(f"\n{B}the plan this task came from{X}")
    print(
        f"  {Y}planner:{X}   antigravity, effort={record['effort']}, {record['seconds']}s, {record.get('total_tokens', 0):,} tokens"
    )
    print(f"  {Y}objective:{X} {record['structured']['objective'][:150]}")
    print(
        f"  {Y}tasks:{X}     {len(record['structured']['tasks'])} "
        f"{D}({', '.join(t['id'] + ':' + t['role'] for t in record['structured']['tasks'])}){X}"
    )
    print(f"\n{B}the task being executed{X}  {D}(plan-local id {task['id']}){X}")
    print(f"  {Y}objective:{X}  {task['objective']}")
    print(f"  {Y}acceptance:{X} {'; '.join(task.get('acceptance', []))}")
    print(
        f"\n{B}egress preview{X} - the delegation service will ask again, with the rendered packet\n"
    )
    print(f"  {Y}worker:{X}       claude-code {D}(subscription auth){X}")
    print(
        f"  {Y}source repo:{X}  {ROOT} {D}(a scrubbed worktree is cut from it; the repo is untouched){X}"
    )
    print(f"  {Y}tools lent:{X}   {', '.join(ALLOWED_TOOLS)}")
    print(f"  {Y}prompt:{X}")
    for line in packet.render_prompt().splitlines():
        print(f"    {D}{line}{X}")


async def verifier(executor: ToolExecutor) -> Any:
    """ORACLE's own test run, through the gate — the half of verification the delegate
    cannot influence. Wired as a callable so the service never imports the executor."""

    async def run_tests(path: Path) -> dict[str, Any] | None:
        outcome = await executor.execute("dev.run_tests", {"path": str(path)})
        result = outcome.result
        # `ran` means the tests ran, not that the tool call returned. The first version
        # of this conflated them and reported "ORACLE ran the tests itself: ok" for a run
        # in which the tool had refused to start for want of a tool host - the exact
        # false-green this project exists to avoid.
        detail = result.model_dump() if hasattr(result, "model_dump") else result
        started = isinstance(detail, dict) and ("passed" in detail or "failed" in detail)
        return {
            "ran": bool(started),
            "ok": outcome.ok,
            "detail": detail,
            "reason": None if started else "the tool did not run the suite",
        }

    return run_tests


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show the payload, send nothing")
    parser.add_argument("--list", action="store_true", help="list the coder tasks on offer")
    parser.add_argument("--plan", help="ledger key, e.g. taskgraph/low/0")
    parser.add_argument("--task", help="plan-local task id, e.g. A")
    parser.add_argument("--keep", action="store_true", help="keep the worktree after the run")
    parser.add_argument(
        "--verify-only",
        metavar="WORKTREE",
        help="skip the delegation and run ORACLE's verification against an existing worktree",
    )
    args = parser.parse_args()

    if args.verify_only:
        # For a delegation whose verification could not run at the time - the first live
        # run had no tool host, so `dev.run_tests` refused to spawn. The evidence half of
        # requirement 4 is re-runnable without spending another delegation.
        engine = PolicyEngine(load_policy(ROOT / "config" / "policy.yaml"))
        executor = ToolExecutor(
            build_registry(),
            engine,
            AuditLog(SCRATCH / "audit.jsonl"),
            host=ToolHost(cwd=ROOT.parent),
        )
        run_tests = await verifier(executor)
        tests = await run_tests(Path(args.verify_only))
        print(f"\n{B}ORACLE's own verification{X}  {D}{args.verify_only}{X}")
        print(f"  ran:    {tests and tests.get('ran')}")
        print(f"  ok:     {tests and tests.get('ok')}")
        print(f"  detail: {json.dumps(tests and tests.get('detail'))[:600]}")
        return 0 if (tests and tests.get("ran")) else 1

    if args.list:
        for record in plans():
            for task in coder_tasks(record):
                print(f"  {record['key']:<26} {task['id']:<3} {task['objective'][:90]}")
        return 0

    record, task = choose(args.plan, args.task)
    packet = packet_for(record, task)
    preview(record, task, packet)

    if args.dry_run:
        print(f"\n{Y}dry run{X} - nothing sent.")
        return 0
    answer = await asyncio.to_thread(input, f"\nRun this delegation? Type {G}yes{X} to proceed: ")
    if answer.strip().lower() != "yes":
        print("Cancelled. Nothing sent.")
        return 1

    SCRATCH.mkdir(parents=True, exist_ok=True)
    # `connect()`, not raw aiosqlite: it sets the row factory the migration runner and
    # the event log both index by name. A bare connection fails on the first query.
    conn = await connect(SCRATCH / "events.sqlite3")
    try:
        await migrate(conn)
        eventlog = EventLog(conn)
        await eventlog.load_head()
        engine = PolicyEngine(load_policy(ROOT / "config" / "policy.yaml"))
        # With a ToolHost: `dev.run_tests` spawns a process, and the executor refuses to
        # do that in-process (ADR-0003). Without one, verification silently does nothing.
        executor = ToolExecutor(
            build_registry(),
            engine,
            AuditLog(SCRATCH / "audit.jsonl"),
            host=ToolHost(cwd=ROOT.parent),
        )
        approvals = ApprovalStore(eventlog, executor, ttl_s=900.0)
        service = DelegationService(
            eventlog,
            approvals,
            engine,
            ClaudeCodeAdapter(),
            handoff_root=SCRATCH / "handoff",
            run_tests=await verifier(executor),
        )

        running = asyncio.create_task(service.run(packet, ROOT, PacketInputs()))
        approved = asyncio.create_task(approve_when_asked(approvals, eventlog))
        active = await running
        approved.cancel()
    finally:
        await conn.close()

    return report(active, keep=args.keep)


async def approve_when_asked(approvals: ApprovalStore, eventlog: EventLog) -> None:
    """The egress preview, as the service renders it. Nothing has left the machine when
    this prints — that ordering is the point of the whole gate."""
    while True:
        for request in approvals.open_requests():
            body = request.get("preview") or {}
            print(f"\n{B}the packet, rendered{X}  {D}{body.get('packet_dir')}{X}")
            print(f"  {Y}files:{X}      {', '.join(body.get('files') or [])}")
            print(f"  {Y}tokens:{X}     {body.get('tokens')}")
            print(f"  {Y}redactions:{X} {body.get('redactions')}")
            print(f"  {Y}tools:{X}      {', '.join(body.get('allowed_tools') or [])}")
            print(f"  {Y}destination:{X} {body.get('destination')} via {body.get('adapter')}")
            answer = await asyncio.to_thread(
                input, f"\nApprove this egress? Type {G}yes{X} to send: "
            )
            await approvals.resolve(str(request["approval_id"]), answer.strip().lower() == "yes")
            return
        await asyncio.sleep(0.2)


def report(active: Any, *, keep: bool) -> int:
    result = active.result or {}
    print(f"\n{B}outcome{X}  {active.outcome}")
    if not result:
        print(f"  {D}nothing ran; see the events above{X}")
        return 1
    tests = result.get("tests") or {}
    structured = result.get("structured") or {}
    checks = [
        ("the delegate finished", active.outcome == Outcome.SUCCESS, str(result.get("exit_code"))),
        (
            "it changed something",
            bool(result.get("diff_lines")) or bool(result.get("untracked")),
            f"{result.get('diff_lines')} diff lines, untracked: {result.get('untracked')}",
        ),
        (
            "ORACLE ran the tests itself",
            bool(tests.get("ran")),
            f"tests ok={tests.get('ok')}" if tests.get("ran") else str(tests.get("reason")),
        ),
        (
            "its claim matches the evidence",
            bool(structured.get("files_changed")) and bool(result.get("diff_lines")),
            f"claimed {structured.get('files_changed')}",
        ),
    ]
    for label, ok, detail in checks:
        mark = f"{G}ok{X}" if ok else f"{R}FAIL{X}"
        print(f"  {label:<30} {mark}  {D}{detail}{X}")
    print(f"\n  {Y}worktree:{X} {result.get('workspace')}  {Y}branch:{X} {result.get('branch')}")
    print(
        f"  {D}{'kept for inspection' if keep else 'inspect it, then delete it - this run is evidence, not a merge'}{X}"
    )
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
