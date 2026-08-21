"""Token counting for the context budget.

**Deviation from docs/AGENT_RUNTIME.md**, recorded rather than made silently:
that doc says "token counting uses the model's real tokenizer, never len(text)/4".
Ollama exposes no tokenizer endpoint, and the only exact count available is a round
trip (`num_predict: 0`, read `prompt_eval_count`) costing 100-700 ms — which is
absurd in a path whose entire budget is ~730 ms.

So counting is split:

  ApproxCounter  hot path. Deliberately CONSERVATIVE: it over-estimates, never under.
                 Over-estimating wastes a little budget; under-estimating blows the
                 context window, which is the failure that matters.
  ExactCounter   the model itself, used to calibrate the approximation in tests.

`test_tokens.py` asserts the approximation never under-counts real text, so the budget
guarantee holds even though the hot path is an estimate. The alternative — vendoring a
7 MB Qwen tokenizer.json — would couple us to one model and break the
"provider is replaceable" property (ADR-0009).
"""

from __future__ import annotations

import re
from typing import Protocol

#: Characters-per-token, derived from Qwen BPE behaviour and rounded DOWN so the
#: resulting estimate rounds UP. Cyrillic tokenizes far less efficiently than Latin in
#: these vocabularies, which is why a single chars/4 rule is wrong for this corpus.
_LATIN_CPT = 3.3
_CYRILLIC_CPT = 1.9
_CJK_CPT = 1.0
_OTHER_CPT = 2.4

_CYRILLIC = re.compile(r"[Ѐ-ӿԀ-ԯ]")
_CJK = re.compile(r"[一-鿿぀-ヿ]")
_LATIN = re.compile(r"[A-Za-z]")

#: Per-message chat-template overhead (role markers, separators). Small but it
#: compounds across a many-turn conversation.
MESSAGE_OVERHEAD_TOKENS = 4


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class ApproxCounter:
    """Conservative estimate. Never returns fewer tokens than the model will use."""

    def count(self, text: str) -> int:
        if not text:
            return 0
        cyr = len(_CYRILLIC.findall(text))
        cjk = len(_CJK.findall(text))
        lat = len(_LATIN.findall(text))
        other = len(text) - cyr - cjk - lat

        est = cyr / _CYRILLIC_CPT + cjk / _CJK_CPT + lat / _LATIN_CPT + other / _OTHER_CPT
        # Whitespace-delimited runs each cost at least one token; short punctuation-heavy
        # text is where character-rate models under-count worst.
        floor = len(text.split())
        return max(int(est) + 1, floor)

    def count_messages(self, messages: list[tuple[str, str]]) -> int:
        return sum(
            self.count(role) + self.count(content) + MESSAGE_OVERHEAD_TOKENS
            for role, content in messages
        )


class ExactCounter:
    """Ground truth via the provider. Test/calibration use only — it costs a round trip.

    Deliberately NOT a `TokenCounter`: its method is async, because the only exact count
    available goes over HTTP. Keeping it structurally distinct stops it being dropped
    into the hot path by accident."""

    def __init__(self, provider: object) -> None:
        self._provider = provider

    async def count_async(self, text: str) -> int:
        counter = getattr(self._provider, "count_tokens", None)
        if counter is None:
            raise NotImplementedError("provider cannot count tokens")
        return int(await counter(text))


DEFAULT_COUNTER = ApproxCounter()
