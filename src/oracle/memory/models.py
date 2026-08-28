"""What ORACLE remembers about me and about its own work (MEMORY.md §3, §4).

Distinct from RAG, which is retrieval over documents I wrote: **memory is what ORACLE
learned; RAG is what it can look up.** The distinction is not filing — it decides who is
allowed to write. A document can never *become* a fact, because a belief formed from a
`node_modules` README is not a belief about my project.

The design rule everything below serves, stated once: **a memory system that remembers
wrong things confidently is more harmful than no memory at all.** So every choice here
favours precision over recall, every item carries where it came from, and nothing is ever
silently deleted — a fact that loses a conflict is marked `superseded_by` and stays
readable, because "why does it think that?" has to be answerable about beliefs ORACLE no
longer holds.

Two shapes, and one deliberate absence:

* `Fact` covers facts *and* preferences. MEMORY.md §2 lists them as two kinds, not two
  stores; they have the same shape, the same write policy, the same conflict rule and the
  same inspection requirement, so `kind` is a column rather than a second table to keep
  in step.
* `Attempt` is the durable record of something that was tried. It is deliberately not
  `orchestration.replan.Attempt`, which is the *in-flight* view of a failure being handed
  to a planner: that one has no signature, no project and no lifetime, and giving it them
  would put persistence into a module whose whole claim is that it reaches nothing. Both
  are built from the same `Task` row, and `memory/attempts.py` is where that happens.
* **There is no field for a worker's claim**, here or in the table. An attempt is read
  back into a planning prompt and into a Handoff Packet, which are the two places prose
  becomes instructions; what a worker said about its own work is not evidence and does not
  travel.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from oracle.core.events import now_iso

#: A fact unconfirmed for this long loses confidence and is flagged for revalidation
#: (MEMORY.md §3). It is *not* deleted and not silently expired: a stale fact that was
#: right is more useful than a gap, as long as its staleness is visible.
STALE_AFTER_DAYS = 90
#: What an unconfirmed fact's confidence is multiplied by once it goes stale.
STALE_MULTIPLIER = 0.8


class FactKind(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"


class FactScope(StrEnum):
    GLOBAL = "global"
    PROJECT = "project"
    COLLECTION = "collection"


class FactSource(StrEnum):
    USER_STATED = "user_stated"
    USER_CORRECTED = "user_corrected"
    OBSERVED = "observed"
    INFERRED = "inferred"


#: Read-time authority (MEMORY.md §3): "user_stated and user_corrected outrank observed,
#: which outranks inferred". A correction sits above a plain statement because it is
#: literally the owner overriding something they or ORACLE said before — treating the two
#: as equal would let a stale statement win a tie against the correction of itself.
AUTHORITY: dict[FactSource, int] = {
    FactSource.USER_CORRECTED: 4,
    FactSource.USER_STATED: 3,
    FactSource.OBSERVED: 2,
    FactSource.INFERRED: 1,
}


class Fact(BaseModel):
    """One thing ORACLE believes, and everything needed to argue with it."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: FactKind = FactKind.FACT
    scope: FactScope = FactScope.GLOBAL
    #: e.g. "Asterim". `None` for global — and a fact about Asterim is not a fact about
    #: GameRecs, which is why scope is part of the key and not a label (MEMORY.md §7).
    scope_ref: str | None = None
    key: str
    value: str
    confidence: float = 1.0
    source: FactSource = FactSource.USER_STATED
    #: Event ids or file paths that support it. This is half of the answer to "why does
    #: ORACLE think that?"; `origin` is the other half.
    evidence: tuple[str, ...] = ()
    #: The turn or task that caused this write. Recorded so the answer to "why?" is a
    #: place in the timeline rather than a shrug.
    origin: str = ""
    created_at: str = Field(default_factory=now_iso)
    last_confirmed_at: str = Field(default_factory=now_iso)
    hit_count: int = 0
    #: The fact that replaced this one. Set instead of deleting, always.
    superseded_by: str | None = None

    @property
    def live(self) -> bool:
        return self.superseded_by is None

    @property
    def authority(self) -> int:
        return AUTHORITY[self.source]

    def outranks(self, other: Fact) -> bool:
        """Higher authority wins; on a tie, the more recent one does (MEMORY.md §3).

        `last_confirmed_at` rather than `created_at`, because a fact re-confirmed today
        is a live belief and one written a year ago and never mentioned since is not."""
        if self.authority != other.authority:
            return self.authority > other.authority
        return self.last_confirmed_at >= other.last_confirmed_at

    def stale_at(self, now: str) -> bool:
        """Whether this fact has gone unconfirmed long enough to need re-checking.

        Computed at read time rather than by a background job: a sweep that mutates
        confidence on a timer is a second writer to reason about, and the answer is a
        pure function of two timestamps. Comparison is on ISO strings, which sort
        correctly for the `now_iso()` format and need no clock in a test."""
        return _days_between(self.last_confirmed_at, now) >= STALE_AFTER_DAYS

    def effective_confidence(self, now: str) -> float:
        return self.confidence * STALE_MULTIPLIER if self.stale_at(now) else self.confidence

    def render(self) -> str:
        """The one-line form that goes into a context band. Labelled with its source,
        because a belief presented without provenance is indistinguishable from a fact
        about the world."""
        where = f"{self.scope_ref}: " if self.scope_ref else ""
        return f"{where}{self.key} = {self.value}  ({self.source})"


