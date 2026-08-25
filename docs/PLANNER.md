# ORACLE — Planning: the planner tier, task specifications, roles, and agent selection

> Status: **design, Phase 8**, with the adapter + one planning round-trip spiked first (see
> [current_task.md](current_task.md)). Execution of a plan is [ORCHESTRATION.md](ORCHESTRATION.md);
> this file is how a plan comes to exist, what it contains, and how each task finds its worker.

## 1. Why planning is delegated

The local router (0.8B) is a measured, reliable **classifier** — 93.3% intent accuracy — and a
poor author of multi-step engineering plans; that is the entire premise of ADR-0001 and ADR-0004.
The original design bridged this gap by not planning at all: one turn, one tool, and a
deterministic escalation to a single delegation. That was correct for Phase 6, and it caps what
ORACLE can take on: nothing that needs decomposition, ordering, or several workers.

The planner tier removes the cap without moving the authority. **A planner is just a worker with
`role: planner`** — invoked through the same `ExternalAgentAdapter` seam, priced by the same gate,
previewed by the same egress card, returning data that is validated before anything runs.
**Claude is the default holder of the role**, measured rather than assumed. Antigravity was the
design's first choice - strong at architectural reasoning, and its ~15k-token prompt overhead
([OQ-05](OPEN_QUESTIONS.md#oq-05)) amortises over a whole graph rather than being wasted on a
small call - and the P6-T5 spike then measured it at **75% valid-on-first-attempt against a 90%
gate** ([OQ-20](OPEN_QUESTIONS.md#oq-20)), so [§6](#6-fallbacks)'s ladder promoted. Whoever holds
it, the planner is a role-holder and not a component: ORACLE never depends on one structurally
(ADR-0019), which is why replacing it cost a line of YAML and no redesign.

Think of it as: ORACLE = manager, planner = senior architect brought in per engagement, Claude =
specialist, local model = the intern with a stopwatch. The manager never lets the architect run
the deploy.

## 2. The ExecutionPlan

The planner returns a **structured plan** — a pydantic schema requested via `--json-schema`,
never a paragraph ORACLE must interpret. Rules from ADR-0017 apply: enums and required fields,
nothing that leans on `minimum`/`pattern`.

```python
class PlannedTask(BaseModel):
    id: str                                   # plan-local, e.g. "A"
    role: str                                 # MUST exist in the role registry (§4)
    objective: str                            # one goal, imperative, self-contained
    project: str | None                       # MUST resolve in the project registry, or null
    acceptance: list[str]                     # verifiable criteria — "pnpm test auth passes"
    constraints: list[str] = []               # "do not modify migrations/**"
    context_hints: list[str] = []             # what the context engine should fetch — queries,
                                              # file paths, doc names. Hints, not contents.
    agent_hint: str | None = None             # a recommendation; ORACLE decides (§5)
    depends_on: list[str] = []
    expected_outcome: Literal["diff", "report", "answer", "verdict"]

class ExecutionPlan(BaseModel):
    objective: str                            # restated user goal
    summary: str                              # one paragraph for the approval card
    tasks: list[PlannedTask]                  # 1..12
    risks: list[str] = []                     # what the planner is unsure about — rendered
                                              # on the approval card, not acted on
```

**Validation before anything else sees it** (the plan-repair pattern: one repair attempt fed the
specific errors, then the fallback ladder — never execute a partially valid plan):

0. **Non-empty.** A plan with `tasks: []` is invalid before anything else is checked. This is
   check zero because it caught a real failure on its first outing: a vendor returned
   `status: SUCCESS` with a schema-valid `structured_output` whose `tasks` array had been
   **silently emptied** by its own schema filter, while the raw response beside it held a complete
   six-task plan (OQ-20, 2026-08-24). Validate every collection a plan may return for emptiness —
   a schema-shaped answer is not a validated answer.
1. Schema-valid; task ids unique; `depends_on` ids exist; acyclic (cycle reported as a path).
2. `len(tasks) ≤ 12`. Larger means the objective should be split at the user level.
3. Every `role` is registered; every `project` resolves against the registry — a hallucinated
   project name is a validation error, never a path.
4. Every task's `acceptance` is non-empty for `expected_outcome: diff` — unverifiable coding tasks
   are not schedulable, because verification is what makes a worker's claim irrelevant.
5. `agent_hint`, if present, names a registered agent; it is *never* binding.

**The planner does not execute.** It receives a read-only context package and returns data. It
does not get the MCP tool surface in v1 — planning input is assembled by the context engine, not
explored by the planner — because a planner that browses is a planner whose egress cannot be
previewed as one packet. Revisit only if measured plan quality demands it (OQ-20 tracks this).

**Plan output is untrusted input** (ADR-0021). It arrives as `external` provenance, sets taint on
the ingesting turn, and every task the plan spawns starts tier-escalated. A plan can *suggest*
`git push`; the suggestion buys nothing but a T3 confirmation card.

## 3. TaskSpec — the specification a worker receives

The Handoff Packet ([INTEGRATIONS.md §6](INTEGRATIONS.md#6-the-handoff-packet--fallback-and-the-core-abstraction))
already is the vendor-neutral task description: goal, acceptance criteria, constraints, curated
context, prior attempts, state — rendered per adapter, previewed before egress, budget-capped at
30k tokens. The replan **generalises rather than replaces it**:

```python
class TaskSpec(BaseModel):
    objective: str
    role: str
    project: str | None
    workspace: WorkspaceSpec | None      # worktree base branch, or none for read-only roles
    acceptance: list[str]
    constraints: list[str]
    context: ContextPackage              # curated, redacted, attributed — assembled by ORACLE
    attempts: list[Attempt]              # MEMORY.md §4 — what was already tried
    expected_outcome: Literal["diff", "report", "answer", "verdict"]
    security: SpecSecurity               # allowed tools, MCP capability, egress destination
```

`TaskSpec` is the machine object; the on-disk `HandoffPacket` (TASK.md, CONTEXT.md, ATTEMPTS.md,
FILES.md, STATE.md, packet.json) is its rendered form, unchanged as the fallback and the egress
artifact. The delta from today: `role`, `acceptance` as a structured list (it is prose in TASK.md
today), and `expected_outcome`, which is what lets a `VERIFY` task know what evidence to demand.
The prompt an adapter derives from a TaskSpec is the adapter's business — Claude gets flags and
files, `agy` gets its argument order, the fallback gets Markdown on disk. Prompt-construction
discipline for small local workers (maximally explicit, exact output format, exact stop condition)
lives in the renderer for the local adapter, per the roadmap's model-stack backlog.

## 4. Roles

A **role** names a job, its output shape, and the spec template that frames it. Roles are registry
entries (data), not classes — adding one is a config change plus a renderer template, not code.

| Role | Expected outcome | Typical holder | Notes |
|---|---|---|---|
| `planner` | plan (ExecutionPlan) | claude | the only role whose output is a graph; antigravity failed the gate (OQ-20) |
| `coder` | diff | claude | worktree, write tools, verification mandatory |
| `debugger` | diff | claude | coder with failure context front-loaded |
| `tester` | diff \| verdict | claude | writes tests, or judges against acceptance |
| `reviewer` | verdict | antigravity, claude | read-only packet; no worktree |
| `researcher` | report | claude, local | read-only |
| `summarizer` | report | local | never delegated to a cloud agent — waste |
| `verifier` | verdict | **none — deterministic** | diff + tests + scope check; a task kind, not an LLM |

`verifier` appearing in the table is the point: where code can hold the role, no model does.

## 5. Agent selection

A hybrid, in this order — no LLM loop deciding which LLM to use:

```
1  policy constraints        can this agent legally take this task? (egress allowed,
                             tier reachable, role permitted for the agent)
2  deterministic rules       role → candidate set, from the capability registry
3  availability              preflight(): binary present, authenticated, not over budget
4  planner hint              breaks ties only — never overrides 1–3
5  cost order                cheapest capable candidate first (local < subscription < metered)
```

### The capability registry

`config/agents.yaml`, loaded like policy — data the model cannot modify:

```yaml
agents:
  claude:
    adapter: claude_cli
    roles: [planner, coder, debugger, tester, reviewer, researcher, documenter]
    locality: cloud
    cost: subscription          # Max plan; no per-token marginal cost, but quota exists
    structured_output: true
    workspace: worktree
    egress: api.anthropic.com
  antigravity:
    adapter: antigravity_cli
    roles: [reviewer, researcher]      # NOT planner - measured 2026-08-24, OQ-20
    locality: cloud
    cost: quota                 # 14k-token prompt overhead — never for small calls (OQ-05)
    structured_output: true     # --json-schema works; the *plans* did not (OQ-20)
    effort: low                 # high browses the filesystem and fails more (OQ-20)
    workspace: project_boundary
    egress: antigravity.google
  local:
    adapter: ollama
    roles: [summarizer, researcher]
    locality: local
    cost: free
    structured_output: true     # schema-constrained, ADR-0017 rules
    workspace: none
```

This extends the existing `AgentCaps` (`integrations/adapter.py`) rather than replacing it:
`capabilities()` reports what an adapter *can* do; the registry records what it is *allowed and
preferred* to do here. The registry is why "swap the planner" or "add an agent" is an edit to a
YAML file plus an adapter, and why the fallback ladder below is data-driven.

## 6. Fallbacks

No single vendor is load-bearing (ADR-0012 extended by ADR-0019). The ladder, walked by
`preflight()` results and validation failures:

| Planner unavailable / plan invalid twice | Worker unavailable |
|---|---|
| 1. **Claude as planner** — same ExecutionPlan schema, same validation | 1. the next capable agent in cost order |
| 2. **Deterministic template plans** — known shapes (investigate→fix→test→review; the escalation path Phase 6 built *is* one of these) filled from the intent, no model | 2. the **Handoff Packet fallback**: spec written to disk, human runs any agent, ORACLE watches for the diff — unchanged, first-class |
| 3. **Single-task plan** — today's behaviour: one delegation, or one tool. Degradation to Phase 6 is a defined state, not a crash | 3. surface with everything assembled, so the human loses nothing but automation |
| 4. **Human-provided plan** — the user writes/edits the task list in the graph approval card; validation is identical | |

The degraded modes are cheap because they are the *old* modes: ORACLE without a planner is ORACLE
as shipped on 2026-08-24, which works.

> ### The ladder has already promoted  `2026-08-24, P6-T5`
>
> **Step 1 is now the default, not the fallback.** The spike measured Antigravity at **75%
> valid-on-first-attempt against a 90% gate** ([OQ-20](OPEN_QUESTIONS.md#oq-20)), so Claude
> authors plans. Antigravity keeps `reviewer` and `researcher`.
>
> This is what the ladder was designed for, and the cost of being wrong about a vendor turned out
> to be one line of `config/agents.yaml` — as intended. Nothing above this line changed.

## 7. What the planner is never given

Stated so scope creep is a violation, not a drift:

- runtime control: no task starts, stops, or gains privilege because the planner said so;
- the tool registry as an execution surface;
- secrets, policy files, or the audit log;
- unredacted context — the packet pipeline's redaction runs before every planner egress;
- a persistent session. Each planning call is one headless run; continuity lives in ORACLE's
  context package and `--conversation` resume is an optimisation to evaluate later, not a
  dependency (the no-permanent-chat rule).

### "No tools" is a property of the sandbox, not of the request  `measured 2026-08-24`

§2 says the planner "does not get the MCP tool surface … a planner that browses is a planner whose
egress cannot be previewed as one packet". The spike found that **intent does not implement that**.
A vendor CLI ships its own tools and uses them: given an empty workspace and a planning prompt,
`agy` at `--effort high` tried to read the owner's home directory — three times out of eight, and
each attempt ended the run.

What actually held the line was the sandbox, in two layers:

1. **`--add-dir <workspace>`** — the filesystem scope the planner is given;
2. **the vendor's own permission gate**, which exists only because ORACLE refuses
   `--dangerously-skip-permissions` (INTEGRATIONS.md §5).

So the rule for any planner-capable adapter is: **assume the planner will browse, and make it
impossible rather than unrequested.** An adapter that cannot be denied tools does not get the
planner role, whatever its conformance rate. The corollary for plan *output* is in §2: validate
collections for emptiness, because a schema filter that silently drops non-conforming items
returns a conformant, useless plan.
## 8. As built  `P8-T1, 2026-08-25`

`src/oracle/orchestration/plan.py` (models, validation, compilation),
`src/oracle/orchestration/registry.py` + `config/agents.yaml` (the capability registry as
data), `src/oracle/runners/planning.py` (the egress, the repair, the graph card), and the
`graph.plan` command that ties them to the daemon. 42 tests, no vendor in any of them.

### What a plan is allowed to say

The `ExecutionPlan` models are the spike's, moved rather than rewritten — they were
measured against a real vendor before they were trusted ([OQ-20](OPEN_QUESTIONS.md#oq-20)).
Three of §2's rules became one line each:

| Rule | How |
|---|---|
| A plan may not name a tool | `extra="forbid"` on both models. A plan carrying **any** unknown field is rejected whole, because "silently trimmed" is exactly how `tool` would have arrived |
| Check 0 is non-empty | `validate()` returns early on `tasks == []` — the silently-emptied plan the spike actually received |
| Enums, not ranges | `expected_outcome` is a `Literal`; `len(tasks) ≤ 12` is checked in code (ADR-0017) |

Two more decisions the code had to make that this document did not:

* **The plan's namespace stays the plan's.** Plan-local ids (`"A"`) are remapped to
  `{root}-a` on compilation, and `depends_on` with them. Letting `"A"` reach the task
  table would make two plans' first task the same row.
* **`expected_outcome` chooses the `TaskKind`**, not the plan. A plan says what it wants
  back; the supervisor decides how that is produced. `verdict` becomes a `VERIFY` task —
  which is also why a plan asking a *worker* for the `verifier` role is rejected: no model
  holds it.

### The registry is where the measurement lives now

`config/agents.yaml` is loaded like policy — versioned, human-edited, never writable from
a tool. It is the answer to "who may hold this role", and it is where OQ-20's verdict is
recorded in a form the code reads: **antigravity does not hold `planner`**, and a test
asserts that, so restoring it means arguing with the dev log rather than editing a line.

Two behaviours worth knowing:

* **It fails closed.** An unreadable registry means no agent holds any role, so planning
  is unavailable and the ladder takes over. A registry that failed open would let a plan
  pick its own executor.
* **A stale default loses.** If `defaults:` names an agent that `roles:` no longer gives
  the role, the default is ignored — honouring it would resurrect a decision a measurement
  already overturned.

`agent_hint` breaks ties and nothing else. A hint the registry will not honour is dropped
silently *for selection* and reported loudly *in validation* if the agent does not exist
at all.

### Two questions, never one, never three

1. **The planning egress** (`ai.delegate`, T2). The preview carries the whole prompt, the
   objective, and the bound: *"up to 2 calls (one repair attempt if the plan is invalid)"*.
   Stating the bound before the click is the pipeline rule; asking again for a repair the
   person already sanctioned is how approval fatigue is manufactured. It also states
   `sends_repo_contents: false`, because that is the question a person actually has.
2. **The graph card** (`ai.graph`, T2, escalated by `external` provenance). It lists every
   task with its role, its agent, and whether it will egress. Approving it authorises the
   graph to *exist and run* — **no egress**, because each delegation's approval binds to
   rendered bytes that do not exist yet (SECURITY.md §10).

There is no third question for the repair attempt, and a refusal at either point is a full
stop rather than a fallback to doing it anyway.

### The repair, bounded

One attempt, fed the *specific* validation errors (`repair_prompt`), then the ladder. The
validator returns every problem rather than the first precisely so that one round trip can
fix a plan with three mistakes in it. A second attempt would be an agentic loop wearing a
budget's clothes.

### Replanning, as built  `P8-T2, 2026-08-25`

The planner is now called a second way: `Planner.replan(request)`, which is `plan()` with a
failure attached. Deliberately the *same method* — a second, nearly-identical planning route is
how one of them quietly stops validating. The same schema, the same validator, the same one repair
attempt, the same registry and the same project set; the only difference is what the prompt
carries and what the preview says.

What the prompt carries is ORACLE's own record: the original objective, the failed task with its
evidence, everything else that already failed under this root, and what never ran because of it —
with an instruction that the skipped work is **not** resumed and must be asked for again. The
worker's claim is not in it and cannot be: the carrier has no field for it.

The decision to call at all is not the planner's. `orchestration/replan.py` owns the budget (≤ 2
per root, one per failure, counted from the rows) and the rule that a refusal, a denial or a HALT
is a decision rather than a problem to route around. Mechanics, the exhaustion report, and every
question the design left open are in
[ORCHESTRATION.md §4](ORCHESTRATION.md#as-built--replanning--p8-t2-2026-08-25).

### Still not built

- **Context.** `context_hints` survive as text and nothing fetches them; the context
  engine is Phase 9. A plan therefore describes what it *wants* looked at and cannot
  cause a read.
- **`REPORT` runs as a delegation.** PLANNER.md §4 says a summarizer is never routed to a
  cloud agent; until the local model owns that role, the daemon maps `REPORT` to the
  delegation runner and this sentence is the admission rather than a silent shrug.
