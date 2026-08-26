# ORACLE — Roadmap

> **Rewritten 2026-08-24** for the supervisor architecture
> ([ADR-0019](DECISIONS.md#adr-0019--the-supervisor-completes-the-orchestrator)–[0022](DECISIONS.md#adr-0022--external-agent-frameworks-evaluated-not-adopted),
> analysis in [`logs/development/2026-08-24-supervisor-replan.md`](../logs/development/2026-08-24-supervisor-replan.md)).
> The previous roadmap's Phases 0–6 are **built** and are recorded below as the foundation, not
> re-scheduled as future work. Sequencing keeps the original discipline: every phase ends with
> something usable, and nothing executes a side effect before the machinery that governs it exists.
>
> **Amended 2026-08-26** against [VISION.md](VISION.md)
> ([dev log](../logs/development/2026-08-26-vision-realignment.md)): a **residency arc** (P12–P13)
> was inserted, the local tier stack was promoted out of the idea backlog to P16, and Mobile, Voice
> and Hardening were renumbered accordingly. Phases 0–11 are unchanged — the audit found the
> architecture already supports the vision; what it lacked was a persistent project entity and a
> reason to be running.

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
  P10    Pipelines (on the task graph)          ← DONE 2026-08-26
  P11    Execution visualisation & advanced UI  ← IN PROGRESS
 RESIDENCY ARC                                    added 2026-08-26 — VISION.md
  P12    Project state & the continue loop
  P13    Residency, boot & the briefing
  P14    Mobile
 EXPERIMENTAL
  P15    Voice
  P16    Local tier ladder                        GPU-CONDITIONAL, unscheduled
 CONTINUOUS
  P17    Hardening (standing, began at P2)
```

**The residency arc was inserted 2026-08-26** and the three phases after it were renumbered
(Mobile P12→P14, Voice P13→P15, Hardening P14→P17). The reason is
[VISION.md](VISION.md): the product is *"I turn on my computer, ORACLE is already there, and I can
simply tell it what I want done"*, and neither half of that sentence is true today. Mobile and voice
are amplifiers of a loop that does not yet close; project state and residency **are** the loop.

**Why this order.** The spike (P6-T5) is deliberately first and deliberately tiny: the planner arc
rests on one unverified assumption — that `agy --json-schema` reliably returns a valid structured
plan — and the fallback ladder changes shape if it fails, so it is answered before the graph is
built, not after. P7 before P8 because a scheduler that can run *hand-written* graphs is testable
without any planner and is the rollback position if planning disappoints. Memory at P9, not
earlier, because P7/P8 need only the `Attempt` record (built in P7 as a table write, not a
subsystem) while full memory competes with nothing until context quality becomes the bottleneck —
which [OQ-18](OPEN_QUESTIONS.md#oq-18) says it already is for retrieval, so P9 carries that too.
Pipelines move *after* the graph exists so a pipeline is a compiled task graph rather than a
parallel executor.

**The residency arc's placement, 2026-08-26.** P12 before P13 because the briefing is per-project
and its resume pointer lives on the project row. Both before mobile and voice because those are
*additional clients* of a loop that does not yet close — and because a phone is most useful exactly
when the desktop is unattended, which is only true once ORACLE keeps running unattended. P12 also
carries an unblock the capability arc needs: `tasks` is **0 rows**, so P11's orbit, timeline and
queue all render activity that has never happened, and the first real `continue` run is what
produces it. The orbit keeps its go/no-go test ([OQ-14](OPEN_QUESTIONS.md#oq-14)) and its old
reasons; what changes is that it can finally be judged against real data instead of a picture we
drew ourselves.

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

## Phase 7 — **done, 2026-08-25**: Task graph & supervisor  **[Supervisor arc]**

**Outcome.** ORACLE runs multi-task graphs: durable rows, dependency-ordered scheduling, real
runners over the existing executor and delegation lifecycle, verification as a **delta against a
baseline**, crash recovery that restarts nothing, cancellation from outside, HALT proven against a
real child process, `WAITING` for work parked on a person, and an API projection the desktop UI
renders as a tree. 88 orchestration tests, all offline and deterministic.

**And no planner anywhere in it**, which was the point of the ordering: when P6-T5's spike
returned "no" on Antigravity, Phase 7 did not care, because a graph does not depend on who
authored it. As-built detail in [ORCHESTRATION.md](ORCHESTRATION.md).


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

## Phase 10 — **done, 2026-08-26**: Pipelines  **[Capability arc]**

**Outcome.** A YAML file becomes a validated `Pipeline`, renders against its parameters, compiles
to a task graph and runs on P7's scheduler. **No pipeline executor, no `pipeline_runs` table, no
new `TaskKind`, no new runner, and exactly two new event types — neither of them a `task.*`.** The
roadmap's own extra criterion is a passing test: a compiled pipeline and a hand-written graph of
the same steps emit identical event sequences, element for element, with no type unique to either.

Two shipped pipelines ([`config/pipelines/`](../config/pipelines/)), both priced against the real
policy and the real tool registry by a test — `oracle-selfcheck` runs ORACLE's own merge gate and
is the live demonstration of "asks once, before starting", because four of its six steps are T2.

**DSL creep was the risk and the answer was seven refusals**, each a model field or a missing enum
member rather than a review note: no `{{ steps.*.* }}`, no `when` over a step result, no
`on_failure: ask`, no `retry: { on: [...] }`, no `capture: junit`, no `report` step, no T3. Four of
those were things PIPELINES.md itself specified; §2's worked example named three tools and
arguments that do not exist. The document is corrected in place with the reason for each.

**And it found a P7 defect.** `Limits.timeout_s[TaskKind.TOOL]` is 120 s while `dev.run_tests`
declares 630 s and `dev.build` 930 s — so **any TOOL task running either was killed at two minutes
and recorded as `TIMEOUT`**, which reads as "the tests hung" rather than "the scheduler did not
wait". `Task.timeout_s` (migration `0004`) adds the `task` level ORCHESTRATION.md §3 already
specified. It is the only change P10 made below the pipeline layer, and it stands on its own merits.

**The one deliberate v1 acceptance:** a `continue` step and its successor may run concurrently,
because "nothing depends on it" is how `continue` is expressed. The alternative touches
`graph.ready()`'s fail-closed rule, which is a P7-core change and not one to make on speculation.

As-built detail in [PIPELINES.md](PIPELINES.md).


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

## Phase 12 — Project state & the continue loop  **[Residency arc]**

> **This is the phase the product is waiting on.** Design:
> [PROJECT_STATE.md](PROJECT_STATE.md) · decision:
> [ADR-0024](DECISIONS.md#adr-0024--a-project-is-a-first-class-persistent-entity) ·
> product rationale: [VISION.md §5](VISION.md#5-what-is-persistent).
>
> **T1 done 2026-08-26** — migration `0005`, the registry, `ProjectObservation`, counters and
> three endpoints ([as built](PROJECT_STATE.md#as-built--p12-t1-2026-08-26)). T2–T5 below.

**Objective.** Make *"continue Asterim"* answerable: ORACLE resolves the project, reads its real
state, decides what remains, plans, dispatches one worker, verifies with evidence, reports, and
records the result against the project.

**Why here and not later.** Three otherwise-unrelated problems collapse into this one phase:

1. The vision's headline utterance does not route — there is no `continue` intent, and no state for
   it to read if there were.
2. `tasks` is **0 rows**. Every view in P11 renders supervisor activity that has never happened, and
   [OQ-14](OPEN_QUESTIONS.md#oq-14) cannot be judged against a picture we drew ourselves.
3. `memory_facts` and `memory_attempts` are keyed by project and are also **0 rows** — P9 shipped a
   subsystem with no entity to attach to.

The first real `continue` run produces the data that answers all three.

**Reuses (no new machinery).** The task graph and scheduler (P7) · the planner ladder and TaskSpec
(P8) · memory bands, already project-scoped (P9) · `core/projects.py` detection, unchanged in role ·
the tool layer for every observation · the event log's `seq` as the briefing pointer.

**New work.**

| | |
|---|---|
| Migration `0005` | `projects` table; index on `tasks(project, status)` |
| `core/projects.py` grows a registry | register / rename / archive; identity is the row id, not the directory name |
| `ProjectObservation` | read-through-tools reader for branch, ahead/behind, dirty count, last commit; `error` is a field, never an exception |
| Unfinished-work derivation | from the task graph first; repo task documents as `local_foreign` evidence only |
| `continue` intent label | + fixtures, + **a re-run of the intent eval** |
| Project counters | denormalised, rebuildable, never authoritative |
| API | `GET /api/v1/projects`, `POST /api/v1/projects`, `GET /api/v1/projects/{id}`; the sidebar stops rendering a bare name list |

**Tasks.** T1 the entity (**done**) · T2 the `continue` intent and unfinished-work derivation ·
T3 the briefing · T4 the sidebar and inspector reading real project state · T5 the first real
`continue` run, end to end, with a person watching every approval.

**Migration work.** `discover_projects()` becomes a *candidate* source rather than the registry.
Existing `memory_facts`/`memory_attempts` rows are keyed by project **name**; they are 0 rows today,
so the backfill is empty — this phase is the last cheap moment to change that key, and taking it
later means writing a migration against real data.

**Tests.** Registry operations preserve `id` across rename · a deleted root renders `MISSING` and
degrades nothing else · counters recomputed from `tasks` equal stored values after a graph runs ·
security: no direct subprocess path in the observer, repo task documents cannot become instructions,
**registering a project widens no policy scope**.

**Acceptance criteria.**

- [ ] `continue <project>` produces a plan from *real* state, or asks a question when state is empty
      — it never invents work.
- [ ] One real end-to-end run completes and writes rows to `tasks` with evidence, timings and cost.
- [ ] The sidebar's `Asterim  2 tasks  branch main +3` line renders from real data.
- [ ] Observed state is never persisted — asserted by a test, not by convention.
- [ ] `make check` green, security suite grown.

**Definition of Done.** A person says "continue Asterim", walks away, and comes back to a completed
or gated task graph whose every number came from the machine rather than from a fixture.

**Risks.** The intent eval regresses when a label is added (measured surface — re-run it, do not
assume) · the observation fan-out misses the glance budget (`EXPERIMENT NEEDED`; the answer is lazy
per-row reads, never a cache) · unfinished-work derivation over-collects and the first plan is
enormous (bound it, and prefer asking).

---

## Phase 13 — Residency, boot & the briefing  **[Residency arc]**

> Decision: [ADR-0025](DECISIONS.md#adr-0025--oracle-is-a-resident-service-the-window-is-a-client) ·
> product rationale: [VISION.md §6](VISION.md#6-what-happens-when-the-pc-boots).

**Objective.** ORACLE is running before I look at it, and when I look it tells me what changed.

**Reuses.** Crash recovery, already built and already correct — interrupted graphs gate, they never
auto-resume · `ARCHITECTURE.md §8` degradation, already the reason Ollama being down is a supported
state · `since_seq`, already global and gap-free · the global HALT hotkey, already specified as
window-independent.

**New work.** `oracled` installed as a Windows service or scheduled task, starting
**degraded-capable** rather than eagerly · a boot health phase over Ollama, both databases, the
index, agent CLIs and policy · the briefing surface, advancing `briefed_through_seq` **on
acknowledgement only** · the shell attaches to a running daemon instead of supervising a sidecar.

**Depends on.** P12 — the briefing is per-project and the resume pointer lives on the project row.

**Tests.** An unacknowledged briefing survives a restart · a service that died overnight is the
first line of the next briefing · HALT works with no window open · boot with Ollama down reaches
ONLINE and says which capability is missing · no interrupted worker is ever auto-resumed (this test
already exists and must keep passing under service start).

**Acceptance criteria.**

- [ ] Reboot the machine; ORACLE is online without anyone starting it.
- [ ] Within 3–5 seconds of the window opening: what ran, what finished, what failed, what is
      waiting, what is next.
- [ ] The briefing does not clear itself on render.
- [ ] Boot animation ≤ ~400 ms.
- [ ] Closing the window does not stop work; reopening it loses nothing.

**Risks.** A background service crashing invisibly (mitigated by making it brief itself) · autostart
holding a GPU-resident model from boot on a 4 GB card (mitigated by degraded-capable start) ·
Windows service permissions interacting with Job Objects — `TO VERIFY` before this phase, cheaply.

---

## Phase 14 — Mobile  **[Capability arc]**

Unchanged in scope and design ([MOBILE.md](MOBILE.md)): PWA, TLS + QR pairing + device tokens,
`since_seq` resume, approve/observe/ask subset, remote HALT, **T3 desktop-only**. The phone talks
to ORACLE, never to workers — the supervisor architecture strengthens this: task trees and worker
status are already API objects by P7/P11. Old Phase 8 acceptance criteria carry over verbatim.

*Renumbered from Phase 12 on 2026-08-26; scope untouched.* It benefits from the residency arc rather
than depending on it: a phone is most useful precisely when the desktop is unattended, which is only
true once ORACLE keeps running while unattended.

---

## Phase 15 — Voice  **[Experimental]**

Unchanged ([old roadmap Phase 10](DECISIONS.md#adr-0007--clients-are-peers-of-one-local-api)
reasoning): a separate client process of the same API; zero core changes is the acceptance test;
TTS re-evaluated at implementation time (Piper archived). Still explicitly after the core
orchestration works — implementing voice before P8 lands is the named anti-goal it always was.

*Renumbered from Phase 13 on 2026-08-26; scope untouched.*

---

## Phase 16 — Local tier ladder  **[Experimental · GPU-CONDITIONAL, unscheduled]**

> Decision: [ADR-0026](DECISIONS.md#adr-0026--the-local-tier-ladder-is-capability-shaped-and-gpu-conditional).
> Promoted 2026-08-26 from the idea backlog, where it sat unscheduled since 2026-08-23.

**Objective.** A capability-tiered local ladder — trivial extraction, summarisation, RAG answers,
private work — so that work which never needs to leave the machine, does not.

**Why it is a phase and not a config change.** The tiers in the vision are described by *capability*
("classification", "summaries", "local/private tasks"), which is durable and can be designed now.
The parameter counts are perishable and depend on hardware that does not exist yet. ADR-0004's own
history is the argument: its original choice was `2b` "based on arithmetic", and **that was wrong** —
`2b` splits 36/64 CPU/GPU at every context length. Only measurement caught it.

**`ASSUMPTION` — no GPU model, VRAM figure or arrival date has been stated.** Until one is, this
phase is not scheduled and [ADR-0004](DECISIONS.md#adr-0004--two-tier-local-model-router--reasoner)
stands as written.

**What a GPU change re-opens** — as measurements to re-run, not settings to edit:

| Question | Today's answer, and why it was that |
|---|---|
| Which model routes? | `qwen3.5:0.8b` — the largest that is 100% GPU-resident at 16k ([OQ-01](OPEN_QUESTIONS.md#oq-01)) |
| Where do embeddings run? | CPU, so the router keeps the GPU ([ADR-0014](DECISIONS.md#adr-0014--embeddings-on-cpu-gpu-reserved-for-the-router)) |
| Is the context budget still split by call type? | Yes — forced by measured prompt-processing rates |
| Is tool pre-filtering still load-bearing? | Yes — ~1,200 tokens of schemas costs ~730 ms per turn |
| Can a local model author plans? | Not evaluated. **Same `ExecutionPlan` fixtures as [OQ-20](OPEN_QUESTIONS.md#oq-20)** — not impression |

**Standing rule, hardware-independent.** *Bigger is not better per task.* Model swap time dominates
inference time on this class of machine, so a resident small model beats a swapped large one for
routing. Tier selection is a function of (task shape, residency, privacy) — never of "which model is
smartest".

**New work when scheduled.** A second `LLMProvider` implementation if the runtime changes · tier
selection alongside the existing capability registry, which already selects by role rather than by
vendor · per-tier fixtures · the residency/eviction question · a re-run of the intent eval on
whatever routes.

---

## Phase 17 — Hardening  **[Continuous]**

The standing workstream, begun at P2, now including: quarterly re-verification of **both** vendor
CLI contracts · the MCP 2026-07-28 migration watch ([OQ-21](OPEN_QUESTIONS.md#oq-21)) · the Claude
Agent SDK re-evaluation trigger ([OQ-19](OPEN_QUESTIONS.md#oq-19)) · cost-per-root-task tracking ·
the Pascal/CUDA watch (OQ-03) · security-suite growth with every new surface · backup/restore ·
performance budgets as tests.

*Renumbered from Phase 14 on 2026-08-26; scope untouched. Its number never implied its order — it
began at P2 and never stops.*

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
