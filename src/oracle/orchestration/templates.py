"""Deterministic plan templates — rung 2 of the planner ladder (PLANNER.md §6).

When no model will produce a valid plan, ORACLE still knows the *shape* of the work it
does: look first, change second, prove third, read it back. A template is that shape with
the objective left out, held as data in `config/plan_templates.yaml` and loaded the way
the capability registry and policy are loaded — versioned, human-edited, never writable
from a tool.

The load-bearing property is that **a template gets no privilege a vendor's plan does
not**. It is turned into the same `ExecutionPlan`, checked by the same `validate()`,
compiled by the same `compile_plan()`, and shown on the same graph card. A template
naming a role no agent holds is rejected exactly as a planner's plan would be, and the
ladder descends again. Nothing here is a second planning path; it is the first one with a
different author.

Two things a template deliberately cannot say:

* **the project.** ORACLE supplies the project it already resolved. A template that could
  name one would be a hallucinated path with a YAML file's authority behind it.
* **a tool.** `PlannedTask` forbids extra fields, so a template that tried would be
  rejected whole — the same line that keeps ADR-0021 true for a vendor.

Substitution is literal `str.replace` of `{objective}`, not `str.format`. The objective is
user text, and `format` is a small language nobody meant to expose to it: a stray `{` in
an objective would raise, and `{0.__class__}` is not a placeholder anybody designed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from oracle.logsink import get_logger
from oracle.orchestration.plan import ExecutionPlan, PlannedTask

log = get_logger(__name__)

DEFAULT_PATH = Path("config/plan_templates.yaml")

#: The one placeholder. Kept as a constant because it appears in the YAML, in the
#: substitution and in the tests, and three spellings of it is two too many.
PLACEHOLDER = "{objective}"


@dataclass(frozen=True)
class Template:
    name: str
    summary: str
    tasks: tuple[dict[str, Any], ...]
    #: Intents this shape answers. Empty means "only when nothing else matched".
    when: frozenset[str] = frozenset()
    default: bool = False


@dataclass(frozen=True)
class Templates:
    templates: tuple[Template, ...] = ()
    #: Why there are none, when there are none. Carried for the same reason the registry
    #: carries it: "unavailable because …" is an answer, "unavailable" is a shrug.
    problem: str | None = None

    def __len__(self) -> int:
        return len(self.templates)

    def get(self, name: str) -> Template | None:
        return next((t for t in self.templates if t.name == name), None)

    def choose(self, intent: str | None) -> Template | None:
        """The template for this intent, or the default, or nothing.

        Intent match first and declaration order after it: a template file is read
        top to bottom by the person maintaining it, and a selection rule they cannot
        predict from reading it is a rule they will fight."""
        if intent:
            for template in self.templates:
                if intent in template.when:
                    return template
        return next((t for t in self.templates if t.default), None)

    def plan_for(
        self, objective: str, *, intent: str | None = None, project: str | None = None
    ) -> ExecutionPlan | None:
        """Fill a template, or return `None` if none fits. **Validate the result** — this
        does not, on purpose: a template is a plan like any other and gets the same
        check, from the same caller, against the same registry."""
        template = self.choose(intent)
        if template is None:
            return None
        return fill(template, objective, project=project)


def fill(template: Template, objective: str, *, project: str | None = None) -> ExecutionPlan:
    """A template plus an objective is a plan. `project` comes from ORACLE's own
    resolution and is stamped on every task, because a template may not name one."""
    tasks = [
        PlannedTask.model_validate(
            {
                **{key: _substitute(value, objective) for key, value in body.items()},
                # After the substitution, never from it.
                "project": project,
            }
        )
        for body in template.tasks
    ]
    return ExecutionPlan(
        objective=objective,
        summary=template.summary,
        tasks=tasks,
        risks=[
            "This is a deterministic template, not a plan somebody thought about: it is "
            "the shape ORACLE uses when no planner is available, and it has not been "
            "matched to this objective by anything cleverer than an intent label."
        ],
    )


def _substitute(value: Any, objective: str) -> Any:
    if isinstance(value, str):
        return value.replace(PLACEHOLDER, objective)
    if isinstance(value, list):
        return [_substitute(item, objective) for item in value]
    return value


def single_task_plan(objective: str, *, project: str | None = None) -> ExecutionPlan:
    """Rung 3: one task, the objective verbatim (PLANNER.md §6).

    This is Phase 6's behaviour reached as a **defined state** rather than as a crash —
    ORACLE with no planner and no template is ORACLE as it shipped on 2026-08-24, which
    works. It is built in code rather than read from the template file because the rung
    below "the template file is unreadable" cannot itself be in the template file.

    `coder` because the single-delegation path this replaces is a coding delegation; the
    acceptance criterion is the only one ORACLE can state without a planner, and it is the
    one its own verifier checks anyway."""
    return ExecutionPlan(
        objective=objective,
        summary="A single delegation. No planner and no template were available, so the "
        "objective goes to one worker unchanged.",
        tasks=[
            PlannedTask(
                id="A",
                role="coder",
                objective=objective,
                project=project,
                acceptance=["the project's existing test suite has no new failures"],
                expected_outcome="diff",
            )
        ],
        risks=[
            "Nothing decomposed this objective. If it needs more than one step, the "
            "worker will have to decide that for itself inside one delegation."
        ],
    )


def load_templates(path: Path | None = None) -> Templates:
    """Read the template file. Never raises: an unreadable file means there are no
    templates, which is a routing fact the ladder already knows how to handle — the same
    fail-closed shape as the capability registry."""
    target = path or DEFAULT_PATH
    try:
        raw: Any = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        log.warning("templates.unreadable", path=str(target), error=str(exc))
        return Templates(problem=f"{target} could not be read: {exc}")
    if not isinstance(raw, dict):
        return Templates(problem=f"{target} is not a mapping")

    found: list[Template] = []
    for name, body in (raw.get("templates") or {}).items():
        body = body if isinstance(body, dict) else {}
        tasks = [t for t in (body.get("tasks") or []) if isinstance(t, dict)]
        if not tasks:
            # Check 0, the same one `validate()` makes first: a silently empty collection
            # passes every other check (the P6-T5 finding).
            log.warning("templates.empty", template=str(name))
            continue
        found.append(
            Template(
                name=str(name),
                summary=str(body.get("summary", "")).strip(),
                tasks=tuple(tasks),
                when=frozenset(str(w) for w in (body.get("when") or [])),
                default=bool(body.get("default", False)),
            )
        )
    log.info("templates.loaded", count=len(found), path=str(target))
    return Templates(templates=tuple(found))


@dataclass(frozen=True)
class Override:
    """An `agent_hint` the registry would not honour, recorded so the audit can say which
    rule overrode it (PLANNER.md §5)."""

    task_id: str
    role: str
    hinted: str
    chosen: str | None
    reason: str

    def as_audit(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "role": self.role,
            "hinted": self.hinted,
            "chosen": self.chosen,
            "reason": self.reason,
        }


def overridden_hints(plan: ExecutionPlan, registry: Any) -> list[Override]:
    """Every `agent_hint` in this plan that selection did not honour.

    Selection has always dropped these silently, which meant "the planner recommended an
    agent the policy forbids and was overridden" was true and *unreviewable*. This
    reports them so the composition layer can write the audit entry; it lives here rather
    than in `plan.py` so that the orchestration layer still imports nothing that audits
    (ARCHITECTURE.md — `AuditLog` is in the policy layer)."""
    from oracle.orchestration.plan import resolve_agent, task_kind

    overrides: list[Override] = []
    for planned in plan.tasks:
        if not planned.agent_hint:
            continue
        kind = task_kind(planned, registry)
        chosen = resolve_agent(planned, registry, kind)
        if chosen == planned.agent_hint:
            continue
        agent = registry.agents.get(planned.agent_hint)
        if agent is None:
            reason = f"{planned.agent_hint!r} is not a registered agent"
        elif planned.role not in agent.roles:
            reason = f"{planned.agent_hint!r} does not hold the {planned.role!r} role"
        elif agent.read_only:
            reason = f"{planned.agent_hint!r} is read-only and this task produces a diff"
        elif agent.locality == "local" and kind is not None:
            reason = f"{planned.agent_hint!r} is a local model and this task is a {kind}"
        else:  # pragma: no cover - defensive; the branches above are the known reasons
            reason = "the registry did not honour the hint"
        overrides.append(
            Override(
                task_id=planned.id,
                role=planned.role,
                hinted=planned.agent_hint,
                chosen=chosen,
                reason=reason,
            )
        )
    return overrides


__all__ = [
    "DEFAULT_PATH",
    "PLACEHOLDER",
    "Override",
    "Template",
    "Templates",
    "fill",
    "load_templates",
    "overridden_hints",
    "single_task_plan",
]
