"""The VERIFY runner: ORACLE's own evidence, compared against a baseline.

The measurement that forced this design, from P6-T5:

| worktree | passed | failed |
|---|---|---|
| the delegate's | 583 | **28** |
| a clean worktree at the same base commit | 578 | **28** |

The same 28 tests fail in an *untouched* worktree of this repo — a fresh checkout has no
`.venv`, so every suite that spawns a binary dies. The delegate had added five passing
tests and broken nothing. **A verifier that reads "failures > 0" as failure would reject
every correct delegation this repo can produce.**

So verification is a *delta*: run the suite in the worker's workspace, run it once in a
pristine workspace at the same base, and report what changed. New failures fail the task;
pre-existing ones are the environment's problem and are reported as such.

Two rules that follow from taking that seriously:

* **No baseline, no verdict.** If the baseline cannot be obtained, this fails the task
  with a reason rather than guessing — a verifier that quietly falls back to a threshold
  is worse than one that admits it cannot tell.
* **The workspace comes from the dependency's row, not from the claim.** The task being
  verified recorded where its work is; the store is read for it. The row is the record.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oracle.integrations.workspace import create_worktree
from oracle.logsink import get_logger
from oracle.orchestration.models import Task, TaskError, TaskResult
from oracle.orchestration.store import TaskStore

log = get_logger(__name__)

#: `dev.run_tests` through the gate, as `DelegationService` already wires it: a path in,
#: the tool's own dict out. Injected rather than imported so this file composes the
#: executor instead of reaching for it.
RunTests = Callable[[Path], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class Counts:
    """What a suite run says, reduced to what a comparison needs."""

    passed: int
    failed: int
    total: int
    failures: frozenset[str]

    @classmethod
    def parse(cls, report: dict[str, Any] | None) -> Counts | None:
        """`None` when the tool did not actually run the suite. The distinction between
        "the tool returned" and "the tests ran" is the one that produced a false green in
        P6-T5's first verification attempt, so it is checked here rather than assumed."""
        detail = (report or {}).get("detail")
        if not isinstance(detail, dict) or "passed" not in detail:
            return None
        failures = detail.get("failures") or []
        names = {str(f.get("name")) for f in failures if isinstance(f, dict) and f.get("name")}
        return cls(
            passed=int(detail.get("passed", 0)),
            failed=int(detail.get("failed", 0)),
            total=int(detail.get("total", 0)),
            failures=frozenset(names),
        )


class BaselineCache:
    """One pristine suite run per graph, not per task.

    A baseline costs a full test run — three minutes on this repo — so it is taken once
    per root and reused. It is deliberately *not* cached across graphs: the base commit
    moves, and a stale baseline is a wrong answer delivered quickly.
    """

    def __init__(self, repo: Path, run_tests: RunTests) -> None:
        self._repo = repo
        self._run_tests = run_tests
        self._cache: dict[str, Counts | None] = {}
        self._lock = asyncio.Lock()

    async def counts_for(self, root_id: str) -> Counts | None:
        async with self._lock:
            if root_id in self._cache:
                return self._cache[root_id]
            counts = await self._measure(root_id)
            self._cache[root_id] = counts
            return counts

    async def _measure(self, root_id: str) -> Counts | None:
        worktree = None
        try:
            worktree = await asyncio.to_thread(create_worktree, self._repo, f"baseline-{root_id}")
            report = await self._run_tests(worktree.ws.path)
            counts = Counts.parse(report)
            log.info(
                "verify.baseline",
                root_id=root_id,
                passed=counts.passed if counts else None,
                failed=counts.failed if counts else None,
            )
            return counts
        except Exception as exc:  # a baseline that cannot be taken is a fact, not a crash
            log.warning("verify.baseline_failed", root_id=root_id, error=str(exc))
            return None
        finally:
            if worktree is not None:
                await asyncio.to_thread(worktree.discard)


async def _workspace_of_dependency(store: TaskStore, task: Task) -> tuple[str | None, str | None]:
    """Where the work being verified lives, read off the dependency's row."""
    for dep_id in task.depends_on:
        dependency = await store.load(dep_id)
        if dependency is None or dependency.result is None:
            continue
        workspace = dependency.result.evidence.get("workspace")
        if workspace:
            return str(workspace), dep_id
    return None, None


def make_verify_runner(
    store: TaskStore,
    run_tests: RunTests,
    baselines: BaselineCache,
) -> Any:
    """Bind the pieces into a `Runner`."""

    async def run(task: Task) -> TaskResult:
        workspace, dep_id = await _workspace_of_dependency(store, task)
        if workspace is None:
            return TaskResult(
                ok=False,
                summary="nothing to verify: no dependency recorded a workspace",
                error=TaskError(
                    kind="invalid_args",
                    message="a VERIFY task must depend on a task that produced a workspace",
                ),
            )
        observed = Counts.parse(await run_tests(Path(workspace)))
        if observed is None:
            return TaskResult(
                ok=False,
                summary="the suite did not run, so nothing was verified",
                evidence={"workspace": workspace, "verified": dep_id},
                error=TaskError(
                    kind="execution_failed",
                    message="dev.run_tests returned no counts",
                    # Worth one retry: a suite that failed to start is the machine being
                    # busy far more often than the code being wrong.
                    retryable=True,
                ),
            )
        baseline = await baselines.counts_for(task.root_id)
        if baseline is None:
            # The rule this whole module exists for: no baseline, no verdict.
            return TaskResult(
                ok=False,
                summary="no baseline: refusing to judge failures that may predate this work",
                evidence={
                    "workspace": workspace,
                    "verified": dep_id,
                    "observed": {"passed": observed.passed, "failed": observed.failed},
                },
                error=TaskError(
                    kind="execution_failed",
                    message="the baseline suite run could not be taken",
                ),
            )

        new_failures = sorted(observed.failures - baseline.failures)
        fixed = sorted(baseline.failures - observed.failures)
        evidence = {
            "workspace": workspace,
            "verified": dep_id,
            "observed": {"passed": observed.passed, "failed": observed.failed},
            "baseline": {"passed": baseline.passed, "failed": baseline.failed},
            "new_failures": new_failures,
            "fixed": fixed,
            "delta_passed": observed.passed - baseline.passed,
        }
        if new_failures:
            return TaskResult(
                ok=False,
                summary=f"{len(new_failures)} test(s) that passed before this work now fail",
                evidence=evidence,
                error=TaskError(
                    kind="execution_failed",
                    message="; ".join(new_failures[:5]),
                    retryable=False,
                ),
            )
        return TaskResult(
            ok=True,
            summary=(
                f"no new failures ({observed.failed} pre-existing, "
                f"{observed.passed - baseline.passed:+d} passing)"
            ),
            evidence=evidence,
        )

    return run
