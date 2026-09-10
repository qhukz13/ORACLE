"""The retrieval eval's lexical gate must be the gate the product ships.

`scripts/eval_embeddings.py` carries its own BM25 and its own `answerable()`, because it runs
against a freshly-chunked corpus rather than the live index. That is fine. What is not fine is the
two drifting apart, and they did: the eval kept an `any(term is discriminating)` gate for two weeks
after production replaced it with the three rules in RAG.md §5.

The cost was not a broken eval — it was a *plausible* one. The `gated` arm scored identically to
plain `rrf` on every column of the 2026-09-10 run, which reads like "fusion gating buys nothing"
and actually meant "the gate never closed". A Russian question carrying one borrowed Latin term
(`jwt`) opened it, and unweighted RRF then admitted thirty noisy BM25 results as an equal opinion.
Measured after the port: the gate opens on 13 of 38 fixtures instead of 38 of 38, and on **0 of the
25 Russian ones**.

So this file pins the constants together. It deliberately does not re-implement the rule or assert
a recall number — it asserts that the two definitions cannot silently diverge again.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "scripts" / "eval_embeddings.py"


def _load_eval() -> ModuleType:
    """Import the eval script as a module. It is a script, not a package member."""
    spec = importlib.util.spec_from_file_location("oq18_eval", EVAL)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        pytest.skip(f"cannot load {EVAL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["oq18_eval"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def evalmod() -> ModuleType:
    if not EVAL.exists():  # pragma: no cover - the script is checked in
        pytest.skip("eval script not present")
    return _load_eval()


def test_the_gate_constants_match_production(evalmod: ModuleType) -> None:
    """One number in two files is one number that will eventually be two."""
    from oracle.rag import retrieval

    assert evalmod.MIN_DF_CEILING == retrieval.MIN_DF_CEILING
    assert evalmod.MIN_QUESTION_COVERAGE == retrieval.MIN_QUESTION_COVERAGE
    assert evalmod._CYRILLIC.pattern == retrieval._CYRILLIC.pattern


def test_a_minority_script_query_does_not_open_the_gate(evalmod: ModuleType) -> None:
    """Rule 2, and the specific failure that made the drift visible.

    "где хранится секрет jwt" carries one Latin term that *is* discriminating. Under the old
    `any(...)` gate that was enough to admit BM25's thirty results into a Russian question it
    cannot answer.
    """
    english = [
        evalmod.lex_tokens(f"service{i} handles token relay pairing logic") for i in range(80)
    ]
    russian = [evalmod.lex_tokens(f"модуль{i} обновление токена доступа секрет") for i in range(6)]
    bm25 = evalmod.BM25(english + russian)

    assert bm25.majority_is_cyrillic is False
    assert bm25.answerable("где хранится секрет jwt") is False
    assert bm25.answerable("как обновляется токен доступа") is False


def test_an_in_script_query_still_fuses(evalmod: ModuleType) -> None:
    """The gate must close on noise, not on the lexical half's whole job. A bare identifier
    lookup is 100% of its own question and has to reach BM25."""
    english = [
        evalmod.lex_tokens(f"service{i} handles token relay pairing logic") for i in range(80)
    ]
    russian = [evalmod.lex_tokens(f"модуль{i} обновление токена доступа секрет") for i in range(6)]
    bm25 = evalmod.BM25(english + russian)

    assert bm25.answerable("service7") is True


def test_the_script_test_is_minority_not_cyrillic(evalmod: ModuleType) -> None:
    """RAG.md §5 is explicit that the rule is *minority*, so a Russian-majority corpus gates out
    Latin instead. A rule that hardcoded Cyrillic would be a bug wearing a measurement."""
    english = [evalmod.lex_tokens(f"service{i} handles token relay logic") for i in range(6)]
    russian = [evalmod.lex_tokens(f"модуль{i} обновление токена доступа секрет") for i in range(80)]
    bm25 = evalmod.BM25(english + russian)

    assert bm25.majority_is_cyrillic is True
    assert bm25.answerable("service3") is False
