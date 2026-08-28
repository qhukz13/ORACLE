# 2026-08-24 — The supervisor replan: what the audit actually found

An architecture replan was requested: rebuild ORACLE around a multi-agent supervisor — ORACLE as
deterministic runtime, Antigravity as planner, Claude as worker, local models as lightweight
workers — with a task graph, structured plans, roles, and a capability registry. Before touching a
doc, three investigations ran: a full implementation-vs-docs audit of this repo, a reuse audit of
Asterim (both repos), and a survey of the external protocol/SDK landscape as of 2026-08.

This log records what they found and the decisions the findings force. The doc updates that follow
are downstream of this file.

## Finding 1 — the requested architecture is mostly already this architecture

The replan brief assumed ORACLE was "a small local model with routing". It is not, and has not been
since ADR-0001. What exists (verified in source, not docs):

| Requested by the brief | Already built | Where |
|---|---|---|
| ORACLE as deterministic supervisor, not the smartest model | yes — pre-router, deterministic escalation, ~5 ms paths | `src/oracle/router/` |
| Security gate every worker action crosses | yes — one gate, 265 security tests | `src/oracle/policy/` |
| Workers via headless CLIs, structured output | yes for Claude — pinned stream contract, fixtures | `src/oracle/integrations/claude.py` |
| Vendor-neutral task specification | yes — the Handoff Packet (6 files + packet.json) | `src/oracle/handoff/` |
| Egress preview, worktree isolation, independent verification | yes, live-verified 2026-08-24 | `src/oracle/delegation/service.py` |
| Guarded tool callback for delegates | yes — ORACLE's MCP server, live-verified | `src/oracle/mcp/` |
| Event-sourced observability with trace ids | yes | `src/oracle/core/` |
| No permanent vendor chat session | already the design — one `-p` run per delegation | ADR-0012 |

What is genuinely **not** built (the audit was unambiguous):

1. **No task graph.** `task_id` exists on events, but the only task producer is `DelegationService`,
   which manages exactly one delegation as a linear lifecycle. No `tasks` table, no dependencies,
   no scheduler, no concurrency.
2. **No planner and no critic.** A routed turn selects **one** tool with **one** model-supplied
   string. The `Plan`/`PlanStep` schema in AGENT_RUNTIME.md §4 was never implemented — deliberately
   (`router/pipeline.py:15-19` states the properties). The state machine's `planning` state is tool
   selection.
3. **No memory subsystem.** `docs/MEMORY.md` is a design; `src/oracle/memory/` does not exist.
   Context bands 5–7 (memory, retrieval, history) have budget allowances and zero producers.
4. **No AntigravityAdapter** — Phase 6's one unbuilt task, against an already-verified CLI contract.
5. **No pipelines, no mobile** (correctly Phase 7/8, never claimed built).

**Consequence:** this is not a migration away from a wrong architecture. It is the *completion* of
ADR-0001 upward: from "route one turn to one executor" to "supervise a graph of tasks across
several executors". Everything below the runtime — gate, toolhost, tools, RAG, adapters, MCP,
events, UI — is kept as-is. The replan adds three subsystems (task graph, planner tier, memory)
and one adapter, and renumbers the roadmap.

Also found: the documented `packages/` component inventory never materialised. The real layout is a
flat `src/oracle/` package (79 files, 16.4k LOC) plus `apps/desktop`. ARCHITECTURE.md §9 was
fiction and is corrected in this replan.

## Finding 2 — Asterim contains a working prototype of the supervisor

Full audit in [docs/ASTERIM_REUSE.md](../../docs/ASTERIM_REUSE.md). The short version:

- **`C:\Projects\asterim-pipeline` (~8k LOC, zero-dependency Node) is a working, Windows-proven
  supervisor**: explicit state machine with a transition table, atomically-persisted state with
  crash recovery ("previous PID alive → human gate, never auto-restart"), an agent launcher that
  handles tree-kill/timeouts/`.cmd` shims, result validation that never trusts exit codes, and an
  empirically-derived list of when to stop and ask a human. It drives an Antigravity orchestrator
  plus two Claude workers today. Roughly 70% of ORACLE's supervisor core exists here as tested
  reference code, in the wrong language.
- **Big Asterim** contributes: a pure-function task-DAG algebra (cycle-as-path detection,
  topological ready-set, aggregate status with a deliberate precedence, SKIPPED ≠ CANCELLED),
  delegation bounds (depth ≤ 3, width ≤ 4, three-valued outcome where TIMEOUT ≠ FAILED), an
  event persist/replay/redact strategy, and a turn lock whose FIFO order is decided synchronously.
