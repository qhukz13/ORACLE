# ORACLE — Agent Runtime

How ORACLE actually thinks. This document covers the loop, the state machine, planning, the context
budget, and cancellation. The security gate it calls into is in [SECURITY.md](SECURITY.md); the tools
it selects from are in [TOOLS.md](TOOLS.md).

## 1. The core insight

**A 2B model cannot be trusted to run a free-form agentic loop.** Everything here is designed around
that fact rather than in denial of it. Four structural mitigations:

1. **Most turns never reach the model** (§2 pre-router).
2. **The model fills in a bounded schema; it does not author control flow** (§4 planner).
3. **Every model output is schema-validated with exactly one repair attempt**, then falls back to a
   deterministic path.
4. **When the task genuinely needs strong reasoning, the correct action is to delegate**, and
   recognising that is itself a routing decision the small model *can* make reliably.

The measure of success is not "the local model solved it" — it is "the right executor solved it, and
ORACLE picked correctly and cheaply."

---

## 2. Turn pipeline

```
 message
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 0. INGRESS      session resolve · trace_id · event append    │
└──────────────────────────────────────────────────────────────┘
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. PRE-ROUTER   deterministic, no LLM, <5 ms                 │
│    /slash command  → direct dispatch                         │
│    palette action  → direct dispatch                         │
│    saved pipeline  → pipeline executor                       │
│    exact tool form → tool call (e.g. "git status Asterim")   │
│    pending question → answer slot of an existing task        │
└──────────────────────────────────────────────────────────────┘
    ▼ (only if nothing matched)
┌──────────────────────────────────────────────────────────────┐
│ 2. INTENT       router model, schema-constrained             │
│    → {intent, project?, targets[], confidence, needs_plan}   │
└──────────────────────────────────────────────────────────────┘
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. RESOLVE      entities → real objects (registry lookup)    │
│    "Asterim" → project C:/Projects/Asterim  or ASK           │
└──────────────────────────────────────────────────────────────┘
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. CONTEXT      budget · retrieve · rank · redact · render   │
└──────────────────────────────────────────────────────────────┘
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. ACT   direct answer │ single tool │ plan │ delegate       │
└──────────────────────────────────────────────────────────────┘
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 6. EXECUTE      per step: validate → POLICY → run → observe  │
└──────────────────────────────────────────────────────────────┘
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 7. CRITIC       expectation check → retry ≤2 │ replan │ stop │
└──────────────────────────────────────────────────────────────┘
    ▼
┌──────────────────────────────────────────────────────────────┐
│ 8. RESPOND      answer + citations + what it did + cost      │
└──────────────────────────────────────────────────────────────┘
```

### Step 1 in detail — the pre-router earns its keep

The pre-router is boring code and it is the highest-leverage component in the system. Every turn it
handles is a turn with zero model latency, zero hallucination risk, and zero token cost. Target:
**>50% of daily turns resolved here.** If that number is low after real use, the fix is more palette
actions and pipelines, not a bigger model.

Matching is ordered and strictly deterministic — no fuzzy matching, no embeddings. Ambiguity falls
through to the model on purpose.

### Step 2 — intent schema

```python
class Intent(BaseModel):
    intent: Literal["chat","question","investigate","modify","run","search",
                    "status","delegate","pipeline","control"]
    project: str | None = None          # must exist in the registry
    targets: list[str] = []             # files, tasks, agents — validated later
    needs_plan: bool                    # multi-step?
    confidence: float                   # 0..1
```

`confidence < 0.55` → do not guess. Ask one clarifying question with concrete options, drawn from
resolvable entities. A wrong confident action costs far more than one question. Below-threshold rates
are tracked; a persistently high rate means the schema or the model tier is wrong.

**`project` is validated against the project registry and never trusted as free text.** A
hallucinated project name resolves to nothing and triggers a clarification, rather than a path built
from an invented string.

---

## 3. State machine

