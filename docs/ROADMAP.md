# ORACLE — Roadmap

> **Rewritten 2026-08-24** for the supervisor architecture
> ([ADR-0019](DECISIONS.md#adr-0019--the-supervisor-completes-the-orchestrator)–[0022](DECISIONS.md#adr-0022--external-agent-frameworks-evaluated-not-adopted),
> analysis in [`logs/development/2026-08-24-supervisor-replan.md`](../logs/development/2026-08-24-supervisor-replan.md)).
> The previous roadmap's Phases 0–6 are **built** and are recorded below as the foundation, not
> re-scheduled as future work. Sequencing keeps the original discipline: every phase ends with
> something usable, and nothing executes a side effect before the machinery that governs it exists.

## Where the project actually is

Verified against source 2026-08-24, not against docs:

| Subsystem | Status | Evidence |
|---|---|---|
| Event-sourced runtime, sessions, WS resume | **implemented** | `src/oracle/core/`, `api/app.py` |
| Pre-router, intent, single-tool selection | **implemented, measured** (93.3% / 100%) | `src/oracle/router/` |
| Tool system: 33 contracts, registry, undo | **implemented** | `src/oracle/tools/` |
| Policy gate: tiers, scopes, taint, approvals, HALT, audit chain | **implemented** (265 security tests) | `src/oracle/policy/` |
| Toolhost: separate process, Job Objects | **implemented, measured** | `src/oracle/toolhost/` |
| RAG: hybrid retrieval, bge-m3, watcher, cache | **implemented**; recall gate unmet ([OQ-18](OPEN_QUESTIONS.md#oq-18)) | `src/oracle/rag/` |
| Delegation: packet, egress preview, worktree, verification, Claude adapter | **implemented, live-verified** | `src/oracle/delegation/`, `integrations/` |
| ORACLE's MCP server (delegate callback) | **implemented, live-verified** | `src/oracle/mcp/` |
| Desktop UI: chat, palette, confirmations, terminal, delegation panel | **implemented** (127 UI tests) | `apps/desktop/` |
| Context assembly bands 5–7 (memory/retrieval/history) | **partially implemented** — budget exists, no producers | `context/budget.py` |
| Memory subsystem | **planned only** — 0 LOC | [MEMORY.md](MEMORY.md) |
| AntigravityAdapter | **planned only** — contract verified, adapter unbuilt | [OQ-05](OPEN_QUESTIONS.md#oq-05) |
| Task graph, planner, multi-worker supervision | **new work** — this roadmap | [ORCHESTRATION.md](ORCHESTRATION.md), [PLANNER.md](PLANNER.md) |
| Pipelines, mobile, voice, orbit view | **deferred as before** | below |

**Needs refactor** (narrow, named): `DelegationService` becomes the runner for one task kind
(P7) · Handoff Packet gains the `TaskSpec` superset (P8) · `AgentCaps` grows into the capability
registry (P8) · ARCHITECTURE.md's `packages/` inventory corrected to the real `src/oracle/` layout
(done in this replan).

### The MVP, stated once

> ORACLE runs on my PC, holds a conversation with a local model, executes a guarded set of file,
> git and dev commands against my real projects, shows me what it's doing in a desktop UI with a
> working terminal, asks before anything risky, and logs everything.

**Delivered** (Phases 0–4, 2026-08-21). Retained here because [ADR-0016](DECISIONS.md#adr-0016--mvp-excludes-the-interesting-parts)
binds to it and because it remains the scope-creep test for the foundation: anything the
supervisor arc adds must justify itself against the phases below, not against this sentence.
The supervisor-arc equivalent is the long-term goal, stated once:

> I give ORACLE a high-level goal; it safely coordinates local models, Claude, Antigravity,
> deterministic tools, pipelines, RAG and memory — while itself remaining local, secure,
> observable, modular, replaceable, testable, and open-source friendly.

## Phase map

```
 FOUNDATION (built) ────────────────────────────────────────┐
  P0 Walking skeleton · P1 Local LLM + runtime              │
  P2 Policy gate · P3 Tools · P4 Desktop UI  ★ MVP          │
  P5 Knowledge (RAG) · P6 Delegation (Claude, MCP, egress)  │
 ───────────────────────────────────────────────────────────┘
 SUPERVISOR ARC
  P6-T5  Antigravity adapter + planning spike   ← DONE 2026-08-24 (OQ-20: no)
  P7     Task graph & supervisor
  P8     Planner integration & multi-worker
  P9     Memory & context engine
 CAPABILITY ARC
  P10    Pipelines (on the task graph)
  P11    Execution visualisation & advanced UI
  P12    Mobile
 EXPERIMENTAL
  P13    Voice
 CONTINUOUS
  P14    Hardening (standing, began at P2)
```

**Why this order.** The spike (P6-T5) is deliberately first and deliberately tiny: the planner arc
rests on one unverified assumption — that `agy --json-schema` reliably returns a valid structured
plan — and the fallback ladder changes shape if it fails, so it is answered before the graph is
built, not after. P7 before P8 because a scheduler that can run *hand-written* graphs is testable
without any planner and is the rollback position if planning disappoints. Memory at P9, not
earlier, because P7/P8 need only the `Attempt` record (built in P7 as a table write, not a
subsystem) while full memory competes with nothing until context quality becomes the bottleneck —
which [OQ-18](OPEN_QUESTIONS.md#oq-18) says it already is for retrieval, so P9 carries that too.
Pipelines move *after* the graph exists so a pipeline is a compiled task graph rather than a
parallel executor. Mobile, voice, orbit keep their old relative order and their old reasons.

---

## Phase 6 — **done, 2026-08-24**: P6-T5, the Antigravity adapter + planning spike

**Outcome.** The adapter is built, fixture-tested and in `make check`. The spike answered its
central question with a **no**: Antigravity returned valid `ExecutionPlan`s 75% of the time
against a 90% gate, so the fallback ladder promoted and **Claude is Phase 8's default planner**
([OQ-20](OPEN_QUESTIONS.md#oq-20),
[dev log](../logs/development/2026-08-24-p6t5-antigravity-planning.md)). A documented "no" was a
success condition of this task, and it cost one line of the capability registry — which is what
designing the ladder before running the spike was for.

**Objective (as set).** Close Phase 6's open task and answer the planner arc's blocking questions
with one small, isolated experiment.

**Why now.** ROADMAP task 7 of old Phase 6 was never built; the CLI contract is verified
([INTEGRATIONS.md §5](INTEGRATIONS.md#5-antigravity--supported-unblocked-2026-08-21)); and the
replan's riskiest assumption (structured plans from `agy`) is testable in days.

**The spike answers, with evidence:** launch reliability from a subprocess · `--json-schema`
conformance rate for `ExecutionPlan` · latency and token cost per planning call · cancellation
(SIGINT/SIGTERM) semantics · workspace boundary behaviour · fixture-recordability of the stream.
Each maps to a numbered question in the spike checklist; the ones already answered by OQ-05 are
not re-asked.

**Acceptance.** `AntigravityAdapter` passes the adapter contract tests against recorded fixtures ·
one supervised live planning run returns a plan that validates (or the failure is recorded and the
fallback ladder is promoted) · one worker task executed from that plan end to end through the
existing delegation lifecycle · findings recorded in a dev log and OQ-20 resolved or narrowed.

**Rollback.** The spike is isolated (`scripts/` + one adapter file + fixtures); a negative result
costs the adapter only, and Phase 7 proceeds with hand-written graphs regardless.

**What it actually cost, and what it bought.** ~1.2M Antigravity tokens across 20 live calls and
one Claude delegation. It bought: the adapter, four recorded fixtures, a measured answer to OQ-20,
and three findings that outlive the verdict — a delegated planner **browses the filesystem** and is
stopped only by the permission gate ORACLE refuses to skip ([SECURITY.md §10](SECURITY.md#10-the-multi-agent-surface--added-2026-08-24-phases-78)) ·
`structured_output` can be **silently emptied** by the vendor's own schema filter · a vendor CLI can
**self-update mid-session** (1.1.17 → 1.1.19), so quarterly re-verification is a floor.

---

## Phase 7 — Task graph & supervisor  **[Supervisor arc]**

**Goal.** ORACLE runs a **hand-written** multi-task graph: durable tasks, dependencies, batched
topological scheduling, bounded concurrency, cancellation, crash recovery, human gates — with no
planner involved yet.

**Why now.** It is the load-bearing new subsystem, and it is fully testable deterministically
(FakeProvider + stub CLIs) without any new vendor risk. Everything later plugs into it.

**Depends on.** P6 (delegation lifecycle — reused as the DELEGATION runner). Not on P6-T5.

**Existing work reused.** `DelegationService` (becomes `DelegationRunner`, its lifecycle intact) ·
`ToolExecutor` (the TOOL runner) · event log, approvals, audit — unchanged. Ported from Asterim
([ASTERIM_REUSE.md](ASTERIM_REUSE.md)): DAG algebra, status vocabulary, recovery rules, gate
rules, launcher edge cases.

**New implementation.** `src/oracle/orchestration/`: Task/TaskResult models · migration `0002`
(`tasks` table) · graph validation (cycle-as-path) · scheduler loop · runners per TaskKind ·
retry/timeout/cancel per [ORCHESTRATION.md §3–4](ORCHESTRATION.md) · `task.*` event flow for
graphs · `Attempt` recording on task completion (the table, not the subsystem) · minimal API
(`GET /api/v1/tasks` grows graph fields) · minimal UI: the existing task list shows a tree.

**Tests.** Graph algebra property tests · scheduler determinism with fake runners · crash-recovery
tests that kill the daemon mid-graph · security: a graph cannot execute a tool its caller could
not; approvals bind per task; HALT kills every running task's tree.

**Acceptance criteria.**
- A hand-written 4-task graph (tool → delegation → verify → report) runs end to end with the
  delegation using the stub CLI; order and gating asserted like the reference scenario.
- One failure mid-graph: dependents `SKIPPED`, aggregate status correct, UI shows why.
- Kill the daemon mid-graph; restart recovers per the rules — the interrupted task gates, nothing
  auto-restarts, no event gap.
- Two delegations run concurrently in separate worktrees without interference; the third queues.
- Cancel one branch; the independent branch completes.
- The security suite grows the graph cases and stays green.

**Definition of done.** Acceptance met · `make check` green · ORCHESTRATION.md corrected to
as-built · report + next task updated.

**Risks.** Scheduler edge cases on Windows asyncio (see OQ-16's rule: pipes on threads) · scope
creep toward a workflow engine — the litmus is ORCHESTRATION.md §8.

**Rollback.** The graph is additive; the single-turn pipeline and single delegation path are
untouched and remain the default until P8 wires intent to graphs.

---

## Phase 8 — Planner integration & multi-worker  **[Supervisor arc]**

**Goal.** "Continue development on X" produces a validated `ExecutionPlan` from the planner, a
graph approval, scheduled workers with roles, verification, bounded replanning, and a report — the
[ORCHESTRATION.md §7](ORCHESTRATION.md#7-end-to-end-example) scenario, live.

**Why now.** P7 gave plans somewhere to run; P6-T5 established who can author them — **Claude**,
after Antigravity missed the conformance gate (OQ-20). Phase 8 builds the planner tier against the
Claude adapter, which already has structured output, fixtures and a pinned contract; Antigravity
stays in the registry as a reviewer/researcher.

**Depends on.** P7, P6-T5.

**Existing work reused.** Handoff Packet (extended to TaskSpec, [PLANNER.md §3](PLANNER.md#3-taskspec--the-specification-a-worker-receives)) ·
egress preview (planning calls are egresses) · `AgentCaps` → capability registry · pre-router and
intent (a new `continue_project`-class intent routes to the supervisor).

**New implementation.** `ExecutionPlan` schema + validation + one-repair · planner invocation as a
PLANNING task · role registry + `config/agents.yaml` · agent selection ([PLANNER.md §5](PLANNER.md#5-agent-selection)) ·
fallback ladder including deterministic template plans · replanning with lineage (`supersedes`) ·
plan-injection security fixtures · graph approval card.

**Acceptance criteria.**
- The reference multi-task scenario runs with FakeProvider + stub CLIs as one deterministic test
  asserting the order: context → planning egress approval → validation → graph approval → per-task
  gating → verification → report.
- An invalid plan gets exactly one repair, then falls to the ladder; asserted per rung.
- A planted injection in planning context yields a tainted plan whose tasks are escalated and the
  adversarial task inert without approval (security suite).
- A planner recommending an agent the policy forbids is overridden, and the audit shows the rule.
- One supervised live run of the full scenario on a real project, both vendors, all previews human-approved.
- Replan on a red verification produces a superseding task; budget exhaustion fails the root with
  the full lineage reported.

**Definition of done.** Acceptance met · PLANNER.md as-built · OQ-20 resolved · gate green.

**Risks.** Plan quality below usefulness → the ladder *is* the mitigation; measure, don't polish
prompts forever (fixture suite is the stop condition, as in P1) · cost per planned graph → the
approval card shows estimates; track spend per root task.

**Rollback.** Disable the planner path (config): ORACLE reverts to P7 graphs + P6 delegation.

---

## Phase 9 — Memory & context engine  **[Supervisor arc]**

**Goal.** The context bands stop being empty: facts, preferences, attempts retrieval into band 5;
retrieval into band 6 for `answer`/`reason` calls; history summarisation into band 7. Plus the
Phase 5 recall gate finally met or the gate re-argued with evidence ([OQ-18](OPEN_QUESTIONS.md#oq-18)).

**Why now.** Delegation and planning quality are now bounded by context quality: packets already
carry `ATTEMPTS.md`, and P7 writes attempts — this phase makes them retrievable, and closes the
known retrieval gap (truncated chunks first, query translation second, per OQ-18's ordering).

**Depends on.** P7 (attempt records exist). P8 benefits but does not block.

**Existing work reused.** [MEMORY.md](MEMORY.md)'s design stands as written — facts schema, write
policy, decay, the Memory UI view · `context/budget.py` bands · RAG for `task_signature` matching.

**New implementation.** `src/oracle/memory/`: facts + preferences store, attempt retrieval, the
restrictive write policy, memory events, REST endpoints, Memory view in the UI · band 5–7
producers wired into assembly · OQ-18's two levers measured in order.

**Acceptance criteria.** MEMORY.md's own rules, asserted: no write mid-plan, no write from a
tainted turn, contradiction surfaces instead of auto-deleting · a repeated task's packet carries
the prior attempt without hand-feeding · retrieval recall ≥ 80% on the fixture set, or the gate
re-set with a written argument · "why does ORACLE think that?" answerable in one click.

**Risks.** Wrong memories are worse than none — the write policy is the control; keep it
restrictive even if recall feels low at first.

**Rollback.** Memory is a band producer; disabling it returns context assembly to today's state.

---

## Phase 10 — Pipelines  **[Capability arc]**

**Goal.** [PIPELINES.md](PIPELINES.md) as specified — with one architectural change from the
replan: **a pipeline compiles to a task graph.** The YAML front-end, validation, up-front
approval, and scope guard are unchanged; the executor is P7's scheduler, not a second engine.
"A step is a delegation (or a tool task) — no second way to run an agent."

**Depends on.** P7. **Reused:** everything in P7; the YAML schema and validator are the new code.
**Acceptance:** as in PIPELINES.md §8, plus: a pipeline run and a hand-written graph of the same
steps produce identical event shapes. **Risk:** DSL creep — the litmus stands.

---

## Phase 11 — Execution visualisation & advanced UI  **[Capability arc]**

**Goal.** The UI represents the supervisor honestly, and the knowledge becomes visible:

1. The **execution tree** (root → plan → tasks → attempts → events) in the center stage and
   inspector ([UI.md §6b](UI.md#6b-the-execution-tree--phase-11)).
2. The orbit updated so the core is ORACLE and orbiting nodes are
   projects/task-groups/agents/collections ([UI.md §3](UI.md#3-the-core-orbital-view--phase-11)).
3. The **knowledge graph** *(added 2026-08-24 from the owner's design references)* — an
   interactive map of every indexed document across the Obsidian vaults, project docs and PDFs:
   collection-coloured clusters, wikilink and (optional) semantic edges, focus mode, retrieval
   traces, select-as-context. Full spec in [UI.md §11b](UI.md#11b-the-knowledge-graph--phase-11);
   layout/rendering decision in [ADR-0023](DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered).
   The data layer already exists (`knowledge.db` documents + embeddings + the `links` table);
   this phase adds the offline layout pass, the persisted positions, and the view.
4. Timeline, agent queue, global search, notifications as previously specified.

**Why late, still.** Amplifiers, not foundations — and now informed by real multi-agent event
data. The orbit keeps its go/no-go test (OQ-14) and its deterministic layout (ADR-0013; port
Asterim's `dagColumns` longest-path ranking for the tree). The knowledge graph opens with the
[OQ-22](OPEN_QUESTIONS.md#oq-22) measurements — offline layout cost, canvas-vs-SVG at corpus
scale, semantic-edge readability — *before* the view is built on them, per sequencing rule 6.

**Depends on.** P8 (there must be an execution to visualise); the graph view depends only on P5's
index and could start earlier if the phase is split — it shares no code with the supervisor arc.

**Acceptance:** UI.md's existing criteria plus:
- the execution tree answers "what is running, what is waiting on me, what failed, why" with
  labels covered · every task node links to its evidence in ≤ 2 clicks;
- the knowledge graph answers its four questions on the real corpus — shape (clusters/hubs),
  neglect (orphans and stale docs findable in one filter), reach (any note's 2-hop neighbourhood
  in one interaction), use (a chat citation's "show on graph" lights the actual sources);
- graph budgets hold as measured in OQ-22: 60 fps pan/zoom, idle < 5% CPU, first paint < 1 s,
  positions stable across sessions;
- the list-view equivalent passes the axe audit and offers every graph action;
- select-as-context feeds a real context package, and if that package later egresses, the
  ordinary preview prices it.

---

## Phase 12 — Mobile  **[Capability arc]**

Unchanged in scope and design ([MOBILE.md](MOBILE.md)): PWA, TLS + QR pairing + device tokens,
`since_seq` resume, approve/observe/ask subset, remote HALT, **T3 desktop-only**. The phone talks
to ORACLE, never to workers — the supervisor architecture strengthens this: task trees and worker
status are already API objects by P7/P11. Old Phase 8 acceptance criteria carry over verbatim.

---

## Phase 13 — Voice  **[Experimental]**

Unchanged ([old roadmap Phase 10](DECISIONS.md#adr-0007--clients-are-peers-of-one-local-api)
reasoning): a separate client process of the same API; zero core changes is the acceptance test;
TTS re-evaluated at implementation time (Piper archived). Still explicitly after the core
orchestration works — implementing voice before P8 lands is the named anti-goal it always was.

---

## Phase 14 — Hardening  **[Continuous]**

The standing workstream, begun at P2, now including: quarterly re-verification of **both** vendor
CLI contracts · the MCP 2026-07-28 migration watch ([OQ-21](OPEN_QUESTIONS.md#oq-21)) · the Claude
Agent SDK re-evaluation trigger ([OQ-19](OPEN_QUESTIONS.md#oq-19)) · cost-per-root-task tracking ·
the Pascal/CUDA watch (OQ-03) · security-suite growth with every new surface · backup/restore ·
performance budgets as tests.

---

## Idea backlog — three-tier local model stack  *(recorded 2026-08-23, unscheduled)*

Carried forward unchanged from the previous roadmap; nothing in the replan schedules it, and item
3 (prompt discipline for small models) is now partially absorbed by the TaskSpec renderer rules.

| Tier | Model | Role |
|---|---|---|
| Light | Qwen 2.5 3B | routing, classification, short deterministic transforms |
| Default | Qwen3 14B | the turn pipeline, tool calls, packet/prompt drafting |
| Heavy | Qwen3 27B | hard local work; browser search via a DeepSeek-style harness, if adaptable |

Implies, when scheduled: `LMStudioProvider` behind `LLMProvider` · escalation routing with
per-tier fixtures · the residency/eviction question · a feasibility spike for the browser harness
(touches the P2 gate — browsing is on the not-planned list and needs a design pass to move).
A 14B default would also make the **local model a planner-ladder candidate** above deterministic
templates — evaluate with the same ExecutionPlan fixtures, not by impression.

---

## Sequencing rules

1. **No phase starts before its predecessor's Definition of Done** — except P7, which does not
   wait for P6-T5 (they share no code; only P8 needs both).
2. **No new execution path bypasses the policy gate.** The scheduler is not a second chokepoint;
   it feeds the only one.
3. **Security tests remain the merge gate**, growing with every phase.
4. **Every phase updates `docs/current_task.md` and `docs/current_report.md`.**
5. **An `EXPERIMENT NEEDED` blocking a phase resolves inside that phase, before dependent code** —
   which is why P6-T5 exists at all.
6. **Spike before structure**: no phase builds on a vendor behaviour that has not been recorded
   into a fixture.
