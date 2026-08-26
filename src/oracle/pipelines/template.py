"""The tiny interpreter: `{{ refs }}` and `when:` conditions (PIPELINES.md §2).

> *"The evaluator is a small, auditable, non-Turing-complete interpreter — **never
> `eval`**, because a pipeline file is a place where injected content could otherwise
> become code execution."*

That sentence is the entire reason this module exists rather than being one line. A
pipeline discovered under `<project>/.oracle/pipelines/` is **repository content** — the
same trust class as a checked-in `AGENTS.md` — so its text is written by whoever wrote
the repo, and `eval` over it is remote code execution with a YAML file's manners.

`ast.literal_eval` is not the cheap way out either: it is safe about *values* and says
nothing about the expression around them, and reaching for it here is how a "safe
evaluator" acquires attribute access.

So: a whitelist tokeniser and a ~90-line recursive-descent parser over exactly

    or · and · not · ( ) · == · != · true · false · a quoted string · an integer · a ref

and nothing else. No arithmetic, no calls, no indexing, no attribute chains beyond
`params.<name>` and `project.<name>`. An expression that is not in that grammar is a
`PipelineError` naming the offending token, not a best-effort interpretation.

**Depth is capped** (`MAX_DEPTH`) because a recursive-descent parser handed
`((((((...))))))` recurses as deep as the input allows, and a `RecursionError` inside a
validator is a crash where a refusal belongs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Parenthesis nesting a human would ever write, plus room. Beyond it, the file is either
#: generated or hostile, and neither deserves a stack frame.
MAX_DEPTH = 8

#: The two namespaces an expression may read. `steps` is deliberately absent: a condition
#: over a previous step's result cannot be evaluated before the run, and the approval card
#: has to list the steps that will actually run (PIPELINES.md §3).
NAMESPACES = ("params", "project")

#: Deliberately permissive about *depth*: matching `steps.build.log_path` is what lets
#: `_lookup` refuse it by name with a reason, instead of the reference falling through
#: unmatched and being reported as "malformed" — which is true and useless.
_REF = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s*\}\}")

_TOKEN = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<eq>==)
    | (?P<ne>!=)
    | (?P<str>'[^']*'|"[^"]*")
    | (?P<int>-?\d+)
    | (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
    """,
    re.VERBOSE,
)


class PipelineError(ValueError):
    """A pipeline file said something this language does not have a meaning for."""


@dataclass(frozen=True)
class Token:
    kind: str
    text: str


def _tokenise(source: str) -> list[Token]:
    out: list[Token] = []
    pos = 0
    while pos < len(source):
        m = _TOKEN.match(source, pos)
        if m is None:
            raise PipelineError(f"cannot read {source[pos:][:16]!r} in condition {source!r}")
        pos = m.end()
        kind = m.lastgroup or ""
        if kind == "ws":
            continue
        out.append(Token(kind, m.group()))
    return out


def _lookup(ref: str, scope: dict[str, Any]) -> Any:
    """`params.x` / `project.y` → a value, or a refusal that names what was wrong.

    A bare name is refused rather than searched: `skip_frontend` and `params.skip_frontend`
    meaning the same thing is the beginning of a scope chain, and a scope chain is the
    beginning of a language.
    """
    head, _, tail = ref.partition(".")
    if head == "steps":
        raise PipelineError(
            f"{ref!r}: a pipeline cannot read a step's result. A value that only exists "
            "once the run has started cannot be shown on the approval card that authorises "
            "the run (PIPELINES.md §3). If you need it, the step that needs it should be a "
            "script run by dev.execute."
        )
    if head not in NAMESPACES:
        raise PipelineError(f"{ref!r}: unknown namespace {head!r}, expected one of {NAMESPACES}")
    if not tail:
        raise PipelineError(f"{ref!r}: {head} is a namespace, not a value — write {head}.<name>")
    if "." in tail:
        # `params.a.b` is an attribute chain, and an attribute chain is the thing this
        # module exists to not have. Two segments, always.
        raise PipelineError(f"{ref!r}: references are {head}.<name>, not nested")
    ns = scope.get(head) or {}
    if tail not in ns:
        raise PipelineError(f"{ref!r}: {head} has no {tail!r}")
    return ns[tail]