- **What not to copy** is as instructive: Asterim's 587-LOC terminal-screen-scraping FSM exists
  only because a TUI agent has no structured output. ORACLE drives `--output-format stream-json`
  and skips that entire problem class. Asterim also ships `--dangerously-skip-permissions` on its
  orchestrator with a written apology; ORACLE's answer to the same problem is the MCP callback
  surface plus `--permission-mode dontAsk` with explicit allow rules — strictly better, already
  built, and staying.

## Finding 3 — the external landscape, verified 2026-08

Survey details and sources are recorded in [INTEGRATIONS.md §10](../../docs/INTEGRATIONS.md) and
the ADRs. Verdicts:

| Candidate | Verdict | Reason |
|---|---|---|
| **Antigravity headless (`agy -p`)** | **adopt** (adapter) | Officially documented: `--output-format json\|stream-json`, `--json-schema`, `--continue/--conversation`, `--print-timeout`. Near-isomorphic to Claude's contract, which justifies sharing the stream-worker plumbing. Already locally verified (OQ-05). No official SDK confirmed; no native ACP. |
| **Claude Agent SDK (Python)** | **evaluate, don't switch yet** (OQ-19) | Wraps the same CLI with typed events and PreToolUse hooks that could enforce the gate in-process. But it is 0.x, replaces a *working, fixture-pinned* contract, and moves the pinned surface from flags to an unstable API. Re-evaluate when the CLI contract next drifts. |
| **ACP (Agent Client Protocol)** | reference; future pluggability | JSON-RPC/stdio, permission-request channel maps cleanly onto the gate. But Claude and Antigravity are both adapter-mediated (Node shims), so today it adds a process hop and a dependency to reach agents ORACLE already reaches natively. Right answer for a hypothetical third-party agent, not for the two that matter. |
| **LangGraph** | not adopted | Its headline features (durable execution, checkpointing, replay) duplicate the event-sourced runtime. The task graph ORACLE needs is ~300 LOC of pure functions, half of which Asterim already wrote. |
| **CrewAI** | not adopted; vocabulary referenced | Role/delegation vocabulary is useful; an LLM manager-agent is the opposite of a deterministic supervisor, and its hierarchical mode has documented routing bugs. |
| **OpenHands SDK** | reference architecture | MIT, modular, event-stream model that parallels ORACLE's. Worth reading; adopting its agent-server would duplicate the runtime. |
| **AutoGen / MS Agent Framework** | not adopted | AutoGen is in maintenance mode; the successor's typed workflow graphs are reference material only. |
| **A2A** | out of scope | Peer-agent interop over HTTP; relevant only if ORACLE ever exposes itself to external systems. |
| **MCP 2026-07-28 spec** | watch item (OQ-21) | Stateless core + Tasks extension + MRTR. ORACLE's hand-rolled server speaks `2025-06-18`; migrate when a client requires it, per the existing "take the SDK if a client rejects the surface" rule. |

No licensing blockers anywhere relevant: everything is MIT or Apache-2.0. `PyMuPDF`-style AGPL
entanglements were not found among the candidates.

## The decisions this forces

1. **KEEP** the runtime, gate, toolhost, tools, RAG, event log, MCP server, delegation lifecycle,
   Claude adapter, UI, and every security control. Unchanged.
2. **ADD** a durable task graph (`orchestration/`), a planner tier behind the existing adapter
   seam, and the memory subsystem the packets already want. New ADRs 0019–0022.
3. **REFACTOR, narrowly**: `DelegationService` becomes the *runner* for one task kind rather than
   the whole story; the Handoff Packet gains a machine-readable TaskSpec superset; `AgentCaps`
   grows into a small capability registry with roles.
4. **ADOPT** from Asterim by porting (not importing): the DAG algebra, the recovery/gate rules,
   the launcher edge cases, the status vocabulary.
5. **DEFER**: ACP adapter, Claude Agent SDK switch, MCP spec migration, multi-machine dispatch —
   each has an open question or a trigger condition, none blocks the spike.
6. **REMOVE** nothing but fiction: the `packages/` inventory in ARCHITECTURE.md, and the
   never-implemented multi-step `Plan` schema in AGENT_RUNTIME.md §4, which is superseded by the
   task graph + planner design rather than finally built as written.

The first implementation step is the **planner spike** (new `current_task.md`): the
AntigravityAdapter against the verified contract, plus one structured planning round-trip and one
worker task executed from it. If `agy --json-schema` cannot reliably return a valid
`ExecutionPlan`, the fallback ladder (Claude as planner → deterministic template plans → human
plan) is the design, not a patch — which is why the spike runs before the task-graph phase builds
anything on the assumption.
