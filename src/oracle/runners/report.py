"""The REPORT runner: the local model writes the digest, and nothing leaves the machine.

P8-T1 shipped `REPORT` mapped to the delegation runner and said so out loud, because
[PLANNER.md §4](../../../docs/PLANNER.md) has always held that a summarizer is never
routed to a cloud agent — sending a paragraph of ORACLE's own measurements to a vendor to
have it rephrased is quota spent on nothing, plus an egress nobody needed. This is that
admission answered.

Three properties, in the order they matter:

* **It cannot egress.** The only thing it talks to is `LLMProvider`, which in this daemon
  is Ollama on localhost (ADR-0004). There is no adapter here, no worktree, no packet, and
  therefore no egress preview — because there is no egress.
* **It summarises evidence, not claims.** The dependencies' `TaskResult.claim` is what
  each worker said about its own work; it is not read here. A local model writing
  ORACLE's report from a worker's prose is the same injection shape as the replan prompt,
  one step further from anybody checking. The UI already shows claims, labelled and
  attributed.
* **It degrades to plain text rather than failing.** No provider, a provider that is down,
  a model that returns nothing: the report becomes the deterministic listing of what
  ORACLE measured. A report task that failed the graph because Ollama was not running
  would be a summary outage reported as a work outage.

It reads its dependencies from the **task store**, never from anything passed alongside:
the row is the record (ORCHESTRATION.md §2), and a runner that trusted an in-memory
hand-off would be reporting on a graph that might have moved on.
"""

from __future__ import annotations

import json
from typing import Any

from oracle.llm.types import CallType, CompletionRequest, Message
from oracle.logsink import get_logger
from oracle.orchestration.models import Task, TaskResult

log = get_logger(__name__)

#: Evidence keys worth putting in front of a model, in the order a person reads them.
#: An allowlist rather than "everything": evidence dicts carry absolute paths and vendor
#: session ids, and a summary is not the place to widen what a model sees.
REPORTED = (
    "outcome",
    "exit_code",
    "diff_lines",
    "untracked",
    "branch",
    "harvest_commit",
    "observed",
    "baseline",
    "new_failures",
    "fixed",
    "delta_passed",
    "verified",
    "ran",
)

SYSTEM = (
    "You are ORACLE's reporter. You are given ORACLE's OWN measurements of tasks it just "
    "ran — diffs it counted, tests it executed, branches it wrote. Write a short, plain "
    "report for the person who asked for the work.\n"
    "Rules:\n"
    "- State only what the measurements say. Do not infer that something works because a "
    "task succeeded, and do not claim anything the numbers do not.\n"
    "- Name what failed, and what never ran because of it.\n"
    "- Say where the work is: branch names and commits are the useful part.\n"
    "- No preamble, no offer to help further. Six sentences at most."
)


def _facts(task: Task, dependencies: list[Task]) -> list[dict[str, Any]]:
    return [
        {
            "task": dep.id,
            "role": dep.spec.role,
            "objective": dep.spec.objective,
            "status": str(dep.status),
            "summary": dep.result.summary if dep.result else "",
            # `claim` is deliberately absent. See the module docstring.
            "measured": {
                key: dep.result.evidence[key]
                for key in REPORTED
                if dep.result and key in dep.result.evidence
            }
            if dep.result
            else {},
        }
        for dep in dependencies
    ]


def plain_report(objective: str, facts: list[dict[str, Any]]) -> str:
    """The report ORACLE can always write: what it measured, listed. Deterministic, and
    the floor the model version is only ever an improvement on."""
    if not facts:
        return f"{objective}\n\nNothing ran that this report depends on."
    lines = [objective, ""]
    for fact in facts:
        lines.append(f"- {fact['task']} ({fact['role']}): {fact['status']}")
        if fact["summary"]:
            lines.append(f"    {fact['summary']}")
        for key, value in fact["measured"].items():
            lines.append(f"    {key}: {value}")
    return "\n".join(lines)


def make_report_runner(provider: Any, store: Any) -> Any:
    """Bind the local model and the task store into a `Runner`.

    `provider` may be `None` — on a machine with no Ollama, or with `llm_enabled=False`,
    the reporter is the deterministic one and the graph still finishes."""

    async def run(task: Task) -> TaskResult:
        dependencies = [row for row in [await store.load(dep) for dep in task.depends_on] if row]
        facts = _facts(task, dependencies)
        floor = plain_report(task.spec.objective, facts)
        evidence: dict[str, Any] = {
            "reported_on": [dep.id for dep in dependencies],
            "generated_by": "template",
        }
        if provider is None:
            return TaskResult(ok=True, summary=floor, evidence=evidence)

        try:
            completion = await provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=SYSTEM),
                        Message(
                            role="user",
                            content=(
                                f"The objective was: {task.spec.objective}\n\n"
                                f"ORACLE measured:\n{json.dumps(facts, indent=2, default=str)}"
                            ),
                        ),
                    ],
                    call_type=CallType.SUMMARIZE,
                    max_tokens=600,
                )
            )
        except Exception as exc:
            # A summary outage is not a work outage.
            log.warning("report.provider_failed", task_id=task.id, error=str(exc))
            evidence["degraded"] = f"the local model was unavailable: {exc}"
            return TaskResult(ok=True, summary=floor, evidence=evidence)

        text = (completion.text or "").strip()
        if not text:
            evidence["degraded"] = "the local model returned nothing"
            return TaskResult(ok=True, summary=floor, evidence=evidence)
        evidence["generated_by"] = "local"
        # The listing rides along regardless: the model wrote the prose, ORACLE wrote the
        # numbers, and the numbers are the part that has to survive a rephrasing.
        evidence["measurements"] = facts
        return TaskResult(ok=True, summary=text, evidence=evidence)

    return run


__all__ = ["REPORTED", "make_report_runner", "plain_report"]
