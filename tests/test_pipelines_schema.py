"""The pipeline file format, and the five features it deliberately does not have.

PIPELINES.md §1 sets a litmus — *"if a pipeline needs branching logic and variables, it
wants to be a script"* — and then §2's own example quietly breaks three of its own rules.
Each test here that expects a rejection is one of those, pinned so the rejection is a
decision somebody has to argue with rather than a gap somebody fills in.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from oracle.orchestration.graph import MAX_GRAPH_SIZE
from oracle.pipelines.models import ParamSpec, Pipeline, StepSpec

MINIMAL = {
    "version": 1,
    "name": "check",
    "steps": [{"id": "status", "tool": "git.status", "with": {"path": "{{ project.root }}"}}],
}


def build(**overrides: object) -> Pipeline:
    return Pipeline.model_validate({**MINIMAL, **overrides})


class TestTheSchemaIsGenerated:
    def test_nothing_hand_writes_a_json_schema(self) -> None:
        """AGENTS.md: schemas are generated from the pydantic models, never duplicated."""
        schema = Pipeline.model_json_schema()
        assert schema["properties"]["steps"]
        assert "version" in schema["required"]

    def test_the_two_shipped_pipelines_parse(self) -> None:
        """The dogfooding claim in PIPELINES.md §7 is only worth making if the files
        actually load. Skipped until P10-T6 writes them."""
        from pathlib import Path

        directory = Path("config/pipelines")
        if not directory.is_dir():
            pytest.skip("config/pipelines lands in P10-T6")
        files = sorted(directory.glob("*.yaml"))
        assert files, "P10-T6 ships asterim-check and oracle-selfcheck"


class TestRefusals:
    """Five noes, each of which is a `yes` somebody could write tomorrow."""

    def test_a_step_result_is_not_addressable(self) -> None:
        """Not a schema rule — the *template* refuses it, because `with:` is free text
        until it is rendered. Asserted here so the pairing is visible from the schema."""
        from oracle.pipelines.template import PipelineError, substitute

        with pytest.raises(PipelineError, match="approval card"):
            substitute("{{ steps.build.log_path }}", {"params": {}, "project": {}})

    def test_on_failure_ask_is_refused(self) -> None:
        """PIPELINES.md §3 offers `ask` nine lines after saying "never a prompt mid-run".
        The document contradicts itself; the security model breaks the tie."""
        with pytest.raises(ValidationError, match="on_failure"):
            build(steps=[{**MINIMAL["steps"][0], "on_failure": "ask"}])  # type: ignore[index]

    def test_retry_cannot_say_which_failures_are_retryable(self) -> None:
        """§3: "the tool decides, not the pipeline author". `runners/tool.py` holds that
        judgement, and retrying a non-idempotent step is a data-loss bug."""
        with pytest.raises(ValidationError):
            build(steps=[{**MINIMAL["steps"][0], "retry": {"max": 1, "on": ["timeout"]}}])  # type: ignore[index]

    def test_capture_junit_is_refused(self) -> None:
        """`dev.run_tests` parses in-process and writes a text blob; there is no junit
        file on disk for §2's example to capture."""
        with pytest.raises(ValidationError):
            build(artifacts=[{"from": "status", "capture": "junit", "as": "x.xml"}])

    def test_more_steps_than_the_graph_allows_is_refused_here_not_there(self) -> None:
        """It would be refused by `orchestration.graph.validate` anyway. Catching it at
        parse time is what buys the line number PIPELINES.md §3 asks for."""
        steps = [{"id": f"s{i}", "tool": "git.status"} for i in range(MAX_GRAPH_SIZE + 1)]
        with pytest.raises(ValidationError, match="exceeds the graph ceiling"):
            build(steps=steps)


class TestUnknownFieldsAreRefusedEverywhere:
    @pytest.mark.parametrize(
        "payload",
        [
            {"cron": "0 * * * *"},
            {"steps": [{"id": "a", "tool": "git.status", "shell": "rm -rf /"}]},
            {
                "artifacts": [
                    {"from": "status", "capture": "stdout", "as": "a", "to": "D:/elsewhere"}
                ]
            },
        ],
    )
    def test_extra_forbid(self, payload: dict[str, object]) -> None:
        """`PlannedTask` has the same rule for the same reason: a field nobody wrote down
        is a field somebody is trying to smuggle."""
        with pytest.raises(ValidationError):
            build(**payload)


