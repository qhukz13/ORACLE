"""Persistence for facts, preferences and prior attempts (MEMORY.md §3, §4).

Three properties this store has that an ordinary CRUD layer does not, each because
MEMORY.md says so and each with a test that fails if it stops being true:

* **It never deletes on conflict.** A fact that loses is marked `superseded_by` and stays
  readable. Auto-deletion on contradiction is tempting and wrong — a transient failure
  would erase a correct fact — so the loser is kept and "why did it used to think that?"
  stays answerable.
* **A lower-authority contradiction changes nothing at all.** It returns a
  `Contradiction` for a person to answer. The store does not pick.
* **Every write is an event.** `memory.written`, `memory.contradicted`, `memory.forgotten`
  ride the same log as everything else, so the audit trail covers memory the way it covers
  tool calls (MEMORY.md §6).

The write *policy* lives in `policy.py` and is not called from here. That is deliberate:
this module knows how to store a fact, and a store that also decided whether a fact was
permissible would be a place where "just this once" could be added without anybody
noticing. The caller passes a `WriteContext`, `remember()` asks the policy, and a refusal
is returned rather than raised — refusing to remember something is a normal outcome.
"""

from __future__ import annotations

import json
from typing import Any

import aiosqlite

from oracle.core.eventlog import EventLog
from oracle.core.events import Event, new_id, now_iso
from oracle.logsink import get_logger
from oracle.memory.models import (
    Attempt,
    Contradiction,
    Fact,
    FactKind,
    FactScope,
    fact_row,
)
from oracle.memory.policy import WriteContext, refuse_and_log

log = get_logger(__name__)

_FACT_COLUMNS = (
    "id",
    "kind",
    "scope",
    "scope_ref",
    "key",
    "value",
    "confidence",
    "source",
    "evidence",
    "origin",
    "created_at",
    "last_confirmed_at",
    "hit_count",
    "superseded_by",
)

_ATTEMPT_COLUMNS = (
    "id",
    "task_signature",
    "goal",
    "project",
    "approach",
    "agent",
    "outcome",
    "what_failed",
    "files_touched",
    "task_id",
    "at",
)


class Refused(Exception):
    """Never raised — declared so `remember()`'s return type reads honestly and a caller
    grepping for it finds this sentence instead of adding a raise."""


def _to_fact(row: aiosqlite.Row) -> Fact:
    return Fact.model_validate(
        {
            "id": row["id"],
            "kind": row["kind"],
            "scope": row["scope"],
            "scope_ref": row["scope_ref"],
            "key": row["key"],
            "value": row["value"],
            "confidence": row["confidence"],
            "source": row["source"],
            "evidence": tuple(json.loads(row["evidence"])),
            "origin": row["origin"],
            "created_at": row["created_at"],
            "last_confirmed_at": row["last_confirmed_at"],
            "hit_count": row["hit_count"],
            "superseded_by": row["superseded_by"],
        }
    )


def _fact_values(fact: Fact) -> tuple[Any, ...]:
    return (
        fact.id,
        str(fact.kind),
        str(fact.scope),
        fact.scope_ref,
        fact.key,
        fact.value,
        fact.confidence,
        str(fact.source),
        json.dumps(list(fact.evidence)),
        fact.origin,
        fact.created_at,
        fact.last_confirmed_at,
        fact.hit_count,
        fact.superseded_by,
    )


def _to_attempt(row: aiosqlite.Row) -> Attempt:
    return Attempt.model_validate(
        {
            "id": row["id"],
            "task_signature": row["task_signature"],
            "goal": row["goal"],
            "project": row["project"],
            "approach": row["approach"],
            "agent": row["agent"],
            "outcome": row["outcome"],
            "what_failed": row["what_failed"],
            "files_touched": tuple(json.loads(row["files_touched"])),
            "task_id": row["task_id"],
            "at": row["at"],
        }
    )


