"""Tool selection.

The claim under test is the one that makes a 0.8B model safe to put in this seat:
**the model picks a name from a menu and supplies at most one string; everything else
is constructed in code.**

So these tests are mostly about what selection *refuses* to do — invent a path, offer a
tool it cannot call, commit without a message, or act on a project that does not exist.
No model is involved: the schema and the argument construction are the parts that carry
the safety, and both are deterministic.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import pytest

from oracle.policy.model import Tier
from oracle.router.selection import (
    ARG_BUILDERS,
    MAX_CANDIDATES,
    NO_TOOL,
    SelectionError,
    ToolSelector,
    _plan_model,
    build_args,
    candidates_for,
)
from oracle.tools import build_registry


class TestTheMenu:
    def test_only_buildable_tools_are_offered(self) -> None:
        """A tool that is offered and then cannot be called is worse than one that was
        never offered: the model picks it, the turn fails, and nothing explains why."""
        registry = build_registry()
        for intent in ("run", "modify", "investigate", "status", "search"):
            for contract in candidates_for(registry, intent):
                assert contract.id in ARG_BUILDERS, f"{contract.id} has no argument builder"

    def test_hidden_tools_are_never_candidates(self) -> None:
        registry = build_registry()
        for intent in ("run", "modify", "investigate", "status", "search"):
            assert all(not c.hidden for c in candidates_for(registry, intent))

    def test_tools_needing_invented_arguments_are_not_selectable(self) -> None:
        """`dev.execute` needs an argv, `term.write` a command, `fs.write` file content,
        `fs.delete` a specific victim. None can be built from (project, one string)
        without inventing something, so the router cannot reach them at all."""
        for tool_id in ("dev.execute", "term.write", "fs.write", "fs.delete", "term.open"):
            assert tool_id not in ARG_BUILDERS

    def test_exactly_one_routable_tool_is_above_t1(self) -> None:
        """`git.push` is routable and T2, deliberately: without a routable tool that
        asks, the Confirmation Center could never fire from a routed turn and the most
        safety-critical surface in the product would be unreachable.

        It is buildable honestly — `origin` and the checked-out branch are what "push my
        changes" means. If a second tool ever joins it here, that is a decision to make
        on purpose, not to discover."""
        registry = build_registry()
        asking = [c.id for c in registry.all() if c.id in ARG_BUILDERS and c.risk > Tier.T1]
        assert asking == ["git.push"]

    def test_the_candidate_list_is_capped(self) -> None:
        registry = build_registry()
        for intent in ("run", "modify", "investigate", "status", "search"):
            assert len(candidates_for(registry, intent)) <= MAX_CANDIDATES

    def test_intent_filtering_actually_narrows(self) -> None:
        registry = build_registry()
        everything = {c.id for c in registry.offerable()}
        for_status = {c.id for c in candidates_for(registry, "status")}
        assert for_status < everything


class TestTheSchema:
    def test_the_tool_name_is_an_enum_of_exactly_the_candidates(self) -> None:
        """ADR-0017: constrained decoding enforces enums. Making an off-menu name
        unspellable beats validating one after the fact."""
        registry = build_registry()
        candidates = candidates_for(registry, "status")
        model = _plan_model(candidates)

        field = model.model_fields["tool"]
        enum_type = field.annotation
        assert enum_type is not None and issubclass(enum_type, Enum)
        allowed = {member.value for member in enum_type}
        assert allowed == {c.id for c in candidates} | {NO_TOOL}

    def test_refusing_is_always_expressible(self) -> None:
        """A model with no way to say "none" will pick something."""
        model = _plan_model(candidates_for(build_registry(), "run"))
        schema = model.model_json_schema()
        blob = str(schema)
        assert NO_TOOL in blob

    def test_the_schema_has_exactly_two_fields(self) -> None:
        """Every generated token costs latency on this GPU, and this call runs on every
        actionable turn."""
        model = _plan_model(candidates_for(build_registry(), "run"))
        assert set(model.model_fields) == {"tool", "text"}


class TestArgumentConstruction:
    def test_the_path_comes_from_the_registry_never_the_model(self, tmp_path: Path) -> None:
        args = build_args("git.status", "some text the model wrote", tmp_path / "Asterim")
        assert args == {"path": str(tmp_path / "Asterim")}
        # Whatever the model said is simply not used for tools that take no text.
        assert "some text" not in str(args)

    def test_a_tool_needing_a_project_refuses_without_one(self) -> None:
        with pytest.raises(SelectionError, match="which project"):
            build_args("git.status", "", None)

    def test_a_commit_without_a_message_is_refused(self, tmp_path: Path) -> None:
        """Refusing beats committing "update" over somebody's afternoon of work."""
        with pytest.raises(SelectionError, match="needs a message"):
            build_args("git.commit", "", tmp_path)
        with pytest.raises(SelectionError, match="needs a message"):
            build_args("git.commit", "ok", tmp_path)

    def test_a_commit_message_is_passed_through(self, tmp_path: Path) -> None:
        args = build_args("git.commit", "  fix the login redirect  ", tmp_path)
        assert args == {"path": str(tmp_path), "message": "fix the login redirect"}

    def test_a_test_filter_is_optional(self, tmp_path: Path) -> None:
        assert build_args("dev.run_tests", "", tmp_path) == {"path": str(tmp_path)}
        assert build_args("dev.run_tests", "test_login", tmp_path) == {
            "path": str(tmp_path),
            "filter": "test_login",
        }

    def test_tools_needing_nothing_get_nothing(self) -> None:
        assert build_args("sys.info", "anything at all", None) == {}

    def test_an_unbuildable_tool_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SelectionError, match="cannot be called"):
            build_args("dev.execute", "npm install", tmp_path)


class TestWithoutAModel:
    async def test_selection_without_a_provider_chooses_nothing(self) -> None:
        """Deterministic degradation: no model means no tool, not a guessed tool."""
        selector = ToolSelector(build_registry(), provider=None)
        selection = await selector.select("run the tests", "run", project_path=Path("C:/x"))
        assert selection.chose_nothing
        assert "offline" in selection.reason

    async def test_an_intent_no_tool_serves_chooses_nothing(self) -> None:
        selector = ToolSelector(build_registry(), provider=None)
        selection = await selector.select("hello", "chat", project_path=None)
        assert selection.chose_nothing
        assert selection.candidates == []
