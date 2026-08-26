"""A pipeline is not a privilege escalation path (PIPELINES.md §8, SECURITY.md §10).

Phase 10 introduces the one thing the security model is most exposed to: **a card that
authorises several actions at once.** "Approve six things" and "rubber-stamp six things"
are the same gesture, and the difference has to be structural rather than a matter of how
carefully anyone reads.

There is also a genuinely new threat class. A pipeline discovered under
`<project>/.oracle/pipelines/` is **repository content** — the same trust as a checked-in
`AGENTS.md` — so cloning a repo would otherwise be enough to ship someone an unattended
workflow. It lands entirely inside the existing taint machinery, which is the point.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from oracle.pipelines.compile import render
from oracle.pipelines.loader import Loaded, Source
from oracle.pipelines.models import Pipeline
from oracle.pipelines.template import PipelineError
from oracle.policy.model import Tier
from oracle.runners.pipeline import MAX_STEP_TIER, check, grant_steps, prepare, revoke_steps

PIPELINES = Path(__file__).resolve().parents[2] / "src" / "oracle" / "pipelines"
REPO = Path(__file__).resolve().parents[2]

#: Tiers copied from `config/policy.yaml` rather than invented, so what these tests prove
#: is a property of the shipped policy and not of a policy written to make them pass. The
#: scope root is the only thing that differs, because a test cannot write to C:/Projects.
#: `test_the_shipped_policy_still_says_this` pins the copies against the real file.
POLICY = """
version: 1
default_decision: deny
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
programs:
  uv:
    subcommands:
      allow:   [run, sync, lock]
      confirm: [pip]
tools:
  fs.read:     {{ tier: T0, scopes: [projects] }}
  fs.delete:   {{ tier: T3, scopes: [projects] }}
  git.status:  {{ tier: T0, scopes: [projects] }}
  dev.build:   {{ tier: T1, scopes: [projects] }}
  dev.execute: {{ tier: T2, scopes: [projects] }}
  pipe.run:    {{ tier: T0 }}