def _attempt_values(attempt: Attempt) -> tuple[Any, ...]:
    return (
        attempt.id,
        attempt.task_signature,
        attempt.goal,
        attempt.project,
        attempt.approach,
        attempt.agent,
        attempt.outcome,
        attempt.what_failed,
        json.dumps(list(attempt.files_touched)),
        attempt.task_id,
        attempt.at,
    )


class MemoryStore:
    """One per daemon. Holds no state of its own — the rows are the state."""

    def __init__(self, conn: aiosqlite.Connection, eventlog: EventLog | None = None) -> None:
        self._conn = conn
        #: Optional so the store is testable without a log; in the daemon it is always
        #: present, and a memory write nobody could find afterwards is not auditable.
        self._log = eventlog

    # -- facts ---------------------------------------------------------------

    async def remember(
        self,
        key: str,
        value: str,
        *,
        context: WriteContext,
        kind: FactKind = FactKind.FACT,
        scope: FactScope = FactScope.GLOBAL,
        scope_ref: str | None = None,
        confidence: float = 1.0,
        evidence: tuple[str, ...] = (),
        origin: str = "",
        trace_id: str = "",
    ) -> Fact | Contradiction | None:
        """Write a fact, surface a contradiction, or refuse.

        Three outcomes and they are all normal:

        * a `Fact` — it was written (possibly superseding one that lost);
        * a `Contradiction` — something incompatible is already held **by a higher
          authority**, so nothing changed and a person is being asked;
        * `None` — the policy refused, and said why on the log.
        """
        verdict = refuse_and_log(context, key)
        if not verdict:
            await self._emit(
                "memory.refused",
                trace_id,
                {"key": key, "source": str(context.source), "reason": verdict.reason},
            )
            return None

        held = await self.get(key, scope=scope, scope_ref=scope_ref)
        candidate = Fact(
            id=new_id("fct"),
            kind=kind,
            scope=scope,
            scope_ref=scope_ref,
            key=key,
            value=value,
            confidence=confidence,
            source=context.source,
            evidence=evidence,
            origin=origin,
        )

        if held is not None and held.value == value:
            # Same belief, said again. Re-confirm rather than write a duplicate: this is
            # what keeps a long-held fact from looking stale (MEMORY.md §3).
            refreshed = held.model_copy(
                update={
                    "last_confirmed_at": now_iso(),
                    "confidence": max(held.confidence, confidence),
                }
            )
            await self._save_fact(refreshed)
            await self._emit(
                "memory.written",
                trace_id,
                {"fact_id": refreshed.id, "key": key, "change": "confirmed"},
            )
            return refreshed

        if held is not None and not candidate.outranks(held):
            # The load-bearing branch. Nothing is deleted, nothing is written, and the
            # disagreement becomes a question instead of a silent overwrite.
            contradiction = Contradiction(
                held=held,
                proposed_value=value,
                proposed_source=context.source,
                reason=(
                    f"{context.source} does not outrank the recorded {held.source}; "
                    "a transient failure must not erase a correct fact"
                ),
            )
            await self._emit(
                "memory.contradicted",
                trace_id,
                {
                    "fact_id": held.id,
                    "key": key,
                    "held": held.value,
                    "proposed": value,
                    "proposed_source": str(context.source),
                    "question": contradiction.question(),
                    "resolved": False,
                },
            )
            log.info("memory.contradicted", key=key, held=held.value, proposed=value)
            return contradiction

        await self._save_fact(candidate)
        if held is not None:
            # The loser is marked, never removed. `superseded_by` is the whole of "why
            # did it used to think that?".
            await self._save_fact(held.model_copy(update={"superseded_by": candidate.id}))
            await self._emit(
                "memory.contradicted",
                trace_id,
                {
                    "fact_id": held.id,
                    "key": key,
                    "held": held.value,
                    "proposed": value,
                    "proposed_source": str(context.source),
                    "superseded_by": candidate.id,
                    "resolved": True,
                },
            )
        await self._emit(
            "memory.written",
            trace_id,
            {
                "fact_id": candidate.id,
                "key": key,
                "value": value,
                "source": str(context.source),
                "scope": str(scope),
                "scope_ref": scope_ref,
                "origin": origin,
                "change": "superseded" if held is not None else "new",
            },
        )
        return candidate

    async def get(
        self, key: str, *, scope: FactScope = FactScope.GLOBAL, scope_ref: str | None = None
    ) -> Fact | None:
        """The live fact for this key in this scope, or nothing. Exact key lookup, scoped
        — MEMORY.md §7 rules out fuzzy fact recall in v1, because a confidently wrong
        answer to "what is the test command" is worse than no answer."""
        async with self._conn.execute(
            "SELECT * FROM memory_facts WHERE key = ? AND scope = ? "
            "AND scope_ref IS ? AND superseded_by IS NULL "
            "ORDER BY last_confirmed_at DESC LIMIT 1",
            (key, str(scope), scope_ref),
        ) as cur:
            row = await cur.fetchone()
        return _to_fact(row) if row is not None else None

    async def by_id(self, fact_id: str) -> Fact | None:
        async with self._conn.execute("SELECT * FROM memory_facts WHERE id = ?", (fact_id,)) as cur:
            row = await cur.fetchone()
        return _to_fact(row) if row is not None else None

    async def live(
        self,
        *,
        scope: FactScope | None = None,
        scope_ref: str | None = None,
        kind: FactKind | None = None,
    ) -> list[Fact]:
        """Every fact currently believed, newest confirmation first."""
        where = ["superseded_by IS NULL"]
        args: list[Any] = []
        if scope is not None:
            where.append("scope = ?")
            args.append(str(scope))
            if scope is not FactScope.GLOBAL:
                where.append("scope_ref IS ?")
                args.append(scope_ref)
        if kind is not None:
            where.append("kind = ?")
            args.append(str(kind))
        async with self._conn.execute(
            # S608: `where` is built from module-local literals; every value is bound.
            f"SELECT * FROM memory_facts WHERE {' AND '.join(where)} "  # noqa: S608
            "ORDER BY last_confirmed_at DESC",
            tuple(args),
        ) as cur:
            rows = await cur.fetchall()
        return [_to_fact(r) for r in rows]

    async def all_facts(self) -> list[Fact]:
        """Everything, including superseded rows. The Memory view's query: a person
        auditing what ORACLE believes needs to see what it stopped believing too."""
        async with self._conn.execute(
            "SELECT * FROM memory_facts ORDER BY last_confirmed_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [_to_fact(r) for r in rows]

    async def history(self, key: str, *, scope_ref: str | None = None) -> list[Fact]:
        """Everything ever believed about this key, newest first — the "why does ORACLE
        think that?" chain, including the beliefs it dropped."""
        async with self._conn.execute(
            "SELECT * FROM memory_facts WHERE key = ? AND scope_ref IS ? ORDER BY created_at DESC",
            (key, scope_ref),
        ) as cur:
            rows = await cur.fetchall()
        return [_to_fact(r) for r in rows]

    async def used(self, fact: Fact) -> None:
        """Record that a fact reached a context band. `hit_count` is what tells a person
        which memories are earning their tokens and which are noise."""
        await self._conn.execute(
            "UPDATE memory_facts SET hit_count = hit_count + 1 WHERE id = ?", (fact.id,)
        )
        await self._conn.commit()

    async def forget(self, fact_id: str, *, reason: str = "", trace_id: str = "") -> bool:
        """The undo button (MEMORY.md §6): a memory system without one is a liability.

        This is the **only** deletion in the subsystem and it is always a person's
        decision — nothing in ORACLE calls it. The event outlives the row, so the audit
        trail still shows what was removed and when."""
        fact = await self.by_id(fact_id)
        if fact is None:
            return False
        await self._conn.execute("DELETE FROM memory_facts WHERE id = ?", (fact_id,))
        await self._conn.commit()
        await self._emit(
            "memory.forgotten",
            trace_id,
            {"fact_id": fact_id, "key": fact.key, "value": fact.value, "reason": reason},
        )
        log.info("memory.forgotten", fact_id=fact_id, key=fact.key)
        return True

    async def _save_fact(self, fact: Fact) -> None:
        placeholders = ", ".join("?" for _ in _FACT_COLUMNS)
        await self._conn.execute(
            # S608: interpolation is `_FACT_COLUMNS` (a module constant) and a run of `?`.
            f"INSERT OR REPLACE INTO memory_facts ({', '.join(_FACT_COLUMNS)}) "  # noqa: S608
            f"VALUES ({placeholders})",
            _fact_values(fact),
        )
        await self._conn.commit()

    # -- attempts ------------------------------------------------------------

    async def record_attempt(self, attempt: Attempt, *, trace_id: str = "") -> Attempt:
        """Attempts are written by the runtime, not by the write policy.

        The policy guards *beliefs*: things ORACLE will state as true. An attempt is a
        record of something that happened, in ORACLE's own words about its own run — it
        asserts nothing about the world, and refusing to record it would mean the replan
        loop and the Handoff Packet lose their only source of "this was already tried"."""
        placeholders = ", ".join("?" for _ in _ATTEMPT_COLUMNS)
        await self._conn.execute(
            # S608: same shape as above.
            f"INSERT OR REPLACE INTO memory_attempts ({', '.join(_ATTEMPT_COLUMNS)}) "  # noqa: S608
            f"VALUES ({placeholders})",
            _attempt_values(attempt),
        )
        await self._conn.commit()
        await self._emit(
            "memory.attempt_recorded",
            trace_id,
            {
                "attempt_id": attempt.id,
                "task_id": attempt.task_id,
                "signature": attempt.task_signature,
                "outcome": attempt.outcome,
            },
        )
        return attempt

    async def attempts_for(
        self, signature: str, *, project: str = "", limit: int = 5
    ) -> list[Attempt]:
        """Exact signature matches, newest first. The fuzzy half is in `attempts.py`,
        which calls this and then widens — kept apart so the SQL stays a lookup."""
        async with self._conn.execute(
            "SELECT * FROM memory_attempts WHERE task_signature = ? "
            "AND (? = '' OR project = ?) ORDER BY at DESC LIMIT ?",
            (signature, project, project, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_to_attempt(r) for r in rows]

    async def attempts_in(self, project: str = "", *, limit: int = 200) -> list[Attempt]:
        """The candidate pool the token-overlap fallback ranks. Bounded, because a
        fallback that scans an unbounded table is a fallback that gets disabled."""
        async with self._conn.execute(
            "SELECT * FROM memory_attempts WHERE (? = '' OR project = ?) ORDER BY at DESC LIMIT ?",
            (project, project, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_to_attempt(r) for r in rows]

    # -- bookkeeping ---------------------------------------------------------

    async def _emit(self, event_type: str, trace_id: str, payload: dict[str, Any]) -> None:
        if self._log is None:
            return
        await self._log.append(
            Event(
                type=event_type,
                trace_id=trace_id or new_id("tr"),
                actor="system",
                payload=payload,
            )
        )


def rows_of(facts: list[Fact], now: str | None = None) -> list[dict[str, Any]]:
    """The Memory view's payload: every field, plus the two things only a clock knows —
    whether a fact has gone stale and what its confidence is worth today."""
    stamp = now or now_iso()
    return [
        {
            **fact_row(fact),
            "stale": fact.stale_at(stamp),
            "effective_confidence": round(fact.effective_confidence(stamp), 3),
        }
        for fact in facts
    ]
