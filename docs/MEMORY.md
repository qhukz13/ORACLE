# ORACLE — Memory

> Status: **built, P9-T1 (2026-08-25)** — `src/oracle/memory/`, migration `0003_memory.sql`,
> band 5 in context assembly, prior attempts in the Handoff Packet, and the Memory view. The
> design below stands as written; §8 records what the code had to decide that it did not.

What ORACLE remembers *about me and about its own work*. Distinct from [RAG.md](RAG.md), which is
retrieval over documents I wrote. Memory is what ORACLE learned; RAG is what it can look up.

## 1. Why memory is a separate subsystem

Without it, ORACLE re-learns the same things forever: that Asterim's tests are run with `pnpm test`
and not `npm test`, that I prefer commits in English, that this exact auth bug was investigated twice
last week and both attempts failed the same way. Every one of those costs a full turn to rediscover,
consumes a scarce context budget, and makes the agent feel stupid in a specific, grating way.

The risk in the other direction is worse: **a memory system that remembers wrong things confidently is
more harmful than no memory at all.** So every design choice below favours precision over recall, and
every remembered item is inspectable and deletable.

## 2. Four kinds of memory

| Kind | Lifetime | Written by | Example |
|---|---|---|---|
| **Working** | one turn | runtime | current plan, step results, taint flag |
| **Episodic** | forever (event log) | runtime, automatically | "at 03:41 on 21 Aug, delegated Asterim auth to Claude; tests passed" |
| **Semantic (facts)** | until invalidated | explicit write only | "Asterim uses pnpm, not npm" |
| **Preferences** | until changed | explicit write only | "commit messages in English, imperative mood" |

Working memory is just the turn object. Episodic memory is **the event log** — no separate store, and
it is already durable, ordered, and queryable. Only the last two need machinery.

## 3. Facts

```python
class Fact(BaseModel):
    id: str
    scope: Literal["global", "project", "collection"]
    scope_ref: str | None            # e.g. "Asterim"
    key: str                         # "test_command"
    value: str                       # "pnpm test"
    confidence: float                # 0..1
    source: Literal["user_stated","user_corrected","observed","inferred"]
    evidence: list[str]              # event ids / file paths that support it
    created_at: datetime
    last_confirmed_at: datetime
    hit_count: int
    superseded_by: str | None
```

**Write policy — deliberately restrictive.** A fact is written only when:

1. I state it directly ("Asterim uses pnpm"), or
2. I correct ORACLE ("no, tests are run with pnpm"), or
3. ORACLE **observed it succeed twice** (`npm test` failed, `pnpm test` worked — twice, across
   different turns), or
4. I explicitly approve a proposed fact.