The runtime's states are exactly what the UI renders in the core visualisation — one vocabulary, no
translation layer. See [UI.md](UI.md#3-the-core-orbital-view--phase-11).

```
        ┌──────────────────────── halted ◀──── HALT (from any state)
        │                            │
        ▼                            │ resume (manual only)
      idle ──▶ understanding ──▶ planning ──▶ awaiting_approval
        ▲            │               │              │  approve
        │            │               │              ▼
        │            └──▶ retrieving ┴──────▶ executing ──▶ delegating
        │                                        │              │
        │                                        ▼              │
        └──────────────── summarizing ◀──────────┴──────────────┘
                                │
                                ▼
                              error
```

Rules: transitions are events (§ event log); `halted` is reachable from every state and leaves only
by explicit human action; `error` is terminal for the turn but never for the session; every state has
a timeout that moves it to `error` rather than hanging forever.

### What `delegating` actually does today  `IMPLEMENTED 2026-08-24`

It is a **handoff**, not a wait. The state is emitted while the delegation is created; the turn then
finishes with `outcome: "delegated"` and the delegation continues under its own `task.*` stream. A
turn that stayed open for a ten-minute delegation would block the session for work the user can
already watch in the delegation panel, and the diagram's arrow back to `summarizing` describes the
*delegation's* completion rather than the turn's.

Two ways in, and neither is a second prompt (INTEGRATIONS.md §8):

- **Explicit** — "ask Claude to …", recognised by the pre-router in ~5 ms. An unnamed project is
  asked about, never guessed.
- **Escalation** — a verification tool (`dev.run_tests`, `dev.build`, `dev.lint`) reported failure
  in this turn. Deterministic: a fact about the turn's outcomes, not a second model call. What
  ORACLE already tried is carried into the packet so the delegate does not repeat it.

The egress preview is the only prompt on either path.

A third way in arrives with Phase 8: an intent whose objective needs decomposition routes to the
**supervisor**, which creates a root task and runs the planning flow instead of a turn-scoped
delegation. The turn still finishes `delegated`; the graph continues under `task.*` like a single
delegation does today ([ORCHESTRATION.md §7](ORCHESTRATION.md#7-end-to-end-example)).

### What `planning` actually does today  `IMPLEMENTED 2026-08-21`

For an actionable intent (`run`, `modify`, `investigate`, `search`, `status`), `planning` is **tool
selection**: one structured call that picks a single tool and supplies at most one string.

```
classify -> SELECT ONE TOOL -> gate -> (awaiting_approval) -> executing -> report
```

Three properties, and none of them depend on the model being good:

1. **The tool name is an enum** built from `registry.for_intent(intent)`, so an off-menu name is
   *unspellable* rather than validated after the fact ([ADR-0017](DECISIONS.md#adr-0017--constrain-what-the-decoder-can-enforce)).
2. **The model never writes a path.** It names a project; the classifier checks that name against the
   registry; the path is composed from a root the runtime owns. A hallucinated project asks for
   clarification instead of becoming a filesystem argument.
3. **Only tools whose arguments can be built honestly are offered** — 11 of 26. Everything else needs
   an argv, a command or file content, none of which can be derived from *(project, one string)*
   without inventing something.

Selection has its own `CallType.SELECT` budget rather than sharing `ROUTE`'s. They have inverted
shapes — routing is a large system prompt and a tiny tools band, selection is the reverse — and
sharing silently **truncated the tool descriptions**, which are the entire basis for the choice.

Measured: **100% on 18 cases**, p50 1157 ms
([`scripts/eval_selection.py`](../scripts/eval_selection.py), write-up in
`logs/development/2026-08-21-selection-accuracy.md`).

---

## 4. Planning

> **Superseded 2026-08-24, never implemented as written.** The in-turn `Plan`/`PlanStep` loop
> below was designed for Phase 1 and deliberately not built — the shipped `planning` state is
> single-tool selection (§3), and the audit confirmed no planner or critic exists in source.
> Multi-step work is now the **task graph** ([ORCHESTRATION.md](ORCHESTRATION.md)), authored by a
> **delegated planner** ([PLANNER.md](PLANNER.md)) rather than the local model — see
> [ADR-0019](DECISIONS.md#adr-0019--the-supervisor-completes-the-orchestrator) and
> [ADR-0020](DECISIONS.md#adr-0020--the-task-graph-is-a-durable-dag-with-append-only-replanning).
> The section is retained because its principles survive the move and bind the new design too:
> a plan is data with a schema; every element validates against a registry; never execute a
> partially valid plan; one repair attempt; bounded replanning. What changed is *who authors the
> plan* (a planner-role agent, not the router model) and *where it runs* (the durable graph, not
> inside one turn). The 8-step cap becomes the graph's 12-task cap; the critic's replan budget
> (≤ 2) becomes the root task's replan budget.

A plan is **data with a schema**, not a chain of thought. The model fills in slots; it does not
invent control flow.

```python
class PlanStep(BaseModel):
    id: str
    tool: str                       # MUST exist in the registry — validated, not trusted
    args: dict[str, Any]            # validated against that tool's JSON Schema
    expect: str | None = None       # what success looks like, for the critic
    on_failure: Literal["abort","continue","ask"] = "abort"
    depends_on: list[str] = []

class Plan(BaseModel):
    goal: str
    steps: list[PlanStep]           # max 8 in v1
    est_risk: Literal["T0","T1","T2","T3"]
```

Validation happens **before the user ever sees the plan**:

1. Every `tool` exists. An unknown tool invalidates the plan — no partial execution, no substitution.
2. Every `args` validates against that tool's schema.
3. `depends_on` forms a DAG with no cycles.
4. `len(steps) <= 8`. Longer means the task should be decomposed or delegated, and the model has
   almost certainly lost the thread.
5. Aggregate risk computed from the resolved arguments, not from tool names.

Invalid plan → one repair attempt with the specific validation errors → then degrade to a single-step
action or a clarifying question. **Never execute a partially valid plan.**

Plans are shown in the UI before execution when `est_risk >= T2`, and are **editable**: I can delete a
step, change an argument, or approve step-by-step. A plan is a proposal.

### Replanning

The critic may request a replan (max **2** per turn) when a step fails in a way that invalidates the
remaining steps. The replan prompt receives the original goal, the trace so far, and the failure —
never a blank slate. Exceeding the replan budget surfaces the failure to the user with what was tried.
Unbounded replanning is how agents burn an afternoon and a token budget achieving nothing.

---

## 5. Context budget

The scarcest resource in the system — and **not for the reason originally assumed.** The constraint is
not VRAM (the router fits at 16k with room to spare); it is **prompt-processing latency**.

`VERIFIED 2026-08-21` on this GPU: 1227 tok → 726 ms · 2427 tok → 1168 ms · 8k → ~3.7 s
([benchmark](../logs/development/2026-08-21-oq01-router-benchmark.md)). Every token in the prompt is
paid for in TTFT, **on every turn**.

So the budget is **split by call type**, not set globally. A single 8k budget would have made the
router take ~4 s to answer "run the tests".

| Call type | Budget | Target TTFT | Frequency |
|---|---|---|---|
| **`route`** — intent + tool selection | **≤ 1200 tok** | ~730 ms | every turn |
| **`answer`** — short reply with context | ≤ 2400 tok | ~1.2 s | most turns |
| **`reason`** — plan construction, packet drafting | ≤ 8000 tok | ~3.7 s | occasional |
| **`summarize`** — background, non-interactive | ≤ 16000 tok | latency irrelevant | background |

Within a call type, the Assembler fills **priority bands** in order; a band that does not fit is
truncated or dropped whole.

| Band | `route` (1200) | `answer` (2400) | `reason` (8000) | Evictable |
|---|---|---|---|---|
| 1 System | 250 | 300 | 400 | never |
| 2 Tools | **400** | 400 | 600 | never |
| 3 Task | 250 | 300 | 300 | never |
| 4 Signals | 150 | 400 | 800 | truncate |
| 5 Memory | 150 | 300 | 700 | drop oldest |
| 6 Retrieval | — | 500 | 3500 | drop lowest-ranked |
| 7 History | — | 200 | 1200 | summarise then drop |
| reserve | 200 | 300 | 500 | — |

Band 2 is the one that bites. **Sending all tool schemas every turn is the most common way to waste a
small model's context** — and it is now measurable: ~1200 tokens of schemas ≈ **730 ms of latency per
turn**. Intent-based pre-filtering to 5–8 candidate tools is therefore load-bearing, not hygiene
([TOOLS.md rule 2](TOOLS.md#rule-2--fewer-tools-than-you-think)).

Two hard rules from the benchmark:

- **`think: false` on every `route` and `answer` call.** Qwen3.5 is a thinking model; at defaults it
  spent 229 tokens reasoning about saying "hello" and returned an empty `response`. Thinking may be
  enabled for `reason` calls, where the latency is affordable.
- The budget is enforced against the **measured** TTFT curve, not a token count alone. Raising a
  band's allowance is a latency decision and must be justified against the table above.

Rules: token counting uses the model's real tokenizer (never `len(text)/4`); every retrieved chunk
carries its source so the answer can cite it; the assembled context is recorded on the turn so any
answer can be explained after the fact; **redaction runs after assembly and before rendering**, with
no path around it.

---

## 6. Memory in the loop

Detail in [MEMORY.md](MEMORY.md). The runtime's contract with memory:

- **Read** at band 5 — pinned facts for the resolved project, plus prior attempts at a similar task.
- **Write** only at explicit points: a task completes, a user correction is recorded, a durable fact
  is confirmed. Never write memory from the middle of a plan; a half-executed plan's beliefs are not
  facts.
- Memory writes are themselves events, so they are auditable and reversible.

---

## 7. Cancellation, timeouts, HALT

Three distinct mechanisms — conflating them is a common bug:

| Mechanism | Scope | Effect |
|---|---|---|
| **Cancel turn** | one turn | cancellation token trips; running step is killed; session survives |
| **Cancel task** | one long-running task | same, for background work; the task is marked `cancelled`, not `failed` |
| **HALT** | everything | all loops cancelled, all job objects terminated, policy → deny-all, manual resume required |

Every `await` inside the loop is cancellation-aware, and every step checks the token before and after
execution. A step whose side effect already happened is recorded as `completed` even if cancelled
mid-observation — **we never record a side effect as not-having-happened.** That distinction is what
keeps the audit log honest.

Timeouts are layered: per-tool (from the contract) < per-step < per-plan < per-turn. Each fires the
level below it first, so a hung `npm install` kills the process rather than the whole session.

---

## 8. Errors

Typed, not strings — the UI renders them differently and the critic reasons about them:

```python
class ToolError(BaseModel):
    kind: Literal["not_found","denied","timeout","invalid_args",
                  "execution_failed","unavailable","cancelled","tainted"]
    message: str            # human-facing, redacted
    detail: str | None      # developer-facing, logged, may be long
    retryable: bool
```

`denied` is never retried — retrying a policy denial is how an agent nags a user into approving
something. `unavailable` (Ollama down, agent CLI missing) is retried with backoff and surfaces a
degradation banner rather than a failure.

---

## 9. Observability

Every turn emits a **trace**: intent, confidence, context composition with per-band token counts, the
plan, each step's policy decision and duration, model latency (TTFT and total), and the final outcome.

Tracked from Phase 1, because these are the numbers that tell us whether the design is working:

- % turns resolved by the pre-router (target > 50%)
- intent accuracy on the fixture set; clarification rate
- structured-output failure rate (target < 2%)
- plan validity rate; replan frequency
- policy decision mix, and **approval-prompt rate per hour** — the prompt-fatigue alarm
- TTFT p50/p95; end-to-end turn latency p50/p95
- taint-escalation rate

If the approval rate exceeds roughly 5–6 per hour of active use, the tiering is wrong and needs
re-tuning toward "reversible + undo" rather than "ask". See [SECURITY.md](SECURITY.md#2-design-principles).