class TestNamesAndLabels:
    @pytest.mark.parametrize("name", ["../evil", "Check", "a b", "", "x" * 64, "1check"])
    def test_a_bad_pipeline_name_is_refused(self, name: str) -> None:
        with pytest.raises(ValidationError):
            build(name=name)

    @pytest.mark.parametrize("label", ["../../evil", "C:/x", "a/b", "", ".hidden", "x" * 65])
    def test_an_artifact_label_cannot_become_a_path(self, label: str) -> None:
        """Validated as a label at parse time so that if a later version ever writes it
        to disk, the traversal question was settled long before the write."""
        with pytest.raises(ValidationError):
            build(artifacts=[{"from": "status", "capture": "stdout", "as": label}])

    @pytest.mark.parametrize("tool", ["gitstatus", "git.", ".status", "Git.Status", "git status"])
    def test_a_tool_id_must_look_like_one(self, tool: str) -> None:
        with pytest.raises(ValidationError):
            build(steps=[{"id": "a", "tool": tool}])

    def test_duplicate_step_ids_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="duplicate step id"):
            build(steps=[{"id": "a", "tool": "git.status"}, {"id": "a", "tool": "dev.lint"}])

    def test_an_artifact_must_capture_from_a_real_step(self) -> None:
        with pytest.raises(ValidationError, match="not a step"):
            build(artifacts=[{"from": "nope", "capture": "stdout", "as": "a.log"}])


class TestParams:
    def test_a_default_is_required_because_a_run_takes_no_arguments(self) -> None:
        with pytest.raises(ValidationError):
            ParamSpec.model_validate({"type": "bool"})

    def test_an_enum_needs_choices_and_a_default_among_them(self) -> None:
        with pytest.raises(ValidationError, match="needs choices"):
            ParamSpec.model_validate({"type": "enum", "default": "a"})
        with pytest.raises(ValidationError, match="is not one of"):
            ParamSpec.model_validate({"type": "enum", "default": "c", "choices": ["a", "b"]})
        assert ParamSpec.model_validate({"type": "enum", "default": "a", "choices": ["a", "b"]})

    def test_choices_on_a_non_enum_is_a_mistake_worth_naming(self) -> None:
        with pytest.raises(ValidationError, match="only meaningful for an enum"):
            ParamSpec.model_validate({"type": "bool", "default": True, "choices": ["a"]})


class TestWhatItDoesAccept:
    def test_the_example_from_the_spec_with_its_errors_corrected(self) -> None:
        """PIPELINES.md §2's example, minus the three things it gets wrong: `oracle.report`
        (no such tool), `retry.on` (the tool decides), and `{{ project }}` (ambiguous
        between a name and a path). What is left is a real pipeline."""
        pipeline = Pipeline.model_validate(
            {
                "version": 1,
                "name": "asterim-check",
                "description": "Full health check before pushing Asterim.",
                "project": "Asterim",
                "params": {"skip_frontend": {"type": "bool", "default": False}},
                "steps": [
                    {"id": "status", "tool": "git.status", "with": {"path": "{{ project.root }}"}},
                    {
                        "id": "backend_tests",
                        "tool": "dev.run_tests",
                        "with": {"path": "{{ project.root }}", "filter": "apps/server"},
                        "timeout": 300,
                        "on_failure": "abort",
                    },
                    {
                        "id": "frontend_tests",
                        "tool": "dev.run_tests",
                        "with": {"path": "{{ project.root }}", "filter": "apps/web"},
                        "when": "not params.skip_frontend",
                        "on_failure": "continue",
                    },
                    {
                        "id": "build",
                        "tool": "dev.build",
                        "with": {"path": "{{ project.root }}"},
                        "timeout": 600,
                        "retry": {"max": 1},
                    },
                ],
                "artifacts": [{"from": "build", "capture": "stdout", "as": "build.log"}],
            }
        )
        assert [s.id for s in pipeline.steps] == [
            "status",
            "backend_tests",
            "frontend_tests",
            "build",
        ]
        assert pipeline.steps[2].on_failure == "continue"
        assert pipeline.steps[3].retry is not None
        assert pipeline.steps[3].retry.max == 1

    def test_with_is_readable_under_its_alias_and_its_name(self) -> None:
        step = StepSpec.model_validate({"id": "a", "tool": "git.status", "with": {"path": "."}})
        assert step.with_ == {"path": "."}