class _Parser:
    """`expr := or_expr` — the whole grammar, in the order precedence binds."""

    def __init__(self, tokens: list[Token], scope: dict[str, Any], source: str) -> None:
        self.tokens = tokens
        self.scope = scope
        self.source = source
        self.i = 0

    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> Token:
        tok = self.peek()
        if tok is None:
            raise PipelineError(f"condition {self.source!r} ends unexpectedly")
        self.i += 1
        return tok

    def parse(self) -> Any:
        value = self.or_expr(0)
        if self.peek() is not None:
            raise PipelineError(f"trailing {self.peek().text!r} in condition {self.source!r}")  # type: ignore[union-attr]
        return value

    def or_expr(self, depth: int) -> Any:
        left = self.and_expr(depth)
        while (tok := self.peek()) and tok.kind == "word" and tok.text == "or":
            self.take()
            right = self.and_expr(depth)
            left = bool(left) or bool(right)
        return left

    def and_expr(self, depth: int) -> Any:
        left = self.not_expr(depth)
        while (tok := self.peek()) and tok.kind == "word" and tok.text == "and":
            self.take()
            right = self.not_expr(depth)
            left = bool(left) and bool(right)
        return left

    def not_expr(self, depth: int) -> Any:
        tok = self.peek()
        if tok and tok.kind == "word" and tok.text == "not":
            self.take()
            return not bool(self.not_expr(depth))
        return self.compare(depth)

    def compare(self, depth: int) -> Any:
        left = self.atom(depth)
        tok = self.peek()
        if tok and tok.kind in ("eq", "ne"):
            self.take()
            right = self.atom(depth)
            return left == right if tok.kind == "eq" else left != right
        return left

    def atom(self, depth: int) -> Any:
        if depth >= MAX_DEPTH:
            raise PipelineError(f"condition {self.source!r} nests deeper than {MAX_DEPTH}")
        tok = self.take()
        if tok.kind == "lparen":
            value = self.or_expr(depth + 1)
            closing = self.take()
            if closing.kind != "rparen":
                raise PipelineError(f"expected ')' in condition {self.source!r}")
            return value
        if tok.kind == "str":
            return tok.text[1:-1]
        if tok.kind == "int":
            return int(tok.text)
        if tok.kind == "word":
            if tok.text == "true":
                return True
            if tok.text == "false":
                return False
            if tok.text in ("and", "or", "not"):
                raise PipelineError(f"{tok.text!r} needs something before it in {self.source!r}")
            return _lookup(tok.text, self.scope)
        raise PipelineError(f"unexpected {tok.text!r} in condition {self.source!r}")


def evaluate(condition: str, scope: dict[str, Any]) -> bool:
    """A `when:` condition, as a bool. Raises `PipelineError` for anything else."""
    tokens = _tokenise(condition)
    if not tokens:
        raise PipelineError("an empty condition is not a condition")
    return bool(_Parser(tokens, scope, condition).parse())


def substitute(text: str, scope: dict[str, Any]) -> str:
    """Replace every `{{ ref }}` in `text`. Refuses an unknown or unresolvable ref.

    Refusing rather than leaving the text alone is the point: a `{{ params.pth }}` typo
    that silently survives becomes a literal `{{ params.pth }}` in a filesystem argument,
    and the first anyone hears of it is a tool error with a confusing path in it.
    """

    def one(m: re.Match[str]) -> str:
        return str(_lookup(m.group(1), scope))

    out = _REF.sub(one, text)
    if "{{" in out or "}}" in out:
        raise PipelineError(f"{text!r} has a malformed reference")
    return out


def scope_for(params: dict[str, Any], project: str | None, root: str | None) -> dict[str, Any]:
    """The two namespaces, built once per run.

    `project` is split into `.name` and `.root` rather than being one value, because the
    file header names a project while every tool argument needs a path — PIPELINES.md's
    `{{ project }}` is ambiguous between them and its own example gets it wrong.
    """
    return {
        "params": dict(params),
        "project": {"name": project or "", "root": root or ""},
    }