"""


@pytest.fixture
def executor(tmp_path: Path) -> object:
    from oracle.policy.audit import AuditLog
    from oracle.policy.engine import PolicyEngine, load_policy
    from oracle.tools import ToolExecutor, build_registry

    path = tmp_path / "policy.yaml"
    path.write_text(POLICY.format(root=tmp_path.as_posix()), encoding="utf-8")
    return ToolExecutor(
        build_registry(), PolicyEngine(load_policy(path)), AuditLog(tmp_path / "a.jsonl")
    )


def test_the_shipped_policy_still_says_this() -> None:
    """The fixture above copies tiers out of `config/policy.yaml`. A copy that drifts is
    a test proving something about nothing, which is the shape of four of the five
    instrument defects this project found in its first week."""
    import yaml

    real = yaml.safe_load((REPO / "config" / "policy.yaml").read_text(encoding="utf-8"))["tools"]
    assert real["fs.delete"]["tier"] == "T3"
    assert real["dev.execute"]["tier"] == "T2"
    assert real["git.status"]["tier"] == "T0"
    assert real["pipe.run"]["tier"] == "T0", (
        "the pipeline entry is a FLOOR: `declared_tier` raises it to max(step), so a floor "
        "above T0 would make PIPELINES.md §3's tier rule unimplementable"
    )


#: Identical to `test_orchestration_boundary.FORBIDDEN_PREFIXES`, and identical on
#: purpose: the compiler sits above the privilege boundary exactly as the scheduler does.
FORBIDDEN_PREFIXES = ("oracle.tools", "oracle.toolhost", "oracle.policy", "oracle.llm")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("module", sorted(PIPELINES.glob("*.py")), ids=lambda p: p.name)
def test_the_pipeline_package_never_imports_the_execution_layers(module: Path) -> None:
    """The compiler must be structurally incapable of executing or pricing anything.

    Pricing needs the registry and the gate, and it lives in `runners/pipeline.py` for
    that reason. If this ever fails, a "small convenience import" has turned the parser
    into a second place where policy is decided."""
    offenders = sorted(
        name
        for name in _imports(module)
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_PREFIXES)
    )
    assert not offenders, f"{module.name} imports the execution layer: {', '.join(offenders)}"


def test_the_pipeline_package_spawns_nothing() -> None:
    for module in PIPELINES.glob("*.py"):
        source = module.read_text(encoding="utf-8")
        for banned in ("subprocess", "os.system", "os.popen", "shell=True"):
            assert banned not in source, f"{module.name} contains {banned!r}"


def pipeline(**overrides: object) -> Pipeline:
    return Pipeline.model_validate(
        {
            "version": 1,
            "name": "check",
            "project": "Asterim",
            "steps": [{"id": "a", "tool": "git.status", "with": {"path": "{{ project.root }}"}}],
            **overrides,
        }
    )


class TestT3IsRefusedBeforeItReachesACard:
    """SECURITY.md §5: a T3 needs the desktop and a phrase typed *for that invocation*.

    Pre-approving one from a batch card would turn `confirm_strong` into `confirm` while
    leaving the label alone — which is worse than not having the tier."""

    def test_the_ceiling_is_t2(self) -> None:
        assert MAX_STEP_TIER is Tier.T2

    def test_a_t3_step_is_a_validation_problem_naming_the_tier(
        self, executor: object, tmp_path: Path
    ) -> None:
        pl = pipeline(steps=[{"id": "wipe", "tool": "fs.delete", "with": {"path": str(tmp_path)}}])
        rendered = render(pl, {}, project_root=str(tmp_path))
        priced, problems = check(rendered, executor)  # type: ignore[arg-type]
        assert not priced
        assert problems and "T3" in problems[0]
        assert "in advance" in problems[0]


class TestAnUnpinnableProgramNeverReachesAGraph:
    def test_a_program_outside_the_allowlist_fails_validation(
        self, executor: object, tmp_path: Path
    ) -> None:
        """`executor.preview()` pins every program the step could start, so a pipeline
        naming one the allowlist does not know is refused *at validation* — with the
        allowlist named in the message, because "refused" without "by what" is useless."""
        pl = pipeline(
            steps=[
                {
                    "id": "a",
                    "tool": "dev.execute",
                    "with": {"path": str(tmp_path), "program": "curl", "args": ["run"]},
                }
            ]
        )
        rendered = render(pl, {}, project_root=str(tmp_path))
        priced, problems = check(rendered, executor)  # type: ignore[arg-type]
        assert not priced
        assert problems and "allowlist" in problems[0]


class TestTheScopeGuardNarrowsRatherThanWidens:
    def test_a_step_reaching_outside_its_project_is_refused(
        self, executor: object, tmp_path: Path
    ) -> None:
        """The `projects` scope covers every project; a pipeline belongs to one. This is
        the pipeline's own guard, and it never loosens the policy scope."""
        inside = tmp_path / "Asterim"
        outside = tmp_path / "ORACLE"
        inside.mkdir()
        outside.mkdir()
        pl = pipeline(steps=[{"id": "a", "tool": "git.status", "with": {"path": str(outside)}}])
        rendered = render(pl, {}, project_root=str(outside))
        _, problems = check(rendered, executor, project_root=inside)  # type: ignore[arg-type]
        assert problems and "may not reach past" in problems[0]

    def test_a_parameter_cannot_become_a_traversal(self, executor: object, tmp_path: Path) -> None:
        """A default is author-controlled text that lands in a path argument. It is
        canonicalised by `executor.preview()` during validation, so it fails **before the
        graph exists** rather than at the step that would have used it."""
        pl = pipeline(
            params={"where": {"type": "string", "default": "../../../../Windows/System32"}},
            steps=[
                {
                    "id": "a",
                    "tool": "fs.read",
                    "with": {"path": "{{ project.root }}/{{ params.where }}/drivers/etc/hosts"},
                }
            ],
        )
        rendered = render(pl, {"where": "../../../../Windows/System32"}, project_root=str(tmp_path))
        priced, problems = check(rendered, executor, project_root=tmp_path)  # type: ignore[arg-type]
        assert not priced, "a traversal must never be priced as runnable"
        assert problems