class Attempt(BaseModel):
    """What was tried before, and why it did not work (MEMORY.md §4).

    "Claude already tried adding a null check here on the 19th and the tests still failed
    for reason X" is worth more than another thousand tokens of source, and it is the
    memory most systems omit."""

    model_config = ConfigDict(frozen=True)

    id: str
    #: Normalised goal + project. Matching is signature-first with a token-overlap
    #: fallback — see `memory/attempts.py` for why v1 has no embedder in it.
    task_signature: str
    goal: str
    project: str = ""
    #: What was tried, in one paragraph. ORACLE's own account: the objective it sent and
    #: what it measured coming back, never the worker's description of its own work.
    approach: str = ""
    agent: str = ""
    outcome: Literal["success", "failure", "abandoned"] = "failure"
    #: The actual error. `None` on success.
    what_failed: str | None = None
    files_touched: tuple[str, ...] = ()
    #: The task row this was recorded from, so the two can be joined. The attempt is not
    #: derived from it on the fly: a task table that has been pruned must not take the
    #: memory with it.
    task_id: str | None = None
    at: str = Field(default_factory=now_iso)

    def render(self) -> str:
        """One line for a packet or a prompt. Short on purpose — this competes for the
        same budget as the source code the worker actually needs."""
        head = f"{self.at[:10]}, {self.agent or 'unknown agent'}: {self.outcome}"
        body = self.approach or self.goal
        tail = f" — failed: {self.what_failed}" if self.what_failed else ""
        return f"{head} — {body}{tail}"


class Contradiction(BaseModel):
    """Two facts that disagree, surfaced rather than resolved (MEMORY.md §3).

    Auto-deletion on contradiction is tempting and wrong: a transient failure would erase
    a correct fact. So a lower-authority contradiction changes nothing and produces one of
    these instead — a question for a person, carrying both sides."""

    model_config = ConfigDict(frozen=True)

    held: Fact
    proposed_value: str
    proposed_source: FactSource
    reason: str

    def question(self) -> str:
        return (
            f"I have recorded that {self.held.key} is {self.held.value!r} "
            f"({self.held.source}), but {self.proposed_source} says {self.proposed_value!r}. "
            "Update?"
        )


def _days_between(earlier: str, later: str) -> float:
    """Days between two `now_iso()` timestamps, tolerating anything unparseable by
    returning 0 — an unreadable timestamp must not make a fact look stale, because the
    consequence of that is a false 'this needs re-checking' on every read."""
    from datetime import datetime

    try:
        a = datetime.fromisoformat(earlier.replace("Z", "+00:00"))
        b = datetime.fromisoformat(later.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (b - a).total_seconds() / 86_400


def fact_row(fact: Fact) -> dict[str, Any]:
    """The wire/UI shape. Kept beside the model so a field added above cannot be silently
    missing from the Memory view, which is where "why does ORACLE think that?" is
    answered."""
    return {
        "id": fact.id,
        "kind": str(fact.kind),
        "scope": str(fact.scope),
        "scope_ref": fact.scope_ref,
        "key": fact.key,
        "value": fact.value,
        "confidence": fact.confidence,
        "source": str(fact.source),
        "evidence": list(fact.evidence),
        "origin": fact.origin,
        "created_at": fact.created_at,
        "last_confirmed_at": fact.last_confirmed_at,
        "hit_count": fact.hit_count,
        "superseded_by": fact.superseded_by,
    }
