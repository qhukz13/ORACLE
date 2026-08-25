"""What a planner is allowed to say, and what happens when it says something else.

The plan is the newest untrusted input in the system (ADR-0021) and the only one that
arrives shaped like instructions. These tests are the boundary: every case here was
either measured against a real vendor in the P6-T5 spike or is a specific thing a plan
must never be able to do.

The most important one is the shortest — `test_a_plan_may_not_name_a_tool`. Everything
else is validation; that one is the line between a to-do list and a privilege.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oracle.orchestration.models import TaskKind
from oracle.orchestration.plan import (
    ExecutionPlan,
    compile_plan,
    elevated_summary,
    parse,
    plan_schema,
    repair_prompt,
    validate,
)
from oracle.orchestration.registry import Registry, load_registry

REPO_REGISTRY = Path(__file__).resolve().parents[1] / "config" / "agents.yaml"
PROJECTS = {"oracle", "asterim"}


@pytest.fixture
def registry() -> Registry:
    """The shipped registry, not a fake: these tests are also the check that
    `config/agents.yaml` still says what the code expects."""
    return load_registry(REPO_REGISTRY)


def raw_plan(**overrides: object) -> dict:
    body: dict = {
        "objective": "make the auth tests pass",
        "summary": "one fix, then a check",
        "tasks": [
            {
                "id": "A",
                "role": "coder",
                "objective": "fix the 401 handling",
                "project": "oracle",
                "acceptance": ["pytest tests/test_auth.py passes"],
                "expected_outcome": "diff",
            },
            {
                "id": "B",
                "role": "reviewer",
                "objective": "review the change",
                "depends_on": ["A"],
                "expected_outcome": "verdict",
            },
        ],
        "risks": ["the failure may be in the fixture, not the code"],
    }
    body.update(overrides)
    return body


def plan_of(**overrides: object) -> ExecutionPlan:
    parsed, problems = parse(raw_plan(**overrides))
    assert parsed is not None, problems
    return parsed


# -- the line that matters -----------------------------------------------------


def test_a_plan_may_not_name_a_tool() -> None:
    """The whole of ADR-0021 in one assertion. `TaskSpec.tool`/`args` exist for tasks the
    supervisor authors; a plan that could set them would be a plan with execution
    authority. `extra="forbid"` is why an *unknown* field is rejected rather than
    trimmed — "trimmed" is exactly how `tool` would have arrived."""
    body = raw_plan()
    body["tasks"][0]["tool"] = "fs.write"  # type: ignore[index]
    body["tasks"][0]["args"] = {"path": "C:/Windows/System32/drivers/etc/hosts"}  # type: ignore[index]

    parsed, problems = parse(body)

    assert parsed is None
    assert any("tool" in p for p in problems), problems


def test_a_plan_may_not_invent_any_field_at_all() -> None:
    """Not a special case for `tool`: a planner that adds fields is a planner whose next
    addition might be one that matters."""
    body = raw_plan()
    body["tasks"][0]["shell"] = "rm -rf /"  # type: ignore[index]
    parsed, _ = parse(body)
    assert parsed is None


def test_a_compiled_task_carries_no_tool(registry: Registry) -> None:
    """The positive form: whatever the plan said, what reaches the table has no tool."""
    graph = compile_plan(plan_of(), registry)
    assert all(t.spec.tool is None and t.spec.args == {} for t in graph.tasks)


# -- validation, in PLANNER.md §2's order --------------------------------------


def test_a_good_plan_validates(registry: Registry) -> None:
    assert validate(plan_of(), registry, PROJECTS) == []


def test_check_zero_is_non_empty(registry: Registry) -> None:
    """The spike's finding: a vendor returned `status: SUCCESS` and a schema-valid plan
    whose `tasks` had been silently emptied by its own filter, while the prose beside it
    held six well-formed tasks. A schema-shaped answer is not a validated answer."""
    problems = validate(plan_of(tasks=[]), registry, PROJECTS)
    assert problems == ["the plan has no tasks"]


def test_an_unknown_role_is_an_error_not_a_lookup_that_missed(registry: Registry) -> None:
    body = raw_plan()
    body["tasks"][0]["role"] = "wizard"  # type: ignore[index]
    problems = validate(plan_of(tasks=body["tasks"]), registry, PROJECTS)
    assert any("'wizard' is not a registered role" in p for p in problems)


def test_a_role_no_agent_can_hold_is_rejected(registry: Registry) -> None:
    """`verifier` is deterministic — no model holds it — so a plan that hands it to a
    worker is asking for something that cannot be scheduled."""
    body = raw_plan()
    body["tasks"][0]["role"] = "verifier"  # type: ignore[index]
    problems = validate(plan_of(tasks=body["tasks"]), registry, PROJECTS)
    assert any("no agent that can hold it" in p for p in problems)


def test_a_hallucinated_project_never_becomes_a_path(registry: Registry) -> None:
    body = raw_plan()
    body["tasks"][0]["project"] = "../../etc"  # type: ignore[index]
    problems = validate(plan_of(tasks=body["tasks"]), registry, PROJECTS)
    assert any("is not a known project" in p for p in problems)


def test_a_dangling_dependency_names_the_missing_task(registry: Registry) -> None:
    body = raw_plan()
    body["tasks"][1]["depends_on"] = ["ghost"]  # type: ignore[index]
    problems = validate(plan_of(tasks=body["tasks"]), registry, PROJECTS)
    assert any("unknown task 'ghost'" in p for p in problems)


def test_a_cycle_is_reported_as_a_path(registry: Registry) -> None:
    body = raw_plan()
    body["tasks"][0]["depends_on"] = ["B"]  # type: ignore[index]
    problems = validate(plan_of(tasks=body["tasks"]), registry, PROJECTS)
    cycle = next(p for p in problems if p.startswith("cycle:"))
    assert "→" in cycle and "A" in cycle and "B" in cycle


def test_a_diff_task_without_acceptance_is_not_schedulable(registry: Registry) -> None:
    """Not because the criteria are the verification contract — they are not — but
    because a coding task nobody can check is a coding task nobody should start."""
    body = raw_plan()
    body["tasks"][0]["acceptance"] = []  # type: ignore[index]
    problems = validate(plan_of(tasks=body["tasks"]), registry, PROJECTS)
    assert any("not schedulable" in p for p in problems)


def test_too_many_tasks_is_a_planner_losing_the_thread(registry: Registry) -> None:
    many = [
        {
            "id": f"T{i}",
            "role": "researcher",
            "objective": "look at something",
            "expected_outcome": "report",
        }
        for i in range(13)
    ]
    problems = validate(plan_of(tasks=many), registry, PROJECTS)
    assert any("exceeds the limit" in p for p in problems)


def test_validation_reports_every_problem_not_the_first(registry: Registry) -> None:
    """The repair attempt is fed these. One problem per round trip would cost a round
    trip per typo."""
    body = raw_plan()
    body["tasks"][0]["role"] = "wizard"  # type: ignore[index]
    body["tasks"][0]["acceptance"] = []  # type: ignore[index]
    body["tasks"][1]["depends_on"] = ["ghost"]  # type: ignore[index]
    problems = validate(plan_of(tasks=body["tasks"]), registry, PROJECTS)
    assert len(problems) >= 3


# -- parsing what actually comes back ------------------------------------------


def test_a_json_string_is_accepted_because_vendors_differ() -> None:
    parsed, problems = parse(json.dumps(raw_plan()))
    assert parsed is not None and problems == []


def test_prose_around_the_json_is_not_guessed_at() -> None:
    """A planner that wrapped its answer in commentary is one whose next answer is worth
    asking for, not one worth reconstructing."""
    parsed, problems = parse("Here is the plan!\n" + json.dumps(raw_plan()))
    assert parsed is None and problems


def test_the_schema_is_generated_from_the_models() -> None:
    """Never hand-written (AGENTS.md): the thing requested and the thing validated cannot
    drift if there is only one of them."""
    schema = plan_schema()
    assert schema["required"] == ["objective", "summary", "tasks"] or set(schema["required"]) == {
        "objective",
        "summary",
        "tasks",
    }
    assert "$defs" in schema and "PlannedTask" in schema["$defs"]


def test_the_repair_prompt_names_the_actual_problems() -> None:
    prompt = repair_prompt(["the plan has no tasks", "A.role 'wizard' is not registered"])
    assert "no tasks" in prompt and "wizard" in prompt
    assert "Change nothing else" in prompt


# -- compilation ---------------------------------------------------------------


def test_plan_local_ids_do_not_leak_into_the_task_table(registry: Registry) -> None:
    """Two plans both calling a task "A" must not collide on one row."""
    graph = compile_plan(plan_of(), registry, root_id="tk_one")
    ids = {t.id for t in graph.tasks}
    assert ids == {"tk_one-a", "tk_one-b"}
    dependent = graph["tk_one-b"]
    assert dependent.depends_on == ("tk_one-a",), "the dependency was not remapped"


def test_the_outcome_decides_the_kind_not_the_plan(registry: Registry) -> None:
    graph = compile_plan(plan_of(), registry)
    kinds = {t.spec.role: t.kind for t in graph.tasks}
    assert kinds["coder"] is TaskKind.DELEGATION
    assert kinds["reviewer"] is TaskKind.VERIFY


def test_the_registry_resolves_the_agent_not_the_plan(registry: Registry) -> None:
    """A plan that could choose its executor would be choosing its own permissions."""
    graph = compile_plan(plan_of(), registry)
    coder = next(t for t in graph.tasks if t.spec.role == "coder")
    assert coder.agent == "claude"


def test_an_agent_hint_breaks_ties_and_nothing_more(registry: Registry) -> None:
    body = raw_plan()
    # A hint the registry honours: antigravity does hold `reviewer`.
    body["tasks"][1]["agent_hint"] = "antigravity"  # type: ignore[index]
    graph = compile_plan(plan_of(tasks=body["tasks"]), registry)
    assert next(t for t in graph.tasks if t.spec.role == "reviewer").agent == "antigravity"

    # A hint it does not: antigravity cannot hold `coder` (it cannot write — OQ-05).
    body = raw_plan()
    body["tasks"][0]["agent_hint"] = "antigravity"  # type: ignore[index]
    graph = compile_plan(plan_of(tasks=body["tasks"]), registry)
    assert next(t for t in graph.tasks if t.spec.role == "coder").agent == "claude"


def test_the_approval_card_lists_what_will_egress(registry: Registry) -> None:
    """The pipeline rule: the shape of the whole thing up front, so a person is not asked
    twelve questions they cannot compare."""
    rows = elevated_summary(compile_plan(plan_of(), registry).tasks)
    assert len(rows) == 2
    assert [r["egresses"] for r in rows] == [True, False]
    assert rows[0]["agent"] == "claude" and rows[0]["role"] == "coder"


# -- the registry itself -------------------------------------------------------


def test_the_shipped_registry_matches_the_measurement(registry: Registry) -> None:
    """`config/agents.yaml` is where OQ-20's answer lives now. If someone gives
    Antigravity the planner role back, this fails and points at the dev log."""
    assert registry.usable
    assert "planner" not in registry.agents["antigravity"].roles
    assert "planner" in registry.agents["claude"].roles
    assert registry.default_for("planner") is not None
    assert registry.default_for("planner").id == "claude"  # type: ignore[union-attr]
    assert registry.agents["antigravity"].read_only is True


def test_an_unreadable_registry_fails_closed(tmp_path: Path) -> None:
    """A registry that failed open would let a plan pick its own executor. Same instinct
    as policy's read-only lockdown."""
    missing = load_registry(tmp_path / "nope.yaml")
    assert not missing.usable
    assert missing.problem is not None
    assert missing.holders_of("coder") == []


def test_a_stale_default_is_ignored_rather_than_honoured(tmp_path: Path) -> None:
    """If `defaults:` and `roles:` disagree, the roles win — honouring the default would
    resurrect a decision a measurement already overturned."""
    path = tmp_path / "agents.yaml"
    path.write_text(
        "version: 1\n"
        "roles:\n  planner: {outcome: plan}\n  coder: {outcome: diff}\n"
        "agents:\n"
        "  antigravity: {adapter: agy, roles: [coder], cost: quota}\n"
        "  claude: {adapter: cli, roles: [planner], cost: subscription}\n"
        "defaults:\n  planner: antigravity\n",
        encoding="utf-8",
    )
    stale = load_registry(path)
    assert stale.default_for("planner") is not None
    assert stale.default_for("planner").id == "claude"  # type: ignore[union-attr]
