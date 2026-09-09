"""Select-as-context: documents the person chose on the knowledge map become band 6.

Band 6 (retrieval) is deliberately unfilled on the answer path, because *searching* there puts the
embedder in front of a latency budget with ~70 ms of headroom. A pin is the case that argument does
not cover: the selection was made by a human, so there is no query and nothing to embed.

What these tests hold is the part that can go wrong quietly. A pin that silently reads nothing, a
pin that keeps steering turns after the person cleared it, and — the one that matters — a pin that
launders provenance, so that choosing a document by hand strips the taint that the same document
would carry if retrieval had found it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from oracle.context.budget import Band
from oracle.core.eventlog import EventLog
from oracle.router.pipeline import PINNED_MAX, TurnPipeline


@dataclass
class _Result:
    payload: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        return self.payload


@dataclass
class _Outcome:
    ok: bool
    result: _Result | None


class _Executor:
    """Records what it was asked for, and answers with whatever the test set."""

    def __init__(self, payload: dict[str, Any] | None, ok: bool = True) -> None:
        self.payload = payload
        self.ok = ok
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool_id: str, args: dict[str, Any], **_: Any) -> _Outcome:
        self.calls.append((tool_id, args))
        if not self.ok or self.payload is None:
            return _Outcome(ok=False, result=None)
        return _Outcome(ok=True, result=_Result(self.payload))


def _pipeline(tmp_path: Any, executor: Any = None) -> TurnPipeline:
    return TurnPipeline(
        EventLog(tmp_path / "events.db"),
        None,
        None,
        executor=executor,
    )


def _payload(**over: Any) -> dict[str, Any]:
    return {
        "context": "[1] ORACLE / docs/SECURITY.md\ntaint rides on provenance",
        "citations": [{"id": "projects/ORACLE/docs/SECURITY.md", "provenance": "local_owned"}],
        "tainted": False,
        "truncated": False,
        "missing": [],
        **over,
    }


class TestPinning:
    def test_a_pin_replaces_rather_than_accumulates(self, tmp_path: Any) -> None:
        """The map's selection *is* the pin. An additive pin grows a context nobody can see the
        whole of, which is the failure band budgets exist to prevent."""
        p = _pipeline(tmp_path)
        p.pin_context("s1", ["a", "b"])
        p.pin_context("s1", ["c"])
        assert p.pinned_context("s1") == ["c"]

    def test_an_empty_pin_clears_it(self, tmp_path: Any) -> None:
        p = _pipeline(tmp_path)
        p.pin_context("s1", ["a"])
        assert p.pin_context("s1", []) == 0
        assert p.pinned_context("s1") == []

    def test_pins_are_per_session(self, tmp_path: Any) -> None:
        """A pin is a statement about one conversation, not about the machine."""
        p = _pipeline(tmp_path)
        p.pin_context("s1", ["a"])
        assert p.pinned_context("s2") == []

    def test_a_pin_is_bounded(self, tmp_path: Any) -> None:
        p = _pipeline(tmp_path)
        assert p.pin_context("s1", [f"doc{i}" for i in range(PINNED_MAX + 30)]) == PINNED_MAX


class TestBandSix:
    @pytest.mark.asyncio
    async def test_a_pin_fills_band_six_through_the_executor(self, tmp_path: Any) -> None:
        """Through the tool layer, never straight into the index: the runtime owns no second
        execution path (ROADMAP sequencing rule 2)."""
        ex = _Executor(_payload())
        p = _pipeline(tmp_path, ex)
        p.pin_context("s1", ["projects/ORACLE/docs/SECURITY.md"])

        items = await p._pinned_items("s1")
        assert [c[0] for c in ex.calls] == ["know.read_documents"]
        assert ex.calls[0][1]["documents"] == ["projects/ORACLE/docs/SECURITY.md"]
        assert len(items) == 1
        assert items[0].band is Band.RETRIEVAL
        assert "taint rides on provenance" in items[0].text

    @pytest.mark.asyncio
    async def test_no_pin_means_no_call_at_all(self, tmp_path: Any) -> None:
        ex = _Executor(_payload())
        p = _pipeline(tmp_path, ex)
        assert await p._pinned_items("s1") == []
        assert ex.calls == []

    @pytest.mark.asyncio
    async def test_choosing_a_document_by_hand_does_not_launder_it(self, tmp_path: Any) -> None:
        """The load-bearing one. A `local_foreign` document taints the turn whether retrieval
        found it or the person picked it off the map (SECURITY.md §6) — otherwise the map becomes
        a way to feed someone else's prose into a turn with the taint filed off."""
        ex = _Executor(_payload(tainted=True))
        p = _pipeline(tmp_path, ex)
        p.pin_context("s1", ["notes/someone-elses.md"])

        items = await p._pinned_items("s1")
        assert items[0].provenance == "local_foreign"

    @pytest.mark.asyncio
    async def test_a_missing_document_is_named_in_the_context(self, tmp_path: Any) -> None:
        """The person is owed the knowledge that something they chose is not in what the model
        was given. Silently dropping it makes the answer look better sourced than it is."""
        ex = _Executor(_payload(missing=["notes/deleted.md"]))
        p = _pipeline(tmp_path, ex)
        p.pin_context("s1", ["notes/deleted.md"])

        items = await p._pinned_items("s1")
        assert "no longer in the index" in items[0].text
        assert "notes/deleted.md" in items[0].text

    @pytest.mark.asyncio
    async def test_a_refused_or_failed_read_leaves_the_band_empty_rather_than_the_turn_broken(
        self, tmp_path: Any
    ) -> None:
        """A pin that cannot be read is not an answer outage — the same reasoning as the memory
        band, one subsystem over."""
        ex = _Executor(None, ok=False)
        p = _pipeline(tmp_path, ex)
        p.pin_context("s1", ["notes/a.md"])
        assert await p._pinned_items("s1") == []

    @pytest.mark.asyncio
    async def test_without_an_executor_a_pin_is_inert(self, tmp_path: Any) -> None:
        p = _pipeline(tmp_path, None)
        p.pin_context("s1", ["notes/a.md"])
        assert await p._pinned_items("s1") == []
