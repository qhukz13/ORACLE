# ORACLE — Memory

> Status: **design — not yet implemented** (`src/oracle/memory/` does not exist; audited
> 2026-08-24). Scheduled as **Phase 9**, with one early slice: the `Attempt` record (§4) starts
> being *written* in Phase 7, when task completion has somewhere durable to record it — because
> the Handoff Packet's `ATTEMPTS.md` and the replanning loop
> ([ORCHESTRATION.md §4](ORCHESTRATION.md#4-failure-and-replanning)) are its consumers and now
> load-bearing. The design below stands as written.

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
