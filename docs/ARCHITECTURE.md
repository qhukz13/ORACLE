# ORACLE — Architecture

> Status: **design**, no implementation. Decisions here are binding unless superseded by a new ADR
> in [DECISIONS.md](DECISIONS.md).

## 1. What ORACLE is

A **local orchestrator**. It receives intent, decides who should do the work, assembles the context
that worker needs, enforces what that worker is allowed to touch, and reports the result.

It is explicitly *not* a large model with a shell. The intelligence is distributed:

| Concern | Handled by | Why |
|---|---|---|
| Intent understanding, routing, short answers | small local LLM (~2B) | fast, private, free, good enough |
| Deliberate multi-step planning | mid local LLM (~4B) or delegation | rarely needed, expensive |
| Deep code reasoning, large refactors | Claude Code / Antigravity | genuinely better at it |
| Deterministic work (git, tests, search, launch) | plain code | an LLM adds latency and error, nothing else |

**The most common correct action is not to call the LLM at all.** Slash commands, palette actions,
saved pipelines and exact-match tool syntax bypass the model entirely. The model is the fallback for
ambiguity, not the front door. This is what makes a 2B model viable as the primary interface.

### Non-goals

Written down so scope creep is a visible violation, not a drift.

- Not a CI system. Pipelines run *my* workflows locally; they do not replace GitHub Actions.
- Not a cloud service. No multi-tenancy, no accounts, no horizontal scaling.
- Not an IDE. It does not edit code in-editor; it delegates that.
- Not a general RPA/automation framework. Mouse/keyboard synthesis is a last-resort tool, not a feature.
- Not a hardware monitor. System stats exist to explain agent latency, nothing more.

---

## 2. System context

```
   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
   │  Desktop    │   │  Browser    │   │  Phone      │   │  Voice      │
   │  (Tauri)    │   │  (any tab)  │   │  (PWA)      │   │  (daemon)   │
   └──────┬──────┘   └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
          │                 │                 │                 │
          └────────┬────────┴────────┬────────┴────────┬────────┘
                   │   HTTP + WebSocket, TLS, token-authenticated
          ┌────────▼──────────────────────────────────────────┐
          │                  oracled  (Python)                │
          │  API ─ Runtime ─ Router ─ Policy ─ Knowledge ─ Log │
          └────────┬──────────────────────┬──────────────┬────┘
                   │ spawn (restricted)   │ HTTP         │ file
          ┌────────▼────────┐   ┌─────────▼────────┐  ┌──▼──────────┐
          │ oracle-toolhost │   │  Ollama          │  │  SQLite ×2  │
          │ (executes OS    │   │  (local models)  │  │  + JSONL    │
          │  side effects)  │   └──────────────────┘  │  audit log  │
          └────────┬────────┘                         └─────────────┘
                   │
          ┌────────▼─────────────────────────────────┐
          │ OS · filesystem · git · docker · npm ·   │
          │ Claude Code CLI · Antigravity CLI (agy)  │
          └──────────────────────────────────────────┘
```

