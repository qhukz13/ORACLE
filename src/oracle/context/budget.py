"""Context Assembler v1 — priority bands under a hard per-call-type budget.

The budget is a latency decision, not a memory one: prompt processing dominates TTFT on
this GPU (~730 ms at 1200 tokens, ~3.7 s at 8k). See
docs/AGENT_RUNTIME.md#5-context-budget and logs/development/2026-08-21-oq01-*.

Bands are filled in priority order. A band that does not fit is truncated or dropped
whole; never-evictable bands that do not fit are a programming error, not a runtime
condition, and raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from oracle.context.tokens import MESSAGE_OVERHEAD_TOKENS, ApproxCounter, TokenCounter
from oracle.llm.types import BUDGETS, CallType, Message
from oracle.logsink import get_logger

log = get_logger(__name__)


class Band(IntEnum):
    """Lower value = filled first = harder to evict."""

    SYSTEM = 1
    TOOLS = 2
    TASK = 3
    SIGNALS = 4
    MEMORY = 5
    RETRIEVAL = 6
    HISTORY = 7


NEVER_EVICT = frozenset({Band.SYSTEM, Band.TOOLS, Band.TASK})

#: Per-call-type band allowances. Sums sit under the budget; the remainder is response
#: headroom. Numbers from docs/AGENT_RUNTIME.md#5-context-budget.
ALLOWANCE: dict[CallType, dict[Band, int]] = {
    # MEASURED (2026-08-21): SYSTEM was 250 in the original design. Few-shot examples
    # are the single biggest accuracy lever for a 0.8B classifier — 63% -> 93% on the
    # fixture set — and they live in the system prompt.
    #
    # They are NOT free, and an earlier version of this comment wrongly claimed they
    # were. Ollama reuses its prompt cache only for byte-identical requests; with a
    # different user message the whole prefix is re-evaluated (~570 ms for ~900 tokens,
    # measured). So the example block costs roughly 380 ms of latency on EVERY routed
    # turn. That is a trade worth making at +30 accuracy points, but it is a trade.
    #
    # The real mitigation is the pre-router (ADR-0011): a turn it resolves costs zero.
    CallType.ROUTE: {
        Band.SYSTEM: 1600,
        Band.TOOLS: 200,
        Band.TASK: 150,
        Band.SIGNALS: 50,
        Band.MEMORY: 0,
        Band.RETRIEVAL: 0,
        Band.HISTORY: 0,
    },
    CallType.ANSWER: {
        Band.SYSTEM: 300,
        Band.TOOLS: 400,
        Band.TASK: 300,
        Band.SIGNALS: 400,
        Band.MEMORY: 300,
        Band.RETRIEVAL: 500,
        Band.HISTORY: 200,
    },
    CallType.REASON: {
        Band.SYSTEM: 400,
        Band.TOOLS: 600,
        Band.TASK: 300,
        Band.SIGNALS: 800,
        Band.MEMORY: 700,
        Band.RETRIEVAL: 3500,
        Band.HISTORY: 1200,
    },
    CallType.SUMMARIZE: {
        Band.SYSTEM: 300,
        Band.TOOLS: 0,
        Band.TASK: 300,
        Band.SIGNALS: 400,
        Band.MEMORY: 300,
        Band.RETRIEVAL: 8000,
        Band.HISTORY: 6000,
    },
}


@dataclass
class Item:
    band: Band
    text: str
    role: str = "user"
    #: Provenance drives taint tracking in Phase 2 (docs/SECURITY.md#6). Carried from
    #: the start so retrieval never has to be retrofitted with it.
    provenance: str = "system"
    source: str | None = None
    truncatable: bool = True


@dataclass
class Assembled:
    messages: list[Message]
    tokens: int
    budget: int
    call_type: CallType
    #: Per-band spend, persisted on the turn so "why did it answer that?" stays
    #: answerable months later (docs/DATABASE.md, turns.context_json).
    per_band: dict[str, int] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    tainted: bool = False


class BudgetExceeded(Exception):
    """Non-evictable bands alone exceed the budget. A bug, not a runtime condition."""


class ContextAssembler:
    def __init__(self, counter: TokenCounter | None = None) -> None:
        self._counter = counter or ApproxCounter()

    def assemble(self, call_type: CallType, items: list[Item]) -> Assembled:
        budget = BUDGETS[call_type]
        allowance = ALLOWANCE[call_type]

        kept: list[Item] = []
        per_band: dict[str, int] = {}
        dropped: list[str] = []
        truncated: list[str] = []
        used = 0

        for band in sorted(Band):
            band_cap = allowance.get(band, 0)
            spent = 0
            for item in [i for i in items if i.band is band]:
                cost = self._cost(item.text)
                remaining_band = band_cap - spent
                remaining_total = budget - used

                if band_cap == 0:
                    dropped.append(item.source or band.name)
                    continue

                if cost <= remaining_band and cost <= remaining_total:
                    kept.append(item)
                    spent += cost
                    used += cost
                    continue

                room = min(remaining_band, remaining_total)
                if band in NEVER_EVICT:
                    if room < cost and not item.truncatable:
                        raise BudgetExceeded(
                            f"{band.name} needs {cost} tokens, only {room} available "
                            f"in a {budget}-token {call_type} budget"
                        )
                    clipped = self._truncate(item.text, room)
                    if not clipped:
                        raise BudgetExceeded(f"{band.name} cannot fit in {call_type} budget")
                    kept.append(Item(**{**item.__dict__, "text": clipped}))
                    c = self._cost(clipped)
                    spent += c
                    used += c
                    truncated.append(item.source or band.name)
                    # Silently clipping the system prompt degraded intent accuracy
                    # once already. Never let this be quiet.
                    log.warning(
                        "context.never_evict_truncated",
                        band=band.name,
                        call_type=str(call_type),
                        needed=cost,
                        available=room,
                    )
                elif item.truncatable and room > 40:
                    clipped = self._truncate(item.text, room)
                    kept.append(Item(**{**item.__dict__, "text": clipped}))
                    c = self._cost(clipped)
                    spent += c
                    used += c
                    truncated.append(item.source or band.name)
                else:
                    dropped.append(item.source or band.name)

            if spent:
                per_band[band.name.lower()] = spent

        messages = self._to_messages(kept)
        total = self._counter.count(
            "".join(m.content for m in messages)
        ) + MESSAGE_OVERHEAD_TOKENS * len(messages)

        if total > budget:  # pragma: no cover - guarded by the loop above
            raise BudgetExceeded(f"assembled {total} tokens over a {budget} budget")

        return Assembled(
            messages=messages,
            tokens=total,
            budget=budget,
            call_type=call_type,
            per_band=per_band,
            dropped=dropped,
            truncated=truncated,
            tainted=any(i.provenance in ("local_foreign", "external") for i in kept),
        )

    # ------------------------------------------------------------------ helpers

    def _cost(self, text: str) -> int:
        return self._counter.count(text) + MESSAGE_OVERHEAD_TOKENS

    def _truncate(self, text: str, max_tokens: int) -> str:
        if max_tokens <= MESSAGE_OVERHEAD_TOKENS:
            return ""
        target = max_tokens - MESSAGE_OVERHEAD_TOKENS
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._counter.count(text[:mid]) <= target:
                lo = mid
            else:
                hi = mid - 1
        clipped = text[:lo].rstrip()
        return clipped + " […]" if clipped and lo < len(text) else clipped

    def _to_messages(self, items: list[Item]) -> list[Message]:
        system = [i.text for i in items if i.role == "system"]
        user = [i.text for i in items if i.role != "system"]
        out: list[Message] = []
        if system:
            out.append(Message(role="system", content="\n\n".join(system)))
        if user:
            out.append(Message(role="user", content="\n\n".join(user)))
        return out
