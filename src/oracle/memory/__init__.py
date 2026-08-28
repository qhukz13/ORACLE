"""Memory: what ORACLE learned, as opposed to what it can look up (docs/MEMORY.md).

Four kinds of memory are named in §2 and only two need machinery here. Working memory is
the turn object; episodic memory is the **event log**, which is already durable, ordered
and queryable. What this package adds is semantic memory (facts and preferences) and
prior attempts.

The package is arranged so the dangerous part is the smallest and the most isolated:

    models.py    the shapes, the authority order, and the decay rule
    policy.py    whether a thing may be written at all - pure, no I/O, says no by default
    store.py     persistence, conflict handling, and the events that make it auditable
    attempts.py  signatures, matching, and turning a finished task into a record
    bands.py     memory into context, in MEMORY.md §5's priority order

`policy.py` is not called from `store.py`'s internals; the caller passes a `WriteContext`
and `remember()` asks. A store that also decided what was permissible would be a place
where "just this once" could be added without anybody noticing.
"""

from oracle.memory.attempts import (
    as_packet_attempts,
    from_task,
    match,
    normalise,
    render_block,
    signature,
    similarity,
)
from oracle.memory.bands import memory_items
from oracle.memory.models import (
    AUTHORITY,
    Attempt,
    Contradiction,
    Fact,
    FactKind,
    FactScope,
    FactSource,
)
from oracle.memory.policy import OBSERVATIONS_REQUIRED, Verdict, WriteContext, may_write
from oracle.memory.store import MemoryStore, rows_of

__all__ = [
    "AUTHORITY",
    "OBSERVATIONS_REQUIRED",
    "Attempt",
    "Contradiction",
    "Fact",
    "FactKind",
    "FactScope",
    "FactSource",
    "MemoryStore",
    "Verdict",
    "WriteContext",
    "as_packet_attempts",
    "from_task",
    "match",
    "may_write",
    "memory_items",
    "normalise",
    "render_block",
    "rows_of",
    "signature",
    "similarity",
]