Never: written mid-plan; never inferred from a single success; never inferred from a document (that's
RAG's job); never written from a tainted turn. A belief formed from a `node_modules` README is not a
fact about my project.

`source` matters at read time. `user_stated` and `user_corrected` outrank `observed`, which outranks
`inferred`. On conflict, the higher-authority and more recent fact wins and the loser is marked
`superseded_by` rather than deleted — so "why does it think that?" is always answerable.

### Decay and invalidation

Facts do not silently expire; they lose confidence and get re-checked.

- A fact unconfirmed for 90 days drops to `confidence * 0.8` and is flagged for revalidation.
- A fact whose evidence file no longer exists is flagged immediately.
- A fact contradicted by an observation is **not auto-deleted** — it is surfaced: "I have recorded
  that Asterim uses pnpm, but `pnpm test` just failed. Update?"

Auto-deletion on contradiction is tempting and wrong: a transient failure would erase a correct fact.

## 4. Prior attempts

The highest-value memory for a delegation-oriented agent, and the one most systems omit.

```python
class Attempt(BaseModel):
    id: str
    task_signature: str        # normalised goal + project, for matching
    goal: str
    project: str
    approach: str              # what was tried, one paragraph
    agent: str                 # local | claude | antigravity
    outcome: Literal["success","failure","abandoned"]
    what_failed: str | None    # the actual error / why it didn't work
    files_touched: list[str]
    at: datetime
```

When a new task matches an existing `task_signature` (embedding similarity + project match), prior
attempts enter the context at band 5 and — critically — go into the **Handoff Packet** for external
agents ([INTEGRATIONS.md](INTEGRATIONS.md)). "Claude already tried adding a null check here on the
19th and the tests still failed for reason X" is worth more than another thousand tokens of source.

## 5. Retrieval into context

Band 5 of the context budget (~700 tokens, see
[AGENT_RUNTIME.md](AGENT_RUNTIME.md#5-context-budget)), filled in priority order:

```
1  preferences relevant to the intent                    (~100 tok)
2  project facts for the resolved project                (~250 tok)
3  prior attempts matching this task signature           (~250 tok)
4  facts referenced in the last 3 turns                  (~100 tok)
```

Facts are injected as a compact block, clearly labelled as ORACLE's own recorded beliefs and marked
`system` provenance — never blended into retrieved document text, where they would be
indistinguishable from untrusted content.

## 6. Inspection and control

Non-negotiable UI surface (P5): a **Memory view** listing every fact and preference with its source,
confidence, age and hit count. Each row: edit, delete, pin, or mark "never remember this".

The user must be able to answer "why does ORACLE think that?" in one click, and "make it stop
thinking that" in two. A memory system without an undo button is a liability. Memory writes and
deletions are themselves events, so the audit trail covers them like everything else.

## 7. Explicitly not doing

- **No automatic personality/profile modelling.** Creepy, low value, hard to correct.
- **No summarising conversations into "insights about the user".** This is where these systems
  hallucinate confidently, and where the errors are hardest to notice.
- **No memory shared across projects without a scope.** A fact about Asterim is not a fact about GameRecs.
- **No embedding-based fuzzy fact recall in v1.** Exact key lookup, scoped. Fuzzy recall of facts
  produces confidently wrong answers; only `task_signature` matching for prior attempts is fuzzy, and
  that surfaces text a human reads rather than a value the agent acts on.

---

## 8. As built  `P9-T1, 2026-08-25`

`src/oracle/memory/` — `models.py` (shapes, authority, decay), `policy.py` (whether a thing may be
written at all), `store.py` (persistence, conflicts, events), `attempts.py` (signatures, matching,
recording), `bands.py` (memory into context) — plus migration `0003`, `GET /api/v1/memory`, the
`memory.remember` / `memory.forget` commands and a Memory view. 44 tests, 26 of them in the
security suite, all offline.

**The policy is a pure function and the store does not call it.** `remember()` takes a
`WriteContext` and asks; a store that also decided what was permissible would be a place where
"just this once" could be added without anybody noticing. `may_write` says no by default: every
yes is an explicit branch, so a `FactSource` added without thought is refused rather than admitted.

### What this document underspecified, now decided

| Question | As built | Why |
|---|---|---|
| Where preferences live | The same table as facts, with a `kind` column | §2 lists them as two *kinds*, not two stores. Same shape, same write policy, same conflict rule, same inspection requirement — a second table would be a second thing to keep in step |
| `user_stated` vs `user_corrected` | A correction **outranks** a statement | §3 pairs them. But a correction is literally the owner overriding something said before, and treating them as equal lets a stale statement win a tie against the correction of itself |
| How decay happens | A pure function of two timestamps, read-time | A background sweep that mutates confidence on a timer is a second writer to reason about, and there is nothing here a timer knows that a subtraction does not |
| Attempt matching | Normalised signature + Jaccard over goal words, **no embedder** | §4 says embedding similarity. An embedder on the packet-rendering path degrades to "no prior attempts" exactly when it is unavailable, which is the failure that makes a memory system feel like it has none. `match()` takes candidates and scores them, so the upgrade is one function when a measurement asks for it |
| Global *facts* in band 5 | Filled, beside preferences | §5's list names preferences and *project* facts and omits global ones, which reads as an oversight: "my main machine is Windows" is a fact, not a preference, and it applies to every turn |
| §5 item 4, "facts referenced in the last 3 turns" | **Not built** | It needs a per-turn record of which facts were *read*; `hit_count` is a running total, not a timeline. A fourth table for a ~100-token slice of one band is worth building when items 1 and 2 stop fitting. `bands.recent_facts` exists and says so, so nobody concludes it was forgotten |

### The rule that makes this safe to have

**Memory is the only place an injection survives the turn it arrived in.** Everything else is
turn-scoped: a retrieved document taints one turn, a plan authors tasks in one graph, a worker's
claim gates nothing ever. A written memory is read back into future prompts, for months, labelled
as ORACLE's own belief.

So the four blocks are enforced in one place and tested in two: **tainted** (checked first, before
the source is even read — a document must not be able to borrow the owner's authority by claiming
it), **mid-plan**, **a single observation**, and **an unapproved inference**. `plan_active` is read
from the daemon rather than from the client payload, and a security test checks that against the
source.

Two consequences worth stating plainly:

* **Nothing is deleted except by a person.** A fact that loses a conflict is marked `superseded_by`
  and stays readable; `forget()` is the only deletion in the subsystem and nothing inside ORACLE
  calls it. The Memory view renders dropped beliefs under the ones that replaced them, because a
  store that keeps the row while the UI hides it is keeping the row for nobody.
* **A lower-authority contradiction changes nothing at all.** It returns a question. Auto-deletion
  on contradiction is tempting and wrong: a transient failure would erase a correct fact.

### The friction this creates, recorded rather than excepted

"Never written mid-plan" is implemented without exception, which means **a correction the owner
types while a graph is running is refused**. That is the literal reading of §3 and it is the safe
one — a graph that could write memory could write the premise of its own next step — but it is a
real annoyance, and the moment somebody hits it, the fix is a queue that applies the write when the
graph ends, not an exception in the policy.

### Still not built

- **Confidence that means anything.** Every write lands at 1.0 and decay is the only thing that
  moves it. Until something calibrates it, `confidence` is a field with a number in it.
- **Proposed facts.** Rule 4 ("I explicitly approve a proposed fact") is enforceable — the policy
  accepts `user_approved` — but nothing proposes one yet, because nothing observes twice.
- **Band 6 on the answer path.** Retrieval is wired into the Handoff Packet, where seconds are free
  against a minutes-long delegation, and deliberately not into an interactive answer, where it
  would spend a latency budget nobody has measured for it ([OQ-15](OPEN_QUESTIONS.md#oq-15)).