**All four clients are peers.** None has a privileged path. This is the single most important
structural decision in the project: it means adding voice (Phase 10) or mobile (Phase 8) requires
*zero* changes to the agent core, and it means the desktop shell can be replaced or dropped without
losing functionality. See [ADR-0007](DECISIONS.md#adr-0007--clients-are-peers-of-one-local-api).

---

## 3. Process model

Three OS processes, three trust levels. **The privilege boundary is a process boundary, not a
function call.**

```
┌──────────────────────────────────────────────────────────────────────┐
│ oracled                                          TRUST: high         │
│  Holds: policy, secrets, DB handles, audit log, device tokens        │
│  Never: executes a shell command, writes outside its own data dir    │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ JSON-RPC over pipe; argv lists only
┌───────────────────────────────▼──────────────────────────────────────┐
│ oracle-toolhost                                  TRUST: low          │
│  Holds: nothing durable. Receives a *pre-authorised* ToolInvocation. │
│  Cannot: read policy, read secrets, re-enter the runtime, widen scope│
│  Runs in a Windows Job Object → whole process tree is killable       │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ CreateProcess / file I/O
┌───────────────────────────────▼──────────────────────────────────────┐
│ child processes (git, npm, pytest, claude, agy, …)   TRUST: none     │
└──────────────────────────────────────────────────────────────────────┘
```

Why the separation is worth an IPC hop:

- A tool crash, hang, or memory blowup cannot take down the agent, the UI, or the audit log.
- A compromised or buggy tool cannot read `ANTHROPIC_API_KEY` — it was never in its address space.
- Cancellation and emergency stop become *real*: kill the job object and the whole tree dies. Killing
  a thread inside a single process does not reliably kill `npm install`'s grandchildren on Windows.
- The toolhost can be hardened further later (restricted token, AppContainer, separate user account)
  without touching agent code.

The model provider (Ollama) is a **fourth, separate** process we do not own. It is treated as an
untrusted network dependency behind an adapter: it may be down, slow, or return garbage, and ORACLE
must degrade rather than fail.

---

## 4. Layers

The layering from the brief was mostly right. Two changes:

1. **Policy is not a layer, it is a gate.** Drawing it as a horizontal layer implies you could route
   around it. It sits on exactly one chokepoint — the Tool Host boundary — and everything crosses it.
2. **Context Assembly is promoted to a first-class subsystem.** With a 2–4B model, context is the
   scarcest resource in the system. Treating assembly as a helper function is how these projects fail.

```
┌────────────────────────────────────────────────────────────────┐
│ L1  Presentation      desktop / browser / phone / voice        │
├────────────────────────────────────────────────────────────────┤
│ L2  API + Event       REST for state, WS for stream, auth,     │
│                       resumable event feed                     │
├────────────────────────────────────────────────────────────────┤
│ L3  Agent Runtime     sessions, turns, tasks, cancellation,    │
│                       state machine, event sourcing            │
├────────────────────────────────────────────────────────────────┤
│ L4  Router + Planner  deterministic pre-router → intent →      │
│                       bounded plan → step execution → critic   │
├────────────────────────────────────────────────────────────────┤
│ L5  Context Assembly  budget, retrieve, rank, redact, render   │
├────────────────────────────────────────────────────────────────┤
│ L6  Capability Layer  tool registry, contracts, schemas        │
│         ══════════ POLICY GATE ══════════  ← the only crossing │
│ L7  Tool Host         execution, isolation, timeouts, undo     │
├────────────────────────────────────────────────────────────────┤
│ L8  Adapters          LLMProvider · ExternalAgent · VectorStore│
├────────────────────────────────────────────────────────────────┤
│ L9  Storage           SQLite ×2 · audit JSONL · blobs · config │
└────────────────────────────────────────────────────────────────┘
```

### Responsibilities and boundaries

| Layer | Owns | Must never |
|---|---|---|
| **L1 Presentation** | rendering, local UI state, keyboard | contain business logic, hold credentials, talk to anything but L2 |
| **L2 API/Event** | authn/authz, serialization, backpressure, resume | make agent decisions, touch the filesystem |
| **L3 Runtime** | lifecycle, the event log, cancellation, HALT | call a tool directly, format prompts |
| **L4 Router/Planner** | intent, plan construction & validation, retries | execute anything, assume a tool exists without a registry lookup |
| **L5 Context** | token budget, retrieval, redaction, prompt rendering | mutate state, decide permissions |
| **L6 Capability** | tool contracts, argument validation | perform the side effect |
| **POLICY GATE** | allow / confirm / confirm_strong / deny + audit | be bypassed, be influenced by retrieved content |
| **L7 Tool Host** | actual execution, isolation, undo journal | re-enter L3–L6, read secrets it wasn't handed |
| **L8 Adapters** | vendor-shaped I/O, retries, normalisation | leak vendor types upward |
| **L9 Storage** | durability, migrations | contain logic |

**Dependency rule:** dependencies point downward only. L4 does not import L7. Anything that looks
like an upward call is an *event*, published to L3, not a function call.

---

## 5. Control flow — one request, end to end

Tracing `"why is Asterim auth broken?"` through the system. This is the reference flow; every
subsystem doc elaborates one band of it.

```
  1  L2   inbound  {type:"session.message", text:"why is Asterim auth broken?"}
          → trace_id assigned, appended to event log, echoed to all clients

  2  L4   PRE-ROUTER (no LLM)
          slash command? no.  palette action? no.  saved pipeline? no.
          → fall through to the model

  3  L4   INTENT  (router model, JSON-schema constrained, ~150 tok in / ~40 out)
          {intent:"investigate", project:"Asterim", confidence:0.81}
          project resolved against the project registry, never invented

  4  L5   CONTEXT ASSEMBLY  (budget: 6000 tok)
          signals   → git status/branch/recent commits, last failing test run
          retrieval → hybrid search "auth" scoped to project=Asterim
          memory    → pinned facts about Asterim + prior attempts at this
          redact    → secret scan over every chunk before it goes anywhere
          budget    → rank, truncate, attribute sources

  5  L4   PLAN  (bounded: ≤ 8 steps, every step must name a registered tool)
          1 git_status(Asterim)             T0
          2 search_project(Asterim,"auth")  T0
          3 read_file(...)                  T0
          4 run_tests(Asterim, auth)        T1
          5 delegate(claude, <packet>)      T2  ← needs confirmation
          the plan is data → rendered in the UI, editable, approvable

  6  L6   each step: validate args against the tool's JSON Schema
  ══      POLICY GATE: steps 1-3 auto · step 4 auto+audit · step 5 CONFIRM
  7  L7   execute, stream stdout/stderr, enforce timeout, journal undo

  8  L4   CRITIC  did the step satisfy its `expect`? retry (≤2), replan, or surface

  9  L5   for step 5: build the Handoff Packet, show the EGRESS PREVIEW
          → the user sees exactly what bytes leave the machine before they leave

 10  L8   ClaudeCodeAdapter: git worktree → claude -p --bare --output-format stream-json
          progress events streamed straight through to the UI

 11  L4   collect: diff the worktree, run tests, summarise

 12  L3   compose the answer with citations → L2 → all clients
```

Steps 1–3 typically complete in under a second and involve one small model call. If the model is
unavailable, steps 1–2 degrade to a deterministic keyword router and the system stays usable — §8.

---

## 6. Event flow

The runtime is **event-sourced**. Every state change is an append-only, monotonically sequenced
record. This is not architectural ornament; it buys four concrete things:

1. **Resumable clients.** The phone reconnects with `since_seq=1042` and catches up exactly. Without
   this, mobile over flaky Wi-Fi is unimplementable.
2. **Deterministic tests.** Record a session, replay it against a mocked tool layer, assert. This is
   how an agent gets a regression suite at all.
3. **A real audit trail.** "What did it do at 03:43?" becomes a query, not an archaeology project.
4. **A free UI.** The Activity Timeline *is* the event log, filtered.

```
  producer          event                              consumers
  ────────          ─────                              ─────────
  Runtime      session.created / turn.started    ─┬─▶  WS fan-out → clients
  Router       agent.state_changed               ─┼─▶  event store (SQLite)
  Router       plan.proposed / plan.step.started ─┼─▶  audit sink (security only)
  Policy       approval.requested / .resolved    ─┼─▶  metrics
  Tool Host    tool.started / .output / .finished─┘
  Integrations external.progress / .completed
```

Event envelope (canonical shape, see [API.md](API.md)):

```json
{ "v": 1, "seq": 1043, "ts": "2026-08-21T03:43:07.412Z", "trace_id": "tr_9f2",
  "session_id": "s_01J", "type": "tool.finished",
  "payload": { "tool": "git_status", "ok": true, "duration_ms": 84 } }
```

`seq` is global and gap-free. A client that sees a gap treats it as "I missed something" and re-syncs.

---

## 7. Data flow and where state lives

| State | Store | Lifetime | Rebuildable? |
|---|---|---|---|
| Sessions, turns, events, tasks, approvals | `oracle.db` (SQLite, WAL) | forever | no — back this up |
| Documents, chunks, vectors, symbols | `knowledge.db` (SQLite + sqlite-vec + FTS5) | until reindex | **yes** — delete freely |
| Security audit | `logs/audit/*.jsonl`, hash-chained | forever, append-only | no |
| Operational logs | `logs/**` JSONL, rotated | 14–90 days | yes |
| Large tool output | blob files, referenced by hash | with the task | no |
| Secrets | Windows Credential Manager (DPAPI) | until revoked | no |
| Models | `D:\ORACLE\models` | until deleted | yes (re-pull) |

Two database files, deliberately. `knowledge.db` is disposable — a corrupted index or a bad chunking
change must never be able to damage session history. It also makes "reindex everything" equal to
deleting one file, which is a feature. See
[ADR-0006](DECISIONS.md#adr-0006--sqlite-only-storage-two-files-sqlite-vec--fts5).

**Runtime data lives on `D:`, not `C:`.** C: has 39.8 GB free; a single 9B model plus an index would
consume an uncomfortable fraction of it. See [TECH_STACK.md](TECH_STACK.md#data-locations).

---

## 8. Degradation — what happens when a piece is missing

An agent that becomes useless when one dependency is down is a bad agent. Explicit degradation modes:

| Failure | Behaviour |
|---|---|
| Ollama down / model not pulled | Deterministic router only. Slash commands, palette, pipelines, search all still work. Banner: "reasoning offline". |
| GPU unavailable (driver change, CUDA drops Pascal) | Fall back to CPU inference automatically; warn about latency; suggest the smaller model. |
| `knowledge.db` missing or corrupt | Retrieval returns empty with a typed warning; lexical file search still works directly against the filesystem. |
| Claude/Antigravity unavailable | Delegation degrades to the **Handoff Packet** fallback: write the task to disk, tell the user, watch for the resulting diff. |
| Tool Host crash | Runtime restarts it; the in-flight step is marked `failed` and is never silently retried if it had side effects. |
| Backend down | Clients show an offline state and a reconnect countdown. The desktop shell still opens. |
| Policy file unreadable or invalid | **Fail closed.** Deny everything except read-only tools and surface loudly. |

The last row is the important one. A security control that fails open is not a security control.

---

## 9. Component inventory

```
packages/
  core/           runtime, event log, sessions, state machine, cancellation, HALT
  router/         pre-router, intent, planner, critic, prompt templates
  context/        budget manager, retrieval orchestration, redaction, renderers
  tools/          tool registry, contracts, schemas, argument validation
  policy/         rules engine, scopes, risk tiers, taint tracking, approvals
  toolhost/       separate process: execution, isolation, job objects, undo journal
  knowledge/      indexer, watchers, parsers, chunkers, embeddings, hybrid search
  memory/         working / episodic / semantic memory, project facts
  integrations/   LLMProvider impls · ExternalAgentAdapter impls · MCP server
  pipelines/      YAML schema, validator, executor
  api/            FastAPI app, WS protocol, auth, pairing, event fan-out
  storage/        migrations, repositories, blob store
apps/
  desktop/        Tauri shell (thin) + React frontend
  mobile/         PWA (shares frontend primitives, different layout)
tests/
  unit/ integration/ security/ replay/ e2e/
```

## 10. Glossary

| Term | Meaning |
|---|---|
| **Turn** | One user input and everything ORACLE does in response. |
| **Task** | A durable, cancellable unit of work; may outlive the turn that started it. |
| **Step** | One tool invocation inside a plan. |
| **Capability** | A named privilege a tool requires, e.g. `fs.write`, `proc.spawn`, `net.egress`. |
| **Scope** | A bounded region a capability may act in — a filesystem root, a project, an allowed program. |
| **Risk tier** | T0–T4; determines whether a step runs automatically, needs confirmation, or is refused. |
| **Taint** | A flag on a turn meaning "untrusted external content entered the context". Raises risk tiers. |
| **Handoff Packet** | A self-contained task description for an external agent; the vendor-neutral fallback. |
| **Egress preview** | The exact payload leaving the machine, shown before it leaves. |
| **Node** | A UI object in the orbital view: a project, task, agent, collection or process. |