class TestAProjectPipelineIsUntrustedInput:
    def loaded(self, tmp_path: Path, source: str) -> Loaded:
        return Loaded(pipeline(), tmp_path / "check.yaml", source, "Asterim")

    def test_a_project_file_cannot_name_another_project(self, tmp_path: Path) -> None:
        """Asserted at the loader in `test_pipelines_loader.py`; restated here because it
        is the containment rule, not a parsing detail."""
        from oracle.pipelines.loader import load_file

        path = tmp_path / "evil.yaml"
        path.write_text(
            "version: 1\nname: evil\nproject: ORACLE\nsteps:\n  - id: a\n    tool: git.status\n",
            encoding="utf-8",
        )
        loaded, problems = load_file(path, source=Source.PROJECT, pinned_project="Asterim")
        assert loaded is None
        assert "may only act on that project" in problems[0].message

    def test_the_two_sources_are_distinguishable_at_the_card(self, tmp_path: Path) -> None:
        """The card has to say where a pipeline came from. A person approving a workflow
        that arrived with a `git clone` is making a different decision from one approving
        their own `config/pipelines/` file, and the tier alone does not tell them apart."""
        assert (
            self.loaded(tmp_path, Source.PROJECT).source
            != self.loaded(tmp_path, Source.GLOBAL).source
        )


class TestGrantsAreBoundSingleUseAndMortal:
    async def test_a_grant_does_not_authorise_a_different_call(
        self, executor: object, tmp_path: Path
    ) -> None:
        """The property the whole up-front card rests on.

        The digest is computed by `preview()` from **resolved** arguments and recomputed
        by `execute()` from the same. So a grant minted for `uv run pytest` does not
        authorise `uv run pytest --and-also-push`, even held by the caller who earned it.
        Approving a run does not approve a mutated version of it."""
        from oracle.pipelines.compile import compile_pipeline
        from oracle.runners.pipeline import PipelineRun

        pl = pipeline(
            steps=[
                {
                    "id": "tests",
                    "tool": "dev.execute",
                    "with": {"path": str(tmp_path), "program": "uv", "args": ["run", "true"]},
                }
            ]
        )
        rendered = render(pl, {}, project_root=str(tmp_path))
        priced, problems = check(rendered, executor)  # type: ignore[arg-type]
        assert not problems, problems
        assert priced[0].tier is Tier.T2
        assert priced[0].needs_approval, "a T2 step must be the kind a card asks about"

        graph = compile_pipeline(rendered, pl, root_id="tk_x")
        run = PipelineRun(
            name=pl.name,
            source=Source.GLOBAL,
            path=tmp_path / "p.yaml",
            root_id="tk_x",
            project="Asterim",
            params={},
            steps=priced,
            omitted=(),
        )
        granted = grant_steps(executor, run, graph)  # type: ignore[arg-type]
        assert granted, "an elevated step must be granted, or nothing is being tested"
        approval_id = next(iter(granted.values()))

        # The same tool, the same grant, one extra argument.
        outcome = await executor.execute(  # type: ignore[attr-defined]
            "dev.execute",
            {"path": str(tmp_path), "program": "uv", "args": ["run", "pytest", "--push"]},
            approval_id=approval_id,
        )
        assert not outcome.ok
        assert (
            "approval" in str(outcome.error.message).lower()
            or "argument" in str(outcome.error.message).lower()
        ), outcome.error

    async def test_a_grant_is_single_use(self, executor: object, tmp_path: Path) -> None:
        """Two identical steps get two grants; one grant does not run twice."""
        from oracle.pipelines.compile import compile_pipeline
        from oracle.runners.pipeline import PipelineRun

        pl = pipeline(
            steps=[
                {
                    "id": "one",
                    "tool": "dev.execute",
                    "with": {"path": str(tmp_path), "program": "uv", "args": ["run", "true"]},
                }
            ]
        )
        rendered = render(pl, {}, project_root=str(tmp_path))
        priced, _ = check(rendered, executor)  # type: ignore[arg-type]
        graph = compile_pipeline(rendered, pl, root_id="tk_y")
        run = PipelineRun(
            name=pl.name,
            source=Source.GLOBAL,
            path=tmp_path / "p.yaml",
            root_id="tk_y",
            project="Asterim",
            params={},
            steps=priced,
            omitted=(),
        )
        granted = grant_steps(executor, run, graph)  # type: ignore[arg-type]
        approval_id = next(iter(granted.values()))
        args = {"path": str(tmp_path), "program": "uv", "args": ["run", "true"]}

        first = await executor.execute("dev.execute", args, approval_id=approval_id)  # type: ignore[attr-defined]
        second = await executor.execute("dev.execute", args, approval_id=approval_id)  # type: ignore[attr-defined]
        assert not second.ok, "a grant must not authorise a second call"
        assert "used" in str(second.error.message).lower() or not first.ok

    async def test_a_grant_dies_with_the_run(self, executor: object, tmp_path: Path) -> None:
        """Revoked in a `finally`, whatever ended the run. A grant that outlives its run
        is a grant nobody is watching."""
        from oracle.pipelines.compile import compile_pipeline
        from oracle.runners.pipeline import PipelineRun

        pl = pipeline(
            steps=[
                {
                    "id": "one",
                    "tool": "dev.execute",
                    "with": {"path": str(tmp_path), "program": "uv", "args": ["run", "true"]},
                }
            ]
        )
        rendered = render(pl, {}, project_root=str(tmp_path))
        priced, _ = check(rendered, executor)  # type: ignore[arg-type]
        graph = compile_pipeline(rendered, pl, root_id="tk_z")
        run = PipelineRun(
            name=pl.name,
            source=Source.GLOBAL,
            path=tmp_path / "p.yaml",
            root_id="tk_z",
            project="Asterim",
            params={},
            steps=priced,
            omitted=(),
        )
        granted = grant_steps(executor, run, graph)  # type: ignore[arg-type]
        revoke_steps(executor, granted)  # type: ignore[arg-type]

        outcome = await executor.execute(  # type: ignore[attr-defined]
            "dev.execute",
            {"path": str(tmp_path), "program": "uv", "args": ["run", "true"]},
            approval_id=next(iter(granted.values())),
        )
        assert not outcome.ok, "a revoked grant must authorise nothing"

    def test_revoking_is_idempotent_and_leaves_nothing_behind(self, executor: object) -> None:
        """The run revokes in a `finally`, which also runs on paths where nothing was
        minted. A revoke that raised there would turn a failed run into a crashed one."""
        revoke_steps(executor, {})  # type: ignore[arg-type]
        revoke_steps(executor, {"t": "ap_nonexistent"})  # type: ignore[arg-type]


