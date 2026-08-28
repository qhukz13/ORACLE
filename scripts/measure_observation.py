#!/usr/bin/env python
"""OQ-24: does observing every project fit the 3-5 second glance budget?

    uv run python scripts/measure_observation.py

[OQ-24](docs/OPEN_QUESTIONS.md#oq-24) bounds Phase 12's sidebar: observed state is never
cached (PROJECT_STATE.md par.2), so showing branch and dirty count for N projects costs
N x (`git.status` + `git.log --limit 1`), each a toolhost IPC round trip plus a `git`
subprocess. Warm-IPC-p50 arithmetic says ~13 projects/s -- but arithmetic is exactly what
ADR-0004 got wrong about qwen3.5:2b, so this script measures the real fan-out instead: the
real registered set (the 7 known candidates plus ORACLE itself), the real policy, and the
same `ToolExecutor -> ToolHost -> git` path the daemon takes, spawned fresh the way
`oracled`'s prewarm spawns it. The toolhost serialises invocations (one at a time, by
design), so the sequential total measured here *is* the fan-out cost -- concurrency in the
caller cannot hide it.

Three things this script will not do:

* **It does not write to any database.** `Project` rows are constructed in memory and never
  stored. The only file written is a throwaway audit log in a temp directory -- the audit
  sink is mandatory on the executor, and leaving the gate in is the point: its cost is part
  of the number the sidebar would pay.
* **It does not bypass the gate.** `observe()` is called exactly as the projects detail
  endpoint calls it, policy evaluation and TOCTOU re-check included.
* **It does not pretend cold means cold disk.** Cold here is a freshly spawned toolhost and
  the first observation this process makes; the OS file cache holds whatever the machine
  was doing before. A colder number would need a reboot -- and the number is already
  pessimistic, because an OQ-18 ONNX eval is deliberately running on all cores (below
  normal priority) while this measures.

Error is a field, never an exception -- the same rule as `ProjectObservation`: a root that
does not exist, or a directory that is not a repository, renders as a row that says so.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oracle.config import get_settings
from oracle.core.events import new_id
from oracle.core.project_state import Project, ProjectObservation, observe
from oracle.core.projects import detect_project, discover_projects
from oracle.logsink import configure
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.toolhost import ToolHost
from oracle.tools import ToolExecutor, build_registry

ROOT = Path(__file__).resolve().parents[1]

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"

#: The real project set (OQ-24: "at the real project count"). The seven candidates the
#: owner considers projects, plus ORACLE itself -- an eighth real repository that lives
#: under the same root. Names, not paths: each becomes `projects_root / name`, exactly as
#: registration would build it. A name whose directory is gone stays in the list on
#: purpose -- the sidebar has to render that row too, and its cost is part of the answer.
PROJECT_NAMES = (
    "Asterim",
    "AsterimDesign",
    "GameRecs",
    "GrowAMonster",
    "MonsterGarden",
    "ORACLE",
    "Source2DemViewer",
    "asterim-pipeline",
)

WARM_PASSES = 2
SPLIT_REPS = 3
GLANCE_BUDGET_MS = (3_000.0, 5_000.0)  # VISION.md par.2: 3-5 s to understand the screen
FANOUT_BUDGET_MS = 1_000.0  # OQ-24's arithmetic strawman: ~13 projects ~= 1 s


@dataclass
class RowResult:
    project: Project
    #: ms per pass: [cold, warm1, warm2]. Filled sequentially.
    passes: list[float] = field(default_factory=list)
    state: str = ""
    is_repo: bool = False

    @property
    def warm_ms(self) -> float:
        return statistics.median(self.passes[1:]) if len(self.passes) > 1 else float("nan")


def _describe(obs: ProjectObservation) -> str:
    if obs.error:
        # The full error names the absolute path; the row already names the project.
        short = obs.error.replace("is not inside a git repository", "").strip()
        if short != obs.error:
            return "error: not a git repository"
        return f"error: {obs.error}"
    bits = [obs.branch or "?"]
    if obs.ahead or obs.behind:
        bits.append(f"+{obs.ahead}/-{obs.behind}")
    bits.append("clean" if obs.clean else f"{obs.dirty} dirty")
    if obs.detected:
        bits.append("/".join(obs.detected.kinds))
    return "  ".join(bits)


async def _timed_observe(executor: ToolExecutor, row: RowResult) -> float:
    """One observation, timed. A crash becomes a row that says so -- never an exception,
    because the surface this stands in for has to render something for every project."""
    t0 = time.perf_counter()
    try:
        obs = await observe(executor, row.project)
    except Exception as exc:  # the ProjectObservation rule, applied to the harness itself
        ms = (time.perf_counter() - t0) * 1000
        row.state = f"harness error: {exc!r}"[:60]
        return ms
    ms = (time.perf_counter() - t0) * 1000
    row.state = _describe(obs)[:60]
    row.is_repo = obs.is_repo
    return ms


async def _split(executor: ToolExecutor, project: Project) -> dict[str, float]:
    """The observation's three parts, timed separately (warm, median of SPLIT_REPS):
    in-process marker detection, `git.status`, and `git.log --limit 1`."""
    path = str(project.root)
    out: dict[str, float] = {}

    reps: list[float] = []
    for _ in range(SPLIT_REPS):
        t0 = time.perf_counter()
        detect_project(project.root, project.name)
        reps.append((time.perf_counter() - t0) * 1000)
    out["detect_project (in-process)"] = statistics.median(reps)

    status_ok = False
    reps = []
    for _ in range(SPLIT_REPS):
        t0 = time.perf_counter()
        outcome = await executor.execute("git.status", {"path": path})
        reps.append((time.perf_counter() - t0) * 1000)
        status_ok = outcome.ok
    label = "git.status (toolhost IPC)" if status_ok else "git.status -> error (toolhost IPC)"
    out[label] = statistics.median(reps)

    if status_ok:  # observe() only pays for git.log where git.status succeeded
        reps = []
        for _ in range(SPLIT_REPS):
            t0 = time.perf_counter()
            await executor.execute("git.log", {"path": path, "limit": 1})
            reps.append((time.perf_counter() - t0) * 1000)
        out["git.log --limit 1 (toolhost IPC)"] = statistics.median(reps)

    return out


def _verdict_line(label: str, total_ms: float, budget_ms: float) -> None:
    fits = total_ms <= budget_ms
    mark, word = (G, "FITS") if fits else (R, "MISSES")
    print(f"  {label:<44}{total_ms:>9.1f} ms  vs {budget_ms:>6.0f} ms  {mark}{word}{X}")


async def _measure(executor: ToolExecutor, host: ToolHost, rows: list[RowResult]) -> int:
    # -- spawn, timed: the cost oracled's prewarm pays once at boot -------------------
    t0 = time.perf_counter()
    await host.start()
    spawn_ms = (time.perf_counter() - t0) * 1000
    print(f"\n{B}toolhost spawn{X} (paid once at boot by oracled's prewarm): {spawn_ms:.1f} ms")

    # -- three sequential passes: cold, then warm x2 ----------------------------------
    pass_wall: list[float] = []
    pass_calls: list[int] = []
    for i in range(1 + WARM_PASSES):
        calls_before = host.stats.calls
        t0 = time.perf_counter()
        for row in rows:
            row.passes.append(await _timed_observe(executor, row))
        pass_wall.append((time.perf_counter() - t0) * 1000)
        pass_calls.append(host.stats.calls - calls_before)
        kind = "cold" if i == 0 else f"warm{i}"
        per_call = pass_wall[-1] / pass_calls[-1] if pass_calls[-1] else 0.0
        print(
            f"{D}  pass {i + 1} ({kind}): {pass_wall[-1]:.1f} ms wall, "
            f"{pass_calls[-1]} toolhost invocations (~{per_call:.0f} ms each){X}"
        )

    # -- the table --------------------------------------------------------------------
    print(f"\n{B}per-project observation cost{X} (ms; warm = median of {WARM_PASSES} passes)")
    hdr = f"  {'project':<18}{'cold':>9}{'warm1':>9}{'warm2':>9}{'warm':>9}   state"
    print(hdr)
    print("  " + "-" * (len(hdr) + 24))
    for row in rows:
        p = row.passes
        print(
            f"  {row.project.name:<18}{p[0]:>9.1f}{p[1]:>9.1f}{p[2]:>9.1f}"
            f"{row.warm_ms:>9.1f}   {row.state}"
        )
    cold_total = sum(r.passes[0] for r in rows)
    warm_total = sum(r.warm_ms for r in rows)
    print("  " + "-" * (len(hdr) + 24))
    print(
        f"  {'total (' + str(len(rows)) + ' projects)':<18}{cold_total:>9.1f}"
        f"{sum(r.passes[1] for r in rows):>9.1f}{sum(r.passes[2] for r in rows):>9.1f}"
        f"{warm_total:>9.1f}"
    )

    # -- the worst project, decomposed ------------------------------------------------
    worst = max(rows, key=lambda r: r.warm_ms)
    targets = [worst]
    repos = [r for r in rows if r.is_repo]
    if repos and not worst.is_repo:
        # The status-vs-log split OQ-24 asks about needs a repository; the worst row may
        # not be one, so decompose the worst repository as well.
        targets.append(max(repos, key=lambda r: r.warm_ms))
    for target in targets:
        print(
            f"\n{B}worst {'repo' if target.is_repo else 'project'} split{X} ({target.project.name}, warm, median of {SPLIT_REPS}):"
        )
        for label, ms in (await _split(executor, target.project)).items():
            print(f"  {label:<38}{ms:>9.1f} ms")

    # -- verdict ----------------------------------------------------------------------
    lo, hi = GLANCE_BUDGET_MS
    print(f"\n{B}verdict{X}")
    _verdict_line("warm fan-out vs OQ-24's ~1 s arithmetic", warm_total, FANOUT_BUDGET_MS)
    _verdict_line("cold fan-out vs 3 s glance floor", cold_total, lo)
    _verdict_line("cold fan-out vs 5 s glance ceiling", cold_total, hi)
    _verdict_line("spawn + cold fan-out vs 5 s ceiling", spawn_ms + cold_total, hi)
    print(
        f"{D}  spawn+cold is the unprewarmed worst case; oracled prewarms at boot, so the"
        f" glance normally pays the cold row, not the spawn.{X}"
    )
    print(
        f"{D}  the toolhost handles one invocation at a time, so these sequential totals"
        f" are the real fan-out cost; parallel callers would queue.{X}"
    )
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    # "error", not "warning": three of the real projects are not git repositories, so
    # every pass would print nine expected `tool.failed` warnings over the table.
    configure(None, "error")  # the table is the output; the log is not

    settings = get_settings()
    projects_root = settings.projects_root
    if not projects_root.is_dir():
        print(f"{R}projects root {projects_root} does not exist; nothing to measure{X}")
        return 2
    policy_path = settings.policy_path
    if not policy_path.is_absolute():
        policy_path = ROOT / policy_path

    candidates = discover_projects(projects_root)
    missing = [n for n in PROJECT_NAMES if n not in candidates]

    print(f"{B}OQ-24 -- observing every project vs the 3-5 s glance budget{X}")
    print(f"  projects root: {projects_root}   policy: {policy_path.relative_to(ROOT)}")
    print(
        f"  discovered candidates: {len(candidates)}   measured (registered set): "
        f"{len(PROJECT_NAMES)}"
    )
    if missing:
        print(f"  {Y}roots absent on disk (rows will say so): {', '.join(missing)}{X}")
    print(
        f"{Y}  machine context: an OQ-18 ONNX eval is running on all {os.cpu_count()} cores"
        f" at below-normal priority (deliberate). Every number here is measured under that"
        f" load and is an upper bound, not a typical value.{X}"
    )

    #: In-memory rows only -- registration is a database write and this is a read-only
    #: measurement. `Project` is a plain pydantic model, so a row is just a value.
    rows = [
        RowResult(Project(id=new_id("pj"), name=name, root=projects_root / name))
        for name in PROJECT_NAMES
    ]

    with tempfile.TemporaryDirectory(prefix="oq24-audit-") as tmp:
        host = ToolHost(cwd=projects_root)  # exactly how oracled builds it
        executor = ToolExecutor(
            build_registry(),
            PolicyEngine(load_policy(policy_path)),
            AuditLog(Path(tmp) / "audit.jsonl"),
            host=host,
        )

        async def run() -> int:
            try:
                return await _measure(executor, host, rows)
            finally:
                await host.stop()

        return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
