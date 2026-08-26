"""Discovery, line numbers, and what a project may not do to another project.

Two properties this file exists for, both from PIPELINES.md §3's *"a typo in step 5 must
not be discovered after step 4 has already pushed a branch"*:

* nothing here raises — a broken file is a `Problem`, not an exception that stops the
  daemon or hides the pipelines that do load;
* every `Problem` carries the **line a person has to go and look at**, which is not the
  same thing as the dotted path pydantic reports.
"""

from __future__ import annotations

from pathlib import Path

from oracle.pipelines import bind_params
from oracle.pipelines.loader import Source, discover, load_file
from oracle.pipelines.models import Pipeline
from oracle.pipelines.template import PipelineError

GOOD = """\
version: 1
name: check
project: Asterim
params:
  skip_frontend: { type: bool, default: false }
steps:
  - id: status
    tool: git.status
    with: { path: "{{ project.root }}" }
  - id: tests
    tool: dev.run_tests
    with: { path: "{{ project.root }}" }
"""


def write(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


class TestLineNumbers:
    def test_a_bad_tool_id_reports_the_line_it_is_on(self, tmp_path: Path) -> None:
        """The whole point of the custom loader. `steps.1.tool` is a path through a parsed
        document; `check.yaml:10` is a place."""
        text = (
            "version: 1\n"  # 1
            "name: check\n"  # 2
            "steps:\n"  # 3
            "  - id: one\n"  # 4
            "    tool: git.status\n"  # 5
            "  - id: two\n"  # 6
            "    tool: not a tool id\n"  # 7
        )
        path = write(tmp_path, "check.yaml", text)
        loaded, problems = load_file(path, source=Source.GLOBAL)
        assert loaded is None
        assert len(problems) == 1
        assert problems[0].line == 6, f"expected the step's line, got {problems[0]}"
        assert "tool" in problems[0].message
        assert str(problems[0]).startswith("check.yaml:6:")

    def test_a_missing_required_field_reports_its_step_not_the_file(self, tmp_path: Path) -> None:
        """A missing key has no line of its own, so the useful answer is the line the
        step starts on — which is where the fix goes."""
        text = "version: 1\nname: check\nsteps:\n  - id: one\n    tool: git.status\n  - id: two\n"
        path = write(tmp_path, "check.yaml", text)
        _, problems = load_file(path, source=Source.GLOBAL)
        assert problems[0].line == 6

    def test_malformed_yaml_is_a_problem_not_an_exception(self, tmp_path: Path) -> None:
        path = write(tmp_path, "broken.yaml", "version: 1\nname: [unclosed\n")
        loaded, problems = load_file(path, source=Source.GLOBAL)
        assert loaded is None
        assert problems and "not valid YAML" in problems[0].message

    def test_a_file_that_cannot_be_read_is_a_problem_too(self, tmp_path: Path) -> None:
        loaded, problems = load_file(tmp_path / "absent.yaml", source=Source.GLOBAL)
        assert loaded is None
        assert problems and "cannot be read" in problems[0].message

    def test_a_document_that_is_not_a_mapping_is_refused(self, tmp_path: Path) -> None:
        path = write(tmp_path, "list.yaml", "- one\n- two\n")
        loaded, problems = load_file(path, source=Source.GLOBAL)
        assert loaded is None and "not a mapping" in problems[0].message

    def test_every_problem_is_reported_not_only_the_first(self, tmp_path: Path) -> None:
        """`plan.validate()` returns every problem for the same reason: a person fixing
        one error at a time through a five-second parse is a person who stops."""
        text = "version: 1\nname: Check\nsteps:\n  - id: ONE\n    tool: nope\n"
        path = write(tmp_path, "many.yaml", text)
        _, problems = load_file(path, source=Source.GLOBAL)
        assert len(problems) >= 3


class TestAProjectCannotReachAnotherProject:
    def test_a_project_file_is_pinned_to_its_own_project(self, tmp_path: Path) -> None:
        """A pipeline under `Asterim/.oracle/pipelines/` is repository content. If it
        could name ORACLE, cloning a repo would be enough to get work run somewhere
        else."""
        path = write(tmp_path, "evil.yaml", GOOD.replace("project: Asterim", "project: ORACLE"))
        loaded, problems = load_file(path, source=Source.PROJECT, pinned_project="Asterim")
        assert loaded is None
        assert "may only act on that project" in problems[0].message

    def test_a_project_file_with_no_project_key_inherits_where_it_was_found(
        self, tmp_path: Path
    ) -> None:
        path = write(tmp_path, "ok.yaml", GOOD.replace("project: Asterim\n", ""))
        loaded, problems = load_file(path, source=Source.PROJECT, pinned_project="Asterim")
        assert not problems and loaded is not None
        assert loaded.project == "Asterim"

    def test_a_global_file_may_name_any_project(self, tmp_path: Path) -> None:
        """`config/pipelines/` sits beside `policy.yaml`: owner-authored, versioned, and
        edited by a human."""
        path = write(tmp_path, "ok.yaml", GOOD)
        loaded, problems = load_file(path, source=Source.GLOBAL)
        assert not problems and loaded is not None and loaded.project == "Asterim"


class TestDiscovery:
    def test_it_finds_global_and_project_files(self, tmp_path: Path) -> None:
        config = tmp_path / "config" / "pipelines"
        write(config, "a.yaml", GOOD.replace("name: check", "name: global-one"))
        projects = tmp_path / "projects"
        write(
            projects / "Asterim" / ".oracle" / "pipelines",
            "b.yaml",
            GOOD.replace("name: check", "name: project-one"),
        )

        found, problems = discover(config_dir=config, projects_root=projects, projects=("Asterim",))
        assert not problems
        assert set(found) == {"global-one", "project-one"}
        assert found["global-one"].source == Source.GLOBAL
        assert found["project-one"].source == Source.PROJECT

    def test_a_project_cannot_shadow_a_global_name(self, tmp_path: Path) -> None:
        """Otherwise a repository picks the name of a pipeline the owner already approves
        by reflex, and the card they have seen before is not the card they are seeing."""
        config = tmp_path / "config" / "pipelines"
        write(config, "a.yaml", GOOD)
        projects = tmp_path / "projects"
        write(projects / "Asterim" / ".oracle" / "pipelines", "b.yaml", GOOD)

        found, problems = discover(config_dir=config, projects_root=projects, projects=("Asterim",))
        assert found["check"].source == Source.GLOBAL
        assert problems and "already taken" in problems[0].message

    def test_one_broken_file_does_not_hide_the_others(self, tmp_path: Path) -> None:
        config = tmp_path / "config" / "pipelines"
        write(config, "good.yaml", GOOD.replace("name: check", "name: fine"))
        write(config, "bad.yaml", "version: 1\nname: [\n")
        found, problems = discover(config_dir=config)
        assert set(found) == {"fine"}
        assert len(problems) == 1

    def test_a_missing_directory_is_not_an_error(self, tmp_path: Path) -> None:
        found, problems = discover(config_dir=tmp_path / "nowhere")
        assert found == {} and problems == []


class TestBindParams:
    def pipeline(self) -> Pipeline:
        return Pipeline.model_validate(
            {
                "version": 1,
                "name": "check",
                "params": {
                    "skip": {"type": "bool", "default": False},
                    "depth": {"type": "int", "default": 3},
                    "mode": {"type": "enum", "default": "fast", "choices": ["fast", "slow"]},
                },
                "steps": [{"id": "a", "tool": "git.status"}],
            }
        )

    def test_defaults_mean_a_run_needs_no_arguments(self) -> None:
        assert bind_params(self.pipeline(), {}) == {"skip": False, "depth": 3, "mode": "fast"}

    def test_an_override_wins(self) -> None:
        assert bind_params(self.pipeline(), {"skip": True})["skip"] is True

    def test_an_undeclared_parameter_is_refused_not_ignored(self) -> None:
        """A caller who thinks they turned something off, and did not, is the failure
        this prevents."""
        try:
            bind_params(self.pipeline(), {"skpi": True})
        except PipelineError as exc:
            assert "skpi" in str(exc)
        else:
            raise AssertionError("an unknown parameter must be refused")

    def test_types_are_checked(self) -> None:
        for bad in ({"skip": "yes"}, {"depth": "three"}, {"mode": "medium"}, {"depth": True}):
            try:
                bind_params(self.pipeline(), bad)
            except PipelineError:
                continue
            raise AssertionError(f"{bad} should not bind")