class TestNothingRunsBeforeValidationPasses:
    def test_a_typo_in_step_two_stops_step_one(self, executor: object, tmp_path: Path) -> None:
        """PIPELINES.md §3's fail-fast rule, as a return type: `prepare()` gives back
        problems **and no graph**, so there is nothing for a scheduler to start."""
        pl = pipeline(
            steps=[
                {"id": "one", "tool": "git.status", "with": {"path": str(tmp_path)}},
                {"id": "two", "tool": "dev.buld", "with": {"path": str(tmp_path)}},
            ]
        )
        loaded = Loaded(pl, tmp_path / "check.yaml", Source.GLOBAL, "Asterim")
        run, graph, problems = prepare(loaded, {}, executor, project_root=None)  # type: ignore[arg-type]
        assert run is None and graph is None
        assert any("dev.buld" in p for p in problems)

    def test_an_unresolvable_reference_is_refused_before_the_graph(
        self, executor: object, tmp_path: Path
    ) -> None:
        pl = pipeline(
            steps=[{"id": "a", "tool": "git.status", "with": {"path": "{{ params.nope }}"}}]
        )
        loaded = Loaded(pl, tmp_path / "check.yaml", Source.GLOBAL, "Asterim")
        run, graph, problems = prepare(loaded, {}, executor, project_root=None)  # type: ignore[arg-type]
        assert run is None and graph is None and problems

    def test_a_step_result_reference_never_becomes_an_argument(self) -> None:
        """The refusal that keeps "one approval up front" honest: an argument nobody can
        resolve before the run is an argument the card cannot show."""
        with pytest.raises(PipelineError, match="approval card"):
            render(
                pipeline(
                    steps=[
                        {
                            "id": "a",
                            "tool": "fs.read",
                            "with": {"path": "{{ steps.build.log_path }}"},
                        }
                    ]
                ),
                {},
                project_root="C:/Projects/Asterim",
            )
