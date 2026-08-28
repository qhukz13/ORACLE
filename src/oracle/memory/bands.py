"""Band 5: memory into context, in MEMORY.md §5's priority order.

    1  preferences relevant to the intent          (~100 tok)
    2  project facts for the resolved project      (~250 tok)
    3  prior attempts matching this task signature (~250 tok)
    4  facts referenced in the last 3 turns        (~100 tok)

The ordering is the design: a preference is cheap and almost always applicable, a project
fact is what stops the same rediscovery every turn, and a prior attempt is the expensive
one that occasionally saves an entire delegation. The assembler truncates from the bottom,
so the order here is what survives a tight budget.

**Everything produced here is `provenance="system"`.** Facts are ORACLE's own recorded
beliefs and are labelled as such — never blended into retrieved document text, where they
would be indistinguishable from untrusted content (MEMORY.md §5). That label is also what
keeps a memory from *taining* a turn: `Assembled.tainted` is computed from provenance, and
a fact that arrived from a tainted turn was never written in the first place
(`memory/policy.py`), so the two halves of that rule meet here.

Item 4 of the list is deliberately absent, and its absence is not an oversight — see
`recent_facts` at the bottom.
"""

from __future__ import annotations

from oracle.context.budget import Band, Item
from oracle.logsink import get_logger
from oracle.memory.attempts import DEFAULT_LIMIT, match, render_block, signature
from oracle.memory.models import Fact, FactKind, FactScope
from oracle.memory.store import MemoryStore

log = get_logger(__name__)

#: How many of each kind reach the band before the assembler even sees them. A band
#: producer that hands over fifty items and lets truncation sort it out has moved the
#: decision to the least informed place in the stack.
MAX_PREFERENCES = 5
MAX_PROJECT_FACTS = 8


def render_facts(facts: list[Fact], heading: str) -> str:
    if not facts:
        return ""
    return heading + "\n" + "\n".join(f"- {f.render()}" for f in facts)


async def memory_items(
    store: MemoryStore,
    *,
    goal: str,
    project: str | None = None,
    mark_used: bool = True,
) -> list[Item]:
    """Band-5 items for one call, in priority order.

    `mark_used` increments `hit_count`, which is what tells a person which memories are
    earning their tokens. Off in tests that assert on the store rather than on the turn."""
    items: list[Item] = []

    preferences = (await store.live(kind=FactKind.PREFERENCE))[:MAX_PREFERENCES]
    if preferences:
        items.append(
            Item(
                Band.MEMORY,
                render_facts(preferences, "Your stated preferences:"),
                provenance="system",
                source="memory.preferences",
            )
        )

    #: MEMORY.md §5's list names preferences and *project* facts and omits global ones,
    #: which reads as an oversight rather than a decision: "my main machine is Windows" is
    #: a fact, not a preference, and it applies to every turn. Filled here, beside
    #: preferences, because both are unscoped and both are cheap — the doc's own instinct
    #: is unscoped before scoped.
    globals_ = (await store.live(scope=FactScope.GLOBAL, kind=FactKind.FACT))[:MAX_PROJECT_FACTS]
    if globals_:
        items.append(
            Item(
                Band.MEMORY,
                render_facts(globals_, "What ORACLE has recorded:"),
                provenance="system",
                source="memory.global",
            )
        )

    facts: list[Fact] = []
    if project:
        facts = (await store.live(scope=FactScope.PROJECT, scope_ref=project, kind=FactKind.FACT))[
            :MAX_PROJECT_FACTS
        ]
        if facts:
            items.append(
                Item(
                    Band.MEMORY,
                    render_facts(facts, f"What ORACLE has recorded about {project}:"),
                    provenance="system",
                    source="memory.facts",
                )
            )

    if goal:
        exact = await store.attempts_for(signature(goal, project), project=project or "")
        found = exact or match(goal, await store.attempts_in(project or ""), limit=DEFAULT_LIMIT)
        block = render_block(found[:DEFAULT_LIMIT])
        if block:
            items.append(
                Item(
                    Band.MEMORY,
                    block,
                    provenance="system",
                    source="memory.attempts",
                )
            )

    if mark_used:
        for fact in [*preferences, *globals_, *facts]:
            await store.used(fact)

    log.debug(
        "memory.band",
        preferences=len(preferences),
        globals=len(globals_),
        facts=len(facts),
        items=len(items),
        project=project,
    )
    return items


async def recent_facts(store: MemoryStore) -> list[Fact]:  # pragma: no cover - see below
    """MEMORY.md §5 item 4: "facts referenced in the last 3 turns".

    **Not built, on purpose.** It needs a per-turn record of which facts were *read*, and
    `hit_count` is a running total rather than a timeline — adding the timeline means a
    fourth table whose only consumer is a ~100-token slice of one band. It is worth
    building when there are enough facts for items 1 and 2 to stop fitting, and this
    function exists so that the day somebody looks for it, they find the reasoning instead
    of concluding it was forgotten."""
    return []
