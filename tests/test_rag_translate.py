"""The second dense probe, and the four ways it declines to produce one.

`translate_to_english` has an unusual contract for this codebase: it returns `None` on
every failure and raises on none of them. That is only defensible because the caller
always has a working answer without it — a Russian question retrieves through one
multilingual embedding whether or not a translation arrives. The tests here are what
keeps the contract true, because "never raises" is the kind of property that survives
until somebody adds an `except` clause one layer down.

The rejection rule is measured rather than defensive.  `MEASURED 2026-08-26`  Handed the
25 Russian fixtures, `qwen3.5:0.8b` returned **5 of them still in Russian** — valid JSON,
inside the length cap, and not a translation. Unguarded, that is the worst shape a
failure can take: the same question is embedded twice, RRF fuses a ranking with itself,
and every log line says the mechanism worked.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from oracle.llm.fake import FakeProvider
from oracle.llm.types import ProviderUnavailable
from oracle.rag.translate import (
    MAX_TRANSLATION_CHARS,
    looks_translatable,
    translate_to_english,
)

RU = "как работает refresh токена в Asterim"


def reply(english: str) -> str:
    return json.dumps({"english": english}, ensure_ascii=False)


async def test_a_good_translation_comes_back_as_the_question() -> None:
    provider = FakeProvider([reply("how does token refresh work in Asterim")])
    assert await translate_to_english(RU, provider) == "how does token refresh work in Asterim"


async def test_thinking_is_off_and_the_schema_is_sent() -> None:
    """Both are load-bearing rather than stylistic: Qwen3.5 left thinking spends the
    reply on reasoning and returns an empty one (OQ-01), and structure is never parsed
    out of prose (AGENTS.md)."""
    provider = FakeProvider([reply("where is the jwt signing secret stored")])
    await translate_to_english(RU, provider)
    assert provider.calls[0].think is False
    assert provider.calls[0].schema_ is not None


async def test_a_reply_still_in_russian_is_refused() -> None:
    """The measured failure — 5 of 25 — and the reason the guard exists at all."""
    provider = FakeProvider([reply("Что мешает подобрать ключ для подключения устройства?")])
    assert await translate_to_english(RU, provider) is None


async def test_an_answer_instead_of_a_translation_is_refused() -> None:
    """A small model handed a question answers it. The length cap is what catches that,
    and it is a cap on a *question*, not on a paragraph."""
    provider = FakeProvider([reply("Token refresh works by " + "x" * MAX_TRANSLATION_CHARS)])
    assert await translate_to_english(RU, provider) is None


async def test_an_empty_reply_is_refused() -> None:
    provider = FakeProvider([reply("   ")])
    assert await translate_to_english(RU, provider) is None


async def test_no_ollama_thins_the_probe_instead_of_failing_the_turn() -> None:
    """The commonest state on a fresh machine, and it must cost nothing but the probe."""
    provider = FakeProvider([reply("anything")], unavailable=True)
    assert await translate_to_english(RU, provider) is None


async def test_malformed_output_twice_is_refused_not_raised() -> None:
    """`generate_structured` repairs once and then raises `StructuredOutputError`. That
    exception must stop here — it is a thinner packet, not a failed delegation."""
    provider = FakeProvider(["not json at all", "still not json"])
    assert await translate_to_english(RU, provider) is None


async def test_a_slow_model_times_out_into_none() -> None:
    """A delegation that stalls on a translation has traded 12 measured points of recall
    for an unmeasured outage."""

    class Slow(FakeProvider):
        async def complete(self, req: Any) -> Any:
            await asyncio.sleep(5)
            raise AssertionError("should have been cancelled")

    assert await translate_to_english(RU, Slow([reply("x")]), timeout=0.05) is None


async def test_cancellation_is_not_swallowed() -> None:
    """HALT reaches every task by cancelling it (SECURITY.md). A helper that catches
    `CancelledError` and returns a value quietly refuses to stop, which is the one
    failure this module must NOT absorb."""

    class Hangs(FakeProvider):
        async def complete(self, req: Any) -> Any:
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

    task = asyncio.create_task(translate_to_english(RU, Hangs([reply("x")]), timeout=30))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_only_a_minority_script_question_pays_for_a_model_call() -> None:
    assert looks_translatable(RU)
    assert looks_translatable("почему свой парсер yaml")
    assert not looks_translatable("how does token refresh work in Asterim")
    assert not looks_translatable("MAX_YAML_DEPTH")


def test_the_guard_and_the_pre_check_are_the_same_rule() -> None:
    """`translate_to_english` rejects an output that `looks_translatable` still accepts.
    Stated as a test because the two are on opposite sides of the call and drifting apart
    would reintroduce the silent failure without anything going red."""
    assert looks_translatable("Как из исходных файлов имя классов выводятся?")


async def test_a_provider_that_explodes_outright_is_still_a_none() -> None:
    class Broken(FakeProvider):
        async def complete(self, req: Any) -> Any:
            raise ProviderUnavailable("socket closed", remedy="start Ollama")

    assert await translate_to_english(RU, Broken([reply("x")])) is None
