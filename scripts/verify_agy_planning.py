#!/usr/bin/env python
"""Measure whether `agy --json-schema` can return a valid ExecutionPlan (P6-T5 req 3, OQ-20).

    uv run python scripts/verify_agy_planning.py --dry-run     # show the payload, send nothing
    uv run python scripts/verify_agy_planning.py --calls 1     # one pilot call, then decide
    uv run python scripts/verify_agy_planning.py               # the full grid, resumable
    uv run python scripts/verify_agy_planning.py --report      # re-print the verdict, send nothing

Phase 8 makes Antigravity the default **planner**. That rests on one unverified
assumption ([OQ-20](../docs/OPEN_QUESTIONS.md#oq-20)): that a structured plan comes back
reliably, at a cost and latency worth paying. This script answers it with numbers instead
of optimism, against a **gate of 90% valid-on-first-attempt** — the same bar the old
plan-validity gate used.

**A failed gate is a successful run of this script.** The fallback ladder
([PLANNER.md §6](../docs/PLANNER.md#6-fallbacks)) exists precisely so that the answer can
be "no": Claude authors plans against the same schema, and Antigravity keeps its
read-only reviewer/researcher roles. That outcome changes one line of the capability
registry, not the architecture.

Three things it deliberately does *not* do, because P6-T5's constraints fence the spike:
no task graph, no scheduler, no roles registry. The ExecutionPlan models below are a
local draft of PLANNER.md §2 — they live here, in `scripts/`, until Phase 7 gives them a
home. The JSON schema is generated from them (never hand-written), per AGENTS.md.

Every call is an egress: previewed, confirmed once for the batch, and appended to a
resumable ledger so a quota wall or a `Ctrl-C` costs at most one call's work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oracle.integrations.antigravity import AntigravityAdapter
from oracle.integrations.types import HandoffPacket, Workspace

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "logs/integrations/agy-planning-ledger.jsonl"
FIXTURES = ROOT / "tests/fixtures/agents/antigravity"

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"

#: The gate. Stated before the first call, so the result cannot be argued into passing.
GATE = 0.90


# -- the schema under test (a local draft of PLANNER.md §2) ---------------------

#: ADR-0017's rule applies here too: express what the decoder can enforce — enums,
#: required fields, types — and validate the rest afterwards. `expected_outcome` is a
#: Literal for exactly that reason; `len(tasks) <= 12` is checked in code, not by schema.
Role = Literal[
    "planner", "coder", "debugger", "tester", "reviewer", "researcher", "summarizer", "verifier"
]
Outcome = Literal["diff", "report", "answer", "verdict"]


class PlannedTask(BaseModel):
    id: str
    role: Role
    objective: str
    project: str | None = None
    acceptance: list[str] = []
    constraints: list[str] = []
    context_hints: list[str] = []
    agent_hint: str | None = None
    depends_on: list[str] = []
    expected_outcome: Outcome


class ExecutionPlan(BaseModel):
    objective: str
    summary: str
    tasks: list[PlannedTask]
    risks: list[str] = []


#: PLANNER.md §4 and §5: a role or an agent the plan invents is a validation error, never
#: a lookup that happens to miss. Same for a project — a hallucinated project name must
#: never become a path.
ROLES = set(Role.__args__)  # type: ignore[attr-defined]
AGENTS = {"claude", "antigravity", "local"}
PROJECTS = {"oracle", "asterim", "asterim-pipeline"}
MAX_TASKS = 12


# -- the corpus ----------------------------------------------------------------

#: Real objectives, from this project's own roadmap and the Asterim reuse audit - not
#: toy prompts. A planner that shines on "build a todo app" and fails on these would have
#: told us nothing.
OBJECTIVES: list[dict[str, str]] = [
    {
        "id": "taskgraph",
        "text": (
            "Implement ORACLE's durable task graph (Phase 7): a `tasks` table with the "
            "status vocabulary PENDING/READY/RUNNING/PASSED/FAILED/TIMEOUT/SKIPPED/CANCELLED, "
            "ready-set scheduling over a DAG, cycle detection that reports the cycle as a "
            "path, and crash recovery that never auto-restarts an interrupted agent. "
            "Python 3.12, asyncio, SQLite via aiosqlite, in the existing src/oracle package."
        ),
    },
    {
        "id": "memory",
        "text": (
            "Build ORACLE's memory subsystem (Phase 9): episodic records of past delegation "
            "attempts and semantic facts extracted from them, stored in SQLite, with recall "
            "wired into the existing context assembly so a repeated task carries what was "
            "learned the first time. Retrieval quality must be measured against a fixture set."
        ),
    },
    {
        "id": "permission-denials",
        "text": (
            "ORACLE's Claude adapter does not surface `result.permission_denials`, which the "
            "Asterim audit calls the single most useful field when a headless run does "
            "nothing. Surface it through the adapter's normalised events and the collected "
            "result, with fixtures recorded for a run that was denied a tool."
        ),
    },
    {
        "id": "worktree-scrub",
        "text": (
            "Harden ORACLE's worktree isolation: before any delegation runs, scrub the "
            "worktree of credentials and agent configuration, verify the scrub with a test "
            "that plants a secret and asserts it is gone, and make the scrub a precondition "
            "the adapter cannot be invoked without."
        ),
    },
]

#: Two levels, because latency and cost are the other half of OQ-20's question and the
#: registry has to pick one. `medium` is skipped deliberately: the interesting comparison
#: is the cheap end against the expensive end.
EFFORTS = ("low", "high")
#: Two, not three, by the owner's call on 2026-08-24 after the pilot measured 55.6k tokens
#: per plan - four times the pre-run estimate. 4 objectives x 2 efforts x 2 repeats = 16
#: calls, which is below the task's own >=20 bar: OQ-20 is therefore *narrowed* with
#: numbers rather than closed, and the dev log says so rather than rounding up.
REPEATS = 2


def cells() -> list[dict[str, Any]]:
    """The grid, in a fixed order so a resumed run continues where it stopped."""
    return [
        {"objective": obj["id"], "effort": effort, "repeat": repeat}
        for obj in OBJECTIVES
        for effort in EFFORTS
        for repeat in range(REPEATS)
    ]


def key(cell: dict[str, Any]) -> str:
    return f"{cell['objective']}/{cell['effort']}/{cell['repeat']}"


# -- the prompt ----------------------------------------------------------------

PLANNING_RULES = (
    f"You are producing an execution plan for ORACLE, a local-first supervisor that will "
    f"execute your plan by delegating each task to a coding agent in an isolated git "
    f"worktree and verifying the result itself.\n"
    f"Return ONLY a JSON object matching the provided schema. No prose, no code fences.\n"
    f"Rules the plan must satisfy, because ORACLE validates them and rejects the plan "
    f"otherwise:\n"
    f'- at most {MAX_TASKS} tasks; each `id` unique and plan-local (e.g. "A", "B").\n'
    f"- every `depends_on` entry names another task in this plan; no cycles.\n"
    f"- `role` is one of: {', '.join(sorted(ROLES))}.\n"
    f"- `project`, if set, is one of: {', '.join(sorted(PROJECTS))}; otherwise null.\n"
    f"- `agent_hint`, if set, is one of: {', '.join(sorted(AGENTS))}. It is a "
    f"recommendation; ORACLE decides.\n"
    f'- every task whose `expected_outcome` is "diff" has non-empty `acceptance`: '
    f'criteria a machine can check, like "pytest tests/test_tasks.py passes".\n'
    f"- `context_hints` are queries or file paths for ORACLE's context engine to fetch. "
    f"Hints, not contents.\n"
    f"You will not execute any of this. You have no tools. Return the plan."
)


def packet_for(objective: dict[str, str], schema: dict[str, Any]) -> HandoffPacket:
    return HandoffPacket(
        task_id=f"plan-{objective['id']}",
        task=f"{PLANNING_RULES}\n\nOBJECTIVE:\n{objective['text']}",
        # The planner gets no tools and no workspace beyond an empty temp dir
        # (PLANNER.md §7): a planner that browses is a planner whose egress cannot be
        # previewed as one packet.
        allowed_tools=("Read",),
        result_schema=schema,
    )


# -- validation (PLANNER.md §2, in order) --------------------------------------


def find_cycle(tasks: list[PlannedTask]) -> list[str] | None:
    """Iterative DFS returning the cycle *as a path* — Asterim's diagnostic, ported,
    because "there is a cycle" is not an error message anyone can act on."""
    edges = {t.id: list(t.depends_on) for t in tasks}
    colour: dict[str, int] = {}
    for start in edges:
        if colour.get(start):
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = []
        while stack:
            node, index = stack.pop()
            if index == 0:
                if colour.get(node) == 2:
                    continue
                colour[node] = 1
                path.append(node)
            if index < len(edges.get(node, [])):
                stack.append((node, index + 1))
                nxt = edges[node][index]
                if colour.get(nxt) == 1:
                    return [*path[path.index(nxt) :], nxt]
                if colour.get(nxt) != 2 and nxt in edges:
                    stack.append((nxt, 0))
            else:
                colour[node] = 2
                if path and path[-1] == node:
                    path.pop()
    return None


def validate(plan: ExecutionPlan) -> list[str]:
    """The checks that come *after* the schema. Returns every problem, not the first —
    the plan-repair pattern feeds the specific errors back, and one error at a time
    would cost one round trip each."""
    problems: list[str] = []
    ids = [t.id for t in plan.tasks]
    if not plan.tasks:
        problems.append("no tasks")
    if len(ids) != len(set(ids)):
        problems.append("duplicate task ids")
    if len(plan.tasks) > MAX_TASKS:
        problems.append(f"{len(plan.tasks)} tasks > {MAX_TASKS}")
    known = set(ids)
    for task in plan.tasks:
        for dep in task.depends_on:
            if dep not in known:
                problems.append(f"{task.id}.depends_on names unknown task {dep!r}")
        if task.project is not None and task.project not in PROJECTS:
            problems.append(f"{task.id}.project {task.project!r} is not a registered project")
        if task.agent_hint is not None and task.agent_hint not in AGENTS:
            problems.append(f"{task.id}.agent_hint {task.agent_hint!r} is not a registered agent")
        if task.expected_outcome == "diff" and not task.acceptance:
            problems.append(f"{task.id} expects a diff with no acceptance criteria")
    cycle = find_cycle(plan.tasks)
    if cycle:
        problems.append("cycle: " + " -> ".join(cycle))
    return problems


def shape_of_failure(record: dict[str, Any]) -> str:
    """One label per failed call, so the failures can be counted rather than described.
    These are the shapes OQ-20 asks about by name."""
    if not record.get("ok_run"):
        return f"run failed ({record.get('status') or 'no status'})"
    if record.get("structured") is None:
        response = (record.get("response") or "").strip()
        if not response:
            return "empty response"
        if response.startswith("```") or not response.startswith("{"):
            return "prose-wrapped JSON"
        try:
            json.loads(response)
        except json.JSONDecodeError:
            return "truncated or invalid JSON"
        return "valid JSON, but no structured_output field"
    if record.get("schema_errors"):
        return "schema-invalid: " + str(record["schema_errors"][0])[:60]
    return "semantically invalid: " + str((record.get("problems") or ["?"])[0])[:60]


# -- the run -------------------------------------------------------------------


def load_ledger() -> dict[str, dict[str, Any]]:
    if not LEDGER.exists():
        return {}
    done = {}
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            done[record["key"]] = record
    return done


def append(record: dict[str, Any]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


async def one_call(cell: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    objective = next(o for o in OBJECTIVES if o["id"] == cell["objective"])
    adapter = AntigravityAdapter(effort=cell["effort"])
    workdir = Path(tempfile.mkdtemp(prefix="oracle-agy-plan-"))
    started = time.perf_counter()
    handle = await adapter.submit(packet_for(objective, schema), Workspace(path=workdir))
    raw: list[str] = []
    assert handle.proc.stdout is not None
    async for _ in adapter.events(handle):
        pass
    result = await adapter.collect(handle)
    elapsed = time.perf_counter() - started
    vendor = handle.result or {}
    usage = vendor.get("usage") or {}

    record: dict[str, Any] = {
        "key": key(cell),
        **cell,
        "ok_run": result.success,
        "status": vendor.get("status"),
        "seconds": round(elapsed, 2),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "thinking_tokens": usage.get("thinking_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "response": (result.result_text or "")[:4000],
        "structured": result.structured,
        "schema_errors": None,
        "problems": None,
        "valid": False,
        "raw": raw,
    }
    if result.structured is not None:
        try:
            plan = ExecutionPlan.model_validate(result.structured)
        except ValidationError as exc:
            record["schema_errors"] = [
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
            ]
        else:
            problems = validate(plan)
            record["problems"] = problems or None
            record["valid"] = not problems
            record["task_count"] = len(plan.tasks)
            # Conformance is not usefulness, and the pilot proved it: a plan can pass
            # every check in PLANNER.md §2 and still be a DAG with no edges — six tasks
            # the scheduler would fire simultaneously because none declared a dependency.
            # These counters cost nothing and are the difference between "the schema
            # holds" and "the planner can plan".
            record["edges"] = sum(len(t.depends_on) for t in plan.tasks)
            record["with_project"] = sum(1 for t in plan.tasks if t.project)
            record["with_context_hints"] = sum(1 for t in plan.tasks if t.context_hints)
            record["with_agent_hint"] = sum(1 for t in plan.tasks if t.agent_hint)
            record["risk_count"] = len(plan.risks)
    if not record["valid"]:
        record["failure_shape"] = shape_of_failure(record)
    return record


def preview(schema: dict[str, Any], todo: list[dict[str, Any]]) -> None:
    print(f"\n{B}egress preview{X} - everything below leaves this machine (Antigravity/Google)\n")
    print(
        f"  {Y}calls:{X} {len(todo)}  {D}({len(OBJECTIVES)} objectives x {len(EFFORTS)} efforts x {REPEATS} repeats, minus what the ledger already has){X}"
    )
    print(f"  {Y}measured cost so far:{X} ~15k input tokens per turn, so budget roughly")
    print(f"    {D}{len(todo)} x 15-45k input tokens, plus output{X}")
    print(f"  {Y}each call sends:{X} the planning rules below + one objective + the schema")
    print(
        f"  {Y}each call does NOT send:{X} any file from this repo {D}(empty temp workdir, no tools){X}"
    )
    print(f"\n{D}{PLANNING_RULES}{X}\n")
    for objective in OBJECTIVES:
        print(f"  {Y}[{objective['id']}]{X} {objective['text'][:150]}...")
    print(
        f"\n  {Y}schema ({len(json.dumps(schema))} bytes, generated from the pydantic models):{X}"
    )
    print(f"    {D}{json.dumps(schema)[:300]}...{X}")


def report(records: list[dict[str, Any]]) -> bool:
    if not records:
        print(f"{Y}no calls recorded yet{X}")
        return False
    valid = [r for r in records if r.get("valid")]
    rate = len(valid) / len(records)
    print(f"\n{B}OQ-20 - agy --json-schema against the ExecutionPlan schema{X}\n")
    print(f"  calls                {len(records)}")
    mark = f"{G}GATE GREEN{X}" if rate >= GATE else f"{R}GATE RED{X}"
    print(
        f"  valid first attempt  {len(valid)}/{len(records)} = {rate:.0%}   {mark} {D}(gate {GATE:.0%}){X}"
    )

    print(f"\n  {Y}by effort{X}")
    for effort in EFFORTS:
        subset = [r for r in records if r["effort"] == effort]
        if not subset:
            continue
        ok = [r for r in subset if r.get("valid")]
        seconds = [r["seconds"] for r in subset]
        tokens = [r["total_tokens"] for r in subset if r.get("total_tokens")]
        print(
            f"    {effort:<5} {len(ok)}/{len(subset)} valid   "
            f"median {statistics.median(seconds):6.1f}s   "
            f"median {statistics.median(tokens) if tokens else 0:>7,.0f} tokens"
        )

    print(f"\n  {Y}by objective{X}")
    for objective in OBJECTIVES:
        subset = [r for r in records if r["objective"] == objective["id"]]
        if not subset:
            continue
        ok = [r for r in subset if r.get("valid")]
        sizes = [r.get("task_count") for r in subset if r.get("task_count")]
        print(
            f"    {objective['id']:<20} {len(ok)}/{len(subset)} valid   "
            f"{D}plan size {min(sizes) if sizes else '-'}-{max(sizes) if sizes else '-'} tasks{X}"
        )

    planned = [r for r in records if r.get("task_count")]
    if planned:
        edged = [r for r in planned if r.get("edges")]
        print(f"\n  {Y}plan richness{X} {D}(valid is not the same as useful){X}")
        print(
            f"    dependencies declared  {len(edged)}/{len(planned)} plans   "
            f"{D}a plan with no edges schedules every task at once{X}"
        )
        for field, label in (
            ("with_project", "project set"),
            ("with_context_hints", "context hints"),
            ("with_agent_hint", "agent hint"),
        ):
            filled = sum(r.get(field) or 0 for r in planned)
            total = sum(r["task_count"] for r in planned)
            print(f"    {label:<22} {filled}/{total} tasks")
        print(
            f"    risks listed           {sum(r.get('risk_count') or 0 for r in planned)} across {len(planned)} plans"
        )

    failures = Counter(r["failure_shape"] for r in records if r.get("failure_shape"))
    if failures:
        print(f"\n  {Y}failure shapes{X}")
        for shape, count in failures.most_common():
            print(f"    {count:>3}x  {shape}")

    tokens = [r["total_tokens"] for r in records if r.get("total_tokens")]
    if tokens:
        print(
            f"\n  {Y}cost{X}  median {statistics.median(tokens):,.0f} tokens/plan, "
            f"{sum(tokens):,} total across {len(records)} calls"
        )
    print(
        f"\n{D}Ledger: {LEDGER.relative_to(ROOT)}. A red gate promotes the fallback ladder "
        f"(PLANNER.md section 6) - it is a finding, not a failure.{X}"
    )
    return rate >= GATE


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show the payload, send nothing")
    parser.add_argument("--report", action="store_true", help="re-print the verdict, send nothing")
    parser.add_argument("--calls", type=int, default=0, help="cap this batch (0 = the whole grid)")
    args = parser.parse_args()

    schema = ExecutionPlan.model_json_schema()
    done = load_ledger()

    if args.report:
        return 0 if report(list(done.values())) else 1

    todo = [c for c in cells() if key(c) not in done]
    if args.calls:
        todo = todo[: args.calls]
    if not todo:
        print(f"{G}the grid is complete{X} - {len(done)} calls in the ledger")
        return 0 if report(list(done.values())) else 1

    preview(schema, todo)
    if args.dry_run:
        print(f"\n{Y}dry run{X} - nothing sent.")
        return 0
    if (
        input(f"\nSend {len(todo)} planning calls? Type {G}yes{X} to proceed: ").strip().lower()
        != "yes"
    ):
        print("Cancelled. Nothing sent.")
        return 1

    adapter = AntigravityAdapter()
    pre = await adapter.preflight()
    if not pre.ok:
        print(f"{R}preflight failed:{X} {pre.reason}\n  {pre.remedy}")
        return 1
    print(f"{D}agy v{pre.version}, authenticated{X}\n")

    for index, cell in enumerate(todo, 1):
        record = await one_call(cell, schema)
        append(record)
        mark = f"{G}valid{X}" if record["valid"] else f"{R}{record.get('failure_shape')}{X}"
        print(
            f"  {index:>2}/{len(todo)}  {key(cell):<28} {record['seconds']:>6.1f}s  "
            f"{record.get('total_tokens') or 0:>7,} tok  {mark}"
        )

    records = list(load_ledger().values())
    green = report(records)
    pin_fixture(records)
    return 0 if green else 1


def pin_fixture(records: list[dict[str, Any]]) -> None:
    """One exchange kept as a fixture, per the task's acceptance criteria: the smallest
    valid plan, because the point is the shape, not the size."""
    valid = [r for r in records if r.get("valid") and r.get("structured")]
    if not valid:
        print(f"\n{Y}no valid plan to pin as a fixture{X}")
        return
    smallest = min(valid, key=lambda r: len(json.dumps(r["structured"])))
    path = FIXTURES / "plan-executionplan.json"
    path.write_text(
        json.dumps(
            {
                "objective": smallest["objective"],
                "effort": smallest["effort"],
                "seconds": smallest["seconds"],
                "usage": {
                    k: smallest.get(k)
                    for k in ("input_tokens", "output_tokens", "thinking_tokens", "total_tokens")
                },
                "structured_output": smallest["structured"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\n{B}pinned{X} {path.relative_to(ROOT)}  {D}(the smallest valid plan){X}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
