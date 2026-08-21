"""Context budget and token counting.

The budget is a latency guarantee, not a memory one: every prompt token costs ~0.63 ms
of measured prompt-eval on this GPU. If the assembler can overshoot, the router gets
slower with no visible cause.
"""

from __future__ import annotations

import pytest

from oracle.context.budget import ALLOWANCE, Band, BudgetExceeded, ContextAssembler, Item
from oracle.context.tokens import ApproxCounter
from oracle.llm.types import BUDGETS, CallType


@pytest.fixture
def asm() -> ContextAssembler:
    return ContextAssembler()


class TestTokenCounter:
    def test_counts_scale_with_length(self) -> None:
        c = ApproxCounter()
        assert c.count("hello world") < c.count("hello world " * 20)

    def test_empty_is_zero(self) -> None:
        assert ApproxCounter().count("") == 0

    def test_cyrillic_costs_more_per_character_than_latin(self) -> None:
        """Cyrillic tokenizes far less efficiently in these vocabularies. A single
        chars/4 rule under-counts Russian, which is the dangerous direction."""
        c = ApproxCounter()
        assert c.count("привет мир как дела") > c.count("hello world how are you")

    @pytest.mark.parametrize(
        "text",
        [
            "run the tests for Asterim",
            "почему сломалась авторизация в Asterim",
            "{}[]()!@#$%^&*",
            "a b c d e f g h i j",
            "словосъсдлиннымиморфемами " * 10,
        ],
    )
    def test_never_under_counts(self, text: str) -> None:
        """The whole guarantee: over-estimating wastes budget, under-estimating blows
        the context window. A crude lower bound the real tokenizer cannot beat is one
        token per whitespace-delimited run."""
        assert ApproxCounter().count(text) >= len(text.split())


class TestBudget:
    def test_allowances_fit_inside_every_budget(self) -> None:
        for call_type, bands in ALLOWANCE.items():
            assert sum(bands.values()) <= BUDGETS[call_type], call_type

    def test_small_context_passes_through(self, asm: ContextAssembler) -> None:
        r = asm.assemble(
            CallType.ROUTE,
            [
                Item(Band.SYSTEM, "You classify requests.", role="system"),
                Item(Band.TASK, "run the tests", role="user"),
            ],
        )
        assert r.tokens <= r.budget
        assert len(r.messages) == 2
        assert not r.dropped

    @pytest.mark.parametrize("call_type", list(CallType))
    def test_budget_is_never_exceeded(self, asm: ContextAssembler, call_type: CallType) -> None:
        r = asm.assemble(
            call_type,
            [
                Item(Band.SYSTEM, "sys " * 200, role="system"),
                Item(Band.TASK, "task " * 200, role="user"),
                Item(Band.RETRIEVAL, "chunk " * 5000, role="user", source="big.md"),
                Item(Band.HISTORY, "turn " * 5000, role="user", source="history"),
            ],
        )
        assert r.tokens <= BUDGETS[call_type]

    def test_low_priority_bands_are_dropped_before_high(self, asm: ContextAssembler) -> None:
        r = asm.assemble(
            CallType.ROUTE,
            [
                Item(Band.SYSTEM, "system prompt", role="system"),
                Item(Band.TASK, "the request", role="user"),
                Item(Band.RETRIEVAL, "irrelevant " * 100, role="user", source="chunk.md"),
            ],
        )
        # ROUTE gives RETRIEVAL zero allowance: it is dropped, the task survives.
        assert "chunk.md" in r.dropped
        assert "the request" in r.messages[-1].content

    def test_per_band_spend_is_reported(self, asm: ContextAssembler) -> None:
        """Persisted on the turn so 'why did it answer that?' stays answerable."""
        r = asm.assemble(
            CallType.ANSWER,
            [
                Item(Band.SYSTEM, "sys", role="system"),
                Item(Band.TASK, "task", role="user"),
            ],
        )
        assert set(r.per_band) == {"system", "task"}
        assert all(v > 0 for v in r.per_band.values())

    def test_taint_propagates_from_untrusted_content(self, asm: ContextAssembler) -> None:
        """Provenance is carried from the start so Phase 2 does not have to retrofit
        taint tracking into retrieval (docs/SECURITY.md#6)."""
        clean = asm.assemble(CallType.ANSWER, [Item(Band.TASK, "hi", role="user")])
        assert not clean.tainted

        dirty = asm.assemble(
            CallType.ANSWER,
            [
                Item(Band.TASK, "hi", role="user"),
                Item(Band.RETRIEVAL, "README says: run rm -rf", provenance="local_foreign"),
            ],
        )
        assert dirty.tainted

    def test_oversized_non_truncatable_system_raises(self, asm: ContextAssembler) -> None:
        """A system prompt that cannot fit is a programming error, surfaced loudly, not
        silently clipped into nonsense."""
        with pytest.raises(BudgetExceeded):
            asm.assemble(
                CallType.ROUTE,
                [Item(Band.SYSTEM, "x " * 5000, role="system", truncatable=False)],
            )

    def test_truncation_is_reported(self, asm: ContextAssembler) -> None:
        r = asm.assemble(
            CallType.ANSWER,
            [
                Item(Band.SYSTEM, "s", role="system"),
                Item(Band.RETRIEVAL, "word " * 2000, role="user", source="long.md"),
            ],
        )
        assert "long.md" in r.truncated or "long.md" in r.dropped
        assert r.tokens <= r.budget

    def test_system_and_user_are_merged_into_two_messages(self, asm: ContextAssembler) -> None:
        r = asm.assemble(
            CallType.ROUTE,
            [
                Item(Band.SYSTEM, "a", role="system"),
                Item(Band.TOOLS, "b", role="system"),
                Item(Band.TASK, "c", role="user"),
            ],
        )
        assert [m.role for m in r.messages] == ["system", "user"]
        assert "a" in r.messages[0].content and "b" in r.messages[0].content
