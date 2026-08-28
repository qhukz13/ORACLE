"""The tiny interpreter, and the things it must refuse to be.

PIPELINES.md §2 makes one hard promise about this code — *"never `eval`, because a
pipeline file is a place where injected content could otherwise become code execution"* —
and one soft one, that the language stays small. The first is asserted structurally by
reading the module's own AST. The second is asserted by trying to grow it: every test
below that expects a `PipelineError` is a feature somebody could add, refused.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from oracle.pipelines.template import (
    MAX_DEPTH,
    PipelineError,
    evaluate,
    scope_for,
    substitute,
)

SCOPE = scope_for(
    {"skip_frontend": False, "depth": 3, "mode": "fast"}, "Asterim", "C:/Projects/Asterim"
)


class TestTheEvaluatorIsNotPython:
    def test_it_never_evals(self) -> None:
        """The promise, checked against the source rather than against intent.

        A reviewer can be persuaded that a call is safe. This cannot: there is no call to
        `eval`, `exec`, `compile` or `literal_eval` anywhere in the module, and the names
        do not appear in it at all."""
        source = Path("src/oracle/pipelines/template.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        bare = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        # Attribute calls are qualified, because `re.compile` is legitimate and
        # `ast.literal_eval` is not — an unqualified check cannot tell them apart, and a
        # test that cannot tell them apart gets relaxed the first time it fires.
        qualified = {
            f"{ast.unparse(node.func.value)}.{node.func.attr}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not bare & {"eval", "exec", "compile", "__import__"}
        assert not {q for q in qualified if q.endswith(("literal_eval", ".eval", ".exec"))}

        # And nothing that *could* execute is even imported. Checked on the AST rather
        # than on the text, because the module's own docstring explains why
        # `ast.literal_eval` is not used here and a substring search cannot tell an
        # explanation from a call.
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not imported & {"ast", "subprocess", "os", "importlib", "builtins", "operator"}

    @pytest.mark.parametrize(
        "expression",
        [
            "1 + 1",
            "len(params.mode)",
            "params.mode[0]",
            "params.__class__",
            "params.mode.upper()",
            "__import__('os')",
            "params.mode if true else false",
            "lambda: 1",
            "params.depth > 2",
        ],
    )
    def test_it_refuses_everything_that_is_not_the_grammar(self, expression: str) -> None:
        """Arithmetic, calls, indexing, attribute chains, comparisons other than == and
        !=. Each is a step toward a language, and PIPELINES.md §1's litmus says the step
        to take instead is `dev.execute` running a script."""
        with pytest.raises(PipelineError):
            evaluate(expression, SCOPE)

    def test_nesting_is_capped_rather_than_crashing(self) -> None:
        """A recursive-descent parser handed enough parentheses raises `RecursionError`,
        and a crash inside a validator is a crash where a refusal belongs."""
        with pytest.raises(PipelineError, match="nests deeper"):
            evaluate("(" * (MAX_DEPTH + 2) + "true" + ")" * (MAX_DEPTH + 2), SCOPE)

    @settings(max_examples=300, deadline=None)
    @given(st.text(max_size=40))
    def test_arbitrary_text_is_a_refusal_or_a_bool_and_never_anything_else(self, s: str) -> None:
        """The property that matters for untrusted input: whatever a repository puts in a
        `when:`, this returns a bool or raises `PipelineError`. It does not raise
        `IndexError`, it does not raise `RecursionError`, and it does not hang."""
        try:
            assert isinstance(evaluate(s, SCOPE), bool)
        except PipelineError:
            pass


class TestTheGrammarItDoesHave:
    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("true", True),
            ("false", False),
            ("not params.skip_frontend", True),
            ("params.skip_frontend or true", True),
            ("params.skip_frontend and true", False),
            ("not (params.skip_frontend or false)", True),
            ("params.mode == 'fast'", True),
            ("params.mode != 'fast'", False),
            ("params.depth == 3", True),
            ("project.name == 'Asterim'", True),
            ("params.mode == 'slow' or project.name == 'Asterim'", True),
        ],
    )
    def test_it_evaluates(self, expression: str, expected: bool) -> None:
        assert evaluate(expression, SCOPE) is expected

    def test_an_empty_condition_is_not_a_condition(self) -> None:
        with pytest.raises(PipelineError):
            evaluate("   ", SCOPE)


class TestStepsIsRefusedByName:
    """The scope cut that the whole up-front-approval property rests on.

    A `when` or an argument that reads a previous step's result cannot be evaluated before
    the run, so the card that authorises the run could not list the steps that will run —
    which is PIPELINES.md §3's one rule about approval. It is refused with a message that
    says what to do instead, because a bare "unknown namespace" would read as a bug."""

    def test_a_condition_over_a_step_result_is_refused_with_a_reason(self) -> None:
        with pytest.raises(PipelineError, match="approval card"):
            evaluate("steps.build.ok", SCOPE)

    def test_an_argument_over_a_step_result_is_refused_too(self) -> None:
        with pytest.raises(PipelineError, match="approval card"):
            substitute("{{ steps.build.log_path }}", SCOPE)


class TestSubstitution:
    def test_it_resolves_both_namespaces(self) -> None:
        assert substitute("{{ project.root }}/apps", SCOPE) == "C:/Projects/Asterim/apps"
        assert substitute("mode={{ params.mode }}", SCOPE) == "mode=fast"

    def test_a_typo_is_refused_rather_than_left_in_place(self) -> None:
        """A surviving `{{ params.pth }}` becomes a literal filesystem argument, and the
        first anyone hears of it is a tool error with a baffling path in it."""
        with pytest.raises(PipelineError, match="no 'pth'"):
            substitute("{{ params.pth }}", SCOPE)

    def test_a_bare_name_is_not_searched_for(self) -> None:
        """A scope chain is the beginning of a language."""
        with pytest.raises(PipelineError, match="unknown namespace"):
            substitute("{{ skip_frontend }}", SCOPE)

    def test_project_is_split_because_the_spec_is_ambiguous(self) -> None:
        """PIPELINES.md §2's own example writes `with: { project: "{{ project }}" }` —
        but the header's `project:` is a *name* and every tool argument needs a *path*.
        Naming them separately is what makes the example fixable."""
        assert substitute("{{ project.name }}", SCOPE) == "Asterim"
        assert substitute("{{ project.root }}", SCOPE) == "C:/Projects/Asterim"
        with pytest.raises(PipelineError, match="namespace, not a value"):
            substitute("{{ project }}", SCOPE)
