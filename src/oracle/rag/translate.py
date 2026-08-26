"""A second dense probe, in the language the corpus is written in (OQ-18, RAG.md §5).

A Russian question against an English codebase loses the lexical half outright — the
script rule in `discriminating_terms` drops its terms, so `retrieve()` takes the
dense-only path and BM25 never runs. Everything that question can reach, it reaches
through one multilingual embedding. Measured on the fixture set, that is worth 60% of
the 25 Russian cases against an 80% gate.

The lever is to ask the *same* question again in English and fuse the two dense
rankings. What that is worth was measured before any of it was written, with **human**
translations, deliberately: +12.0 points of Russian recall@5 is the ceiling of the idea,
and a ceiling that had not cleared the gate would have killed it without an
implementation to argue about.

Three things constrain what is here:

* **It is not on the interactive path.** A second probe costs one more query embedding —
  63 ms p50 / 97 ms p95 on this machine — against ~70 ms of headroom before the
  generation call even starts. It runs where seconds are free: the Handoff Packet, whose
  retrieval already precedes a delegation measured in minutes.
* **It never fails a turn.** No Ollama, no model, a refusal, a timeout, a malformed
  reply: every one of them returns `None` and retrieval thins to the native probe. The
  degraded result is today's result, which is the whole reason this can ship at all.
* **The model translates, and does not answer.** A 0.8B model handed a question will
  happily answer it. Constrained decoding plus an explicit instruction is the control,
  and `translate_to_english` returning the *question* rather than a reply is what the
  fixtures assert.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from oracle.llm.structured import StructuredOutputError, generate_structured
from oracle.llm.types import CallType, Message
from oracle.logsink import get_logger

if TYPE_CHECKING:
    from oracle.llm.provider import LLMProvider

log = get_logger(__name__)

#: A translated question longer than this is not a translation. The model that produces
#: one has started answering, and the failure mode is silent: a long English paragraph
#: about tokens retrieves the wrong thing confidently. Fixture questions run 30-90
#: characters; 400 is generous and still catches an answer.
MAX_TRANSLATION_CHARS = 400

#: How long the packet path waits. Seconds are free there, minutes are not — a delegation
#: that stalls on a translation has traded measured recall for an unmeasured outage.
#:
#: MEASURED 2026-08-26. 20 s was the first value and it was wrong, for a reason worth
#: keeping: a warm `qwen3.5:0.8b` translates a fixture question in **1.6 s p50 on an idle
#: machine**, and the same call took **19.7 s** with all 24 threads busy embedding a
#: corpus. A packet is built while other work is running — that is what a supervisor
#: *is* — so the idle number is the wrong one to size against, and a budget that fits
#: only an idle machine is a feature that switches itself off under load and says nothing.
#:
#: 45 s covers warm-but-loaded with margin and still refuses a **cold** load, which is
#: ~46-50 s on this GPU (OQ-01). That line is deliberate: the router model is resident
#: because the turn that asked for the delegation just used it, so a cold model here means
#: something else is wrong, and waiting a minute to find out is worse than a thin packet.
DEFAULT_TIMEOUT_S = 45.0

_SYSTEM = (
    "You translate a developer's question into English so it can be matched against an "
    "English codebase.\n"
    "Rules:\n"
    "- Translate the question. Do NOT answer it.\n"
    "- Keep identifiers, file names, symbols and product names exactly as written.\n"
    "- Output the question only, with no explanation and no added context.\n"
    "- If the question is already English, repeat it unchanged."
)


class TranslatedQuery(BaseModel):
    """The router model's one job here, as a schema rather than as a hope."""

    english: str = Field(description="the question, in English, and nothing else")


async def translate_to_english(
    question: str,
    provider: LLMProvider,
    *,
    # ASYNC109 wants the caller to impose the deadline with `asyncio.timeout`. The
    # caller here is a *synchronous* retrieval path reached through a worker thread, and
    # the contract of this function is that it absorbs failure rather than propagating
    # it. Owning the deadline is what makes "never raises" true; handing it back to a
    # caller that cannot express one would make it false.
    timeout: float = DEFAULT_TIMEOUT_S,  # noqa: ASYNC109
) -> str | None:
    """The question in English, or `None` — never an exception.

    `None` is not an error path that happens to be quiet; it is the contract. Every
    caller of this is a retrieval path that has a perfectly good answer without it.
    """
    try:
        result = await asyncio.wait_for(
            generate_structured(
                provider,
                [
                    Message(role="system", content=_SYSTEM),
                    Message(role="user", content=question),
                ],
                TranslatedQuery,
                temperature=0.0,
                max_tokens=256,
                call_type=CallType.ROUTE,
            ),
            timeout=timeout,
        )
    except (StructuredOutputError, TimeoutError, asyncio.CancelledError) as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        log.info("rag.translate_unavailable", reason=type(exc).__name__)
        return None
    except Exception as exc:  # provider down, transport error, anything at all
        log.info("rag.translate_unavailable", reason=str(exc)[:200])
        return None

    english = result.value.english.strip()
    if not english or len(english) > MAX_TRANSLATION_CHARS:
        log.info("rag.translate_rejected", reason="length", chars=len(english))
        return None
    # MEASURED 2026-08-26: **5 of the 25 Russian fixtures came back in Russian** — a
    # valid object, inside the length cap, and not a translation. Unguarded, that is the
    # worst shape a failure can have: the second probe embeds the same question twice,
    # RRF fuses a ranking with itself, and the log says translation succeeded.
    #
    # The rule is the mirror of `looks_translatable`, and it is a rejection rather than a
    # repair on purpose. The repair the model would need is a better model, and a second
    # attempt at 1.6 s each is latency spent to re-roll a die
    # (`logs/development/2026-08-26-oq18-translation.md`).
    if looks_translatable(english):
        log.info("rag.translate_rejected", reason="untranslated")
        return None
    return english


_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def looks_translatable(question: str) -> bool:
    """A cheap pre-check so a purely English question never pays for a model call.

    Deliberately *not* the corpus-script test that `discriminating_terms` applies. That
    one needs the index and belongs where fusion is decided; this one only has to be
    right about the case that costs money — an all-Latin question has nothing to gain
    from a second all-Latin probe.
    """
    return bool(_CYRILLIC.search(question))
