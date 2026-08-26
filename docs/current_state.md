# ORACLE — Current State

> **Snapshot for an agent picking this project up cold.** Written 2026-08-26 against source and
> against the live databases, not against the other docs. Where a doc and the code disagree, that
> disagreement is recorded in §10 rather than smoothed over.
>
> This file is a **snapshot** — overwrite it, do not append. For *what to do next* read
> [current_task.md](current_task.md); for *what was just done* read
> [current_report.md](current_report.md); for *the rules you must follow* read
> [../AGENTS.md](../AGENTS.md) first.

---

## 1. What ORACLE is

A **local-first supervisor of agents** running on one Windows machine. It takes intent, assembles
context, decides who should do the work, enforces what that worker may touch, verifies the result,
and reports with evidence. It is not a chatbot with tools, and it is deliberately not the smartest
model in its own system — intelligence is distributed:

| Concern | Handled by |
|---|---|
| Orchestration, state, scheduling, permissions, verification | deterministic Python |
| Intent, routing, short answers | local 0.8B model via Ollama |
| Plan authorship | Claude (measured winner — see OQ-20) |
| Implementation, debugging | Claude Code, in a git worktree |
| Review, research | Antigravity (`agy`), read-only |
| git / tests / search / launch | plain code, no model |

The governing idea: **the most common correct action is not to call the LLM at all.** Slash
commands, palette actions, pipelines and exact-match tool syntax bypass the model entirely. This is
what makes a sub-1B model viable as the primary interface.

---

## 2. Running it

```bash
uv run oracled
```

```bash
npm --prefix apps/desktop run dev
```

- **Backend** `oracled` → `127.0.0.1:8787` (FastAPI + uvicorn). Health at `/health`.
- **UI** → `localhost:5273` (Vite), proxies `/api` and `/health` to 8787. `.claude/launch.json`
  defines this as `oracle-ui`.
- **Tauri shell** `npm --prefix apps/desktop run tauri dev` — thin wrapper, optional. The browser is
  a first-class client (ADR-0007); nothing requires the shell.
- **Gate** `uv run python scripts/check.py` (aliased `make check`). GNU make is *not* installed on
  this machine — the Makefile delegates to the script for exactly that reason.

Data lives on **`D:\ORACLE\data`**, not in the repo. Logs on `D:\ORACLE\logs` plus repo-local
`logs/`. Policy, collections, agent registry and pipelines live in `config/` **in the repo**,
version-controlled on purpose: a human edits them, git records it.

At boot you should see `tools.registered count=33`, `pipelines.loaded count=2 problems=0`,
`rag.watch_started roots=9`, and `oracled.started schema_version=4`.

---

## 3. Where the project actually is

**Foundation (P0–P6) and the supervisor arc (P7–P9) are built. P10 is built. P11 is in progress.**

```
 P0  walking skeleton              done
 P1  local LLM + runtime           done
 P2  policy gate                   done
 P3  tools                         done
 P4  desktop UI            * MVP   done 2026-08-21
 P5  knowledge (RAG)               done — recall gate still unmet (OQ-18)
 P6  delegation, MCP, egress       done 2026-08-24
 P7  task graph & scheduler        done 2026-08-25
 P8  planner + multi-worker        done 2026-08-25
 P9  memory & context engine       done 2026-08-26 — see the caveat below
 P10 pipelines                     done 2026-08-26
 P11 execution vis & advanced UI   IN PROGRESS — T1/T3/T4 done, T5 set, T2 blocked
 P12 project state · P13 residency · P14 mobile · P15 voice · P16 tiers · P17 hardening
```

**The caveat that matters most:** the supervisor arc is *built and tested* but has **never run for
real**. `tasks` is 0 rows. `memory_facts` is 0 rows. `memory_attempts` is 0 rows. Everything P7–P9
ships has been exercised by tests and fixtures only. See §7 and §11.

### Branch state — read this before you commit

```
  phase6-integration   <- HEAD, 54 commits ahead of origin/main
  origin/main          <- stale, sits at Phase 5-era work
```

The branch name is a fossil: **all of Phase 6 through Phase 11 lives on `phase6-integration`**, not
on `main`. Do not assume `main` reflects the project. Whether this branch should be merged or
renamed is an open decision nobody has made.

---

## 4. Process and trust model

Three OS processes, three trust levels. **The privilege boundary is a process boundary, not a
function call** — this is the single most defended property in the codebase.

```
oracled            TRUST high   policy, secrets, DB handles, audit log, tokens
   |                            never executes a shell command
   |  JSON-RPC over pipe, argv lists only, pre-authorised invocations
oracle-toolhost    TRUST low    holds nothing durable, cannot read policy or secrets,
   |                            runs inside a Windows Job Object -> whole tree killable
   |  CreateProcess / file I/O
child processes    TRUST none   git, npm, pytest, claude, agy, ...
```

Ollama is a **fourth** process ORACLE does not own, treated as an untrusted network dependency
behind an adapter — it may be down, and the system must stay usable when it is.

Two rules keep the boundary honest, both enforced rather than documented: **the child resolves
nothing** (paths canonicalised and programs pinned on the parent side), and **a tool declaring
`proc.spawn` cannot run in-process** (without the Job Object there is no tree termination, and HALT
would be a lie).

**One deliberate exception:** `app.launch` runs in `oracled` and launches detached, because "stop
what you are doing" must not mean "close my editor with unsaved work". The registry enforces the
exception's exact shape (ADR-0018). `term.*` is the mirror image and stays in the toolhost — a
runaway `npm install` in a shell is precisely what HALT exists to stop.

### The policy gate

Policy is **not a layer, it is a gate** on one chokepoint — the toolhost boundary. Tiers are a
function of `(tool, resolved arguments, scope, taint)`, never of the tool alone:

| Tier | Meaning | Effect |
|---|---|---|
| T0 | no side effect, in scope | auto |
| T1 | reversible local write, in scope | auto + undo journal |
| T2 | externally visible / costly | confirm |
| T3 | destructive / wide blast radius | confirm_strong |
| T4 | never | deny, not offerable |

Taint (untrusted content entered the context) escalates by exactly one tier and never lifts T0. An
unreadable or invalid policy file **fails closed**. Approvals expire after **180 s**
([core/approvals.py:42](../src/oracle/core/approvals.py)) and expiry resolves as refused — nothing
auto-approves, ever. HALT is bound to `Ctrl+Alt+Shift+H`, deliberately awkward.

---

## 5. Subsystem inventory

Raw line counts (`src/oracle/`, ~24,700 lines across 20 packages):

| Package | Lines | State |
|---|---:|---|
| `tools/` | 4,522 | 33 tool contracts, registry, executor, undo journal |
| `rag/` | 3,439 | indexer, chunkers, tree-sitter, PDF, embeddings, hybrid search, watcher |
| `orchestration/` | 2,575 | task graph, scheduler, plan validation, replan, recovery, templates |
| `runners/` | 2,007 | tool · delegation · planning · pipeline · verify · report runners |
| `router/` | 1,627 | pre-router, intent, selection, turn pipeline |
| `integrations/` | 1,271 | adapter protocol, Claude CLI, Antigravity CLI, worktrees, delivery |
| `memory/` | 1,265 | facts, preferences, attempts, write policy, context bands |
| `api/` | 1,264 | FastAPI app, WS fan-out, REST |
| `policy/` | 1,182 | engine, model, paths, programs, apps, hash-chained audit |
| `core/` | 1,090 | runtime, event log, sessions, approvals, HALT |
| `pipelines/` | 931 | YAML loader, validation, compile-to-graph |
| `toolhost/` | 712 | separate process, Job Objects, argv-only protocol |
| `llm/` `mcp/` `handoff/` `context/` `delegation/` `logsink/` `storage/` | 3,647 | — |

### Tools — 33, intent-shaped, no general shell (ADR-0015)

```
fs.    read list stat write patch move delete
git.   status diff log branch add commit push stash undo
dev.   run_tests build lint execute
know.  search search_code read_context reindex
term.  open read write input resize close
sys.   info processes
app.   launch
```

`dev.execute` is the escape hatch for anything the pipeline DSL should not grow to express.

### Agent registry (`config/agents.yaml`)

9 roles (`planner coder debugger tester reviewer researcher summarizer verifier operator`), 3
agents:

- **claude** — planner, coder, debugger, tester, reviewer, researcher · worktree · cloud ·
  structured output · egress `api.anthropic.com`
- **antigravity** — reviewer, researcher **only**, `read_only: true`, `effort: low`. Lost the
  planner role by measurement (OQ-20: 75% valid-on-first-attempt against a 90% gate, and every hard
  failure was `--effort high` browsing the filesystem). Cannot hold `coder`: headless `agy` cannot
  write without a flag ORACLE refuses.
- **local** — summarizer, researcher · free · no egress

A plan cannot give itself an executor: the registry is versioned data a human edits.

### API surface

```
GET  /health
GET  /api/v1/status              GET  /api/v1/knowledge
GET  /api/v1/sessions            POST /api/v1/sessions
GET  /api/v1/sessions/{id}/events
GET  /api/v1/tasks               GET  /api/v1/memory   GET /api/v1/memory/attempts
POST /api/v1/mcp/tools           POST /api/v1/mcp/call
WS   /api/v1/stream?since_seq=N
```

---

## 6. The UI as it actually stands

React 19 + zustand, 13 components, ~202 tests across 18 files. **This is the least finished part of
the system, and the current phase is about exactly that.**

Mounted and working: command palette (`Ctrl+K`), confirmation center with egress preview, graph and
pipeline approval cards, delegation panel, memory view, task tree, inspector, terminal dock (xterm +
ConPTY), tool cards with citations, knowledge state.

**What is not:**

- `Stage` is still `"chat" | "events" | "memory"` toggled by two buttons. UI.md §2 asks for
  `Ctrl+1..4` across Orbit / Chat / Timeline / Tasks. **This is P11-T5, the active task.**
- `TaskTree` renders unconditionally *above* the chat log instead of in its own view.
- **`KnowledgeHealth` is imported by nothing.** Built, 11 passing tests, unreachable in the running
  app. ADR-0023 puts the graph re-layout action on it, so P11-T6 needs it mounted.
- The orbital view does not exist and is gated on OQ-14, which is gated on data (§11).
- The knowledge graph view does not exist. OQ-22 measured it and said build it, **narrower** than
  UI.md §11b describes.

Two accessibility facts worth carrying: `a11y.test.tsx` covers 11 of 13 components and deliberately
disables axe's `color-contrast` rule (happy-dom lays nothing out), so `contrast.test.ts` checks
UI.md §14 as arithmetic over tokens parsed out of `styles.css` instead — it cannot drift. `Inspector`
is the component still uncovered by the axe suite.

### Event vocabulary the UI consumes

```
session.created  session.resync    system.degraded    agent.state
turn.started     turn.finished     message.delta      message.completed
tool.started     tool.finished     approval.requested approval.resolved
task.created     task.updated      task.finished
delegate.event   ai.delegate       knowledge.state
term.opened      term.output       term.closed
pipeline.started pipeline.finished plan.descended     plan.rejected
graph.replan_exhausted
```

`seq` is global and gap-free; a client seeing a gap re-syncs. Clients resume with `since_seq=<n>`;
the backlog window is 10,000 events and per-connection queues are bounded at 1,000 (overflow closes
the socket rather than silently dropping).

---

## 7. Data — the real numbers

**`D:\ORACLE\data\oracle.db`** — 0.2 MB, schema v4, WAL. Back this up; it is not rebuildable.

| Table | Rows |
|---|---:|
| `events` | 403 |
| `sessions` | 14 |
| `tasks` | **0** |
| `memory_facts` | **0** |
| `memory_attempts` | **0** |

**`D:\ORACLE\data\knowledge.db`** — 141.7 MB, disposable. Delete it to force a full reindex.

| | |
|---|---:|
| `documents` | 1,422 |
| `chunks` | 14,823 (FTS5 mirror in `chunks_fts`) |
| chunk vectors (`vec0`) | 13,895 |
| `links` (wikilinks) | 1,180 |

`ORACLE` is itself an indexed project in the `projects` collection and the watcher is live, so
**this repository is part of the corpus retrieval is measured against.** Writing this file took
`documents` from 1,421 to 1,422 within seconds of saving it, which also means the corpus fingerprint
cited by the OQ-22 measurement data (`e342f8a55a6ce17d`) no longer matches. Any recall number
re-measured from here is measured against a different corpus than OQ-18 and OQ-22 saw. Committing
documentation is not a neutral act in this project.

Two databases, deliberately: a corrupted index must never be able to damage session history, and
"reindex everything" equals deleting one file (ADR-0006).

Embeddings are `bge-m3` at 1024d on **CPU**; the GPU (4 GB Pascal, GTX 1050 Ti) is reserved for the
router model `qwen3.5:0.8b` at 16k context — the largest Qwen3.5 that stays 100% GPU-resident
(OQ-01, 93.3% intent accuracy, 100% single-tool selection).

Security audit is a hash-chained JSONL in `logs/audit/`, append-only, verified by
`scripts/audit.py verify`.

---

## 8. Tests and the gate

- **1,071 Python tests** across 65 files. `testpaths = ["tests"]`, `filterwarnings = ["error"]`,
  `asyncio_mode = "auto"`. No test may require Ollama to be running.
- **~202 UI tests** across 18 files (vitest + happy-dom + axe-core).
- **`tests/security/` — 25 files — is part of the merge gate and is not optional.** It covers path
  traversal, shell absence, injection, plan injection, replan authority, pipeline authority,
  orchestration boundary, egress gate, MCP tokens, memory writes, and the audit chain.

`scripts/check.py` runs **seven** steps in order:

```
ruff format --check -> ruff check -> mypy -> tsc -> pytest (minus security) -> security -> vitest
```

**Gate status: GREEN**, run 2026-08-26 at HEAD on `phase6-integration` — all seven steps, 344 s
(pytest 157 s, security 185 s, vitest 33 s). `oracle-selfcheck` runs most but not all of it
(§10.2 explains what it misses).

---

## 9. Open questions that still bite

Full detail in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md). The ones that affect what you can build:

| # | Question | Status |
|---|---|---|
| OQ-14 | Does the orbital view earn its place? | **open — blocked on data, not effort.** Phase 11 go/no-go |
| OQ-18 | Can Russian questions reach an English corpus? | measured — **78.9% against an 80% gate**, one fixture short; gate NOT moved |
| OQ-22 | Does the knowledge graph hold its budgets at corpus scale? | 3 of 4 answered — build it, narrower; canvas-vs-SVG needs a real window |
| OQ-23 | Does a failure-carrying prompt produce a *different* plan? | open, blocks nothing |
| OQ-15 | Routed-turn latency under ~1.5 s? | open, quality not blocker |
| OQ-12/13 | Taint escalation and approval rate tolerable in daily use? | `ASSUMPTION` — needs real use |
| OQ-03 | How long will Pascal keep GPU acceleration? | `UNKNOWN`, monitoring |

OQ-22's most portable findings: the semantic-edge toggle UI.md called optional is, on this corpus,
**the difference between a graph and a scatter of dots** — explicit wikilinks touch 157 of 1,420
documents and 156 of those are in one Obsidian vault, because `links` is populated only by the
Obsidian chunker. Recommended default `k=4, thr=0.85`. And §11b's promise to show "bridges between a
vault and a project" was **struck from the spec** — across every k and every threshold the graph
holds exactly **one** such edge, because the notes are ML prose and the projects are TypeScript,
Rust and Python. The embedder is right that they are not about the same things.

Also from OQ-22, and larger than the measurement: layout positions were seeded by **array index**,
so reindexing after adding one file moved every node. ADR-0013's entire argument is that a person
learns where things are. Seeding from a hash of the node's own id fixed it. It would have shipped as
"the layout is unstable, add more iterations".

---

## 10. Known defects and doc-vs-code discrepancies

Verified by reading on 2026-08-26. These are the things that will waste your time if nobody tells
you.

1. **`document_vectors` is described as a shipped table and is not one.**
   [current_report.md](current_report.md) says *"`document_vectors` is a required table, written by
   `store.put()`"*. `rag/store.py` creates `meta`, `documents`, `chunks`, `chunk_vectors`,
   `chunks_fts` and `links` — nothing else, and the live DB confirms it. `document_vectors` is a
   **function in `scripts/measure_graph.py`** backed by an `.npz` cache. The measurement's
   conclusion — that per-document vectors must be persisted or incremental indexing spends ~52 s on
   I/O against a `< 5 s` budget — stands as a **requirement not yet built**, not as a description of
   the code.

2. **`oracle-selfcheck` is not the full gate, though its header implies it.** The pipeline runs 6
   steps; `scripts/check.py` runs 7. The pipeline **omits `tsc` and `vitest` entirely** — the whole
   TypeScript half, ~202 tests — and adds an `audit` step the gate does not have. A green
   `oracle-selfcheck` does **not** mean `make check` is green.

3. **`oracle-selfcheck` runs the security suite twice.** Its `tests` step is `dev.run_tests`, which
   derives `uv run pytest --junit-xml=…` with no ignore, and `testpaths = ["tests"]` includes
   `tests/security/`. The separate `security` step then runs it again. `check.py` avoids this with
   `--ignore=tests/security`; the pipeline does not.

4. **The `chunker_version` guard does not fire on the indexes it was written for.** `bind()` raises
   only when a key is *present and different*, then writes the current value in — so an index built
   before the key existed passes and is stamped by whatever binds it first. Already happened to the
   live index: **57% of its 14,586 rows exceed the shipped 1200-char cap, longest 4,055** — the v1
   signature. Undecided whether a missing version should refuse; the database wants a reindex either
   way.

5. **`TaskTree.test.tsx` is green on a fixture the app cannot produce.** `store.ts` never populates
   `dependsOn`, so `TaskTree`'s `after {deps}` line is dead in the running app. Fixtures should be
   recorded from the wire, not hand-written.

6. **`make perf` and `make eval` are documented in TESTING.md §8 and defined nowhere.** Phase 11's
   acceptance depends on budgets being assertable, so add the targets or correct the doc.

7. **A merge-gate test fails under CPU starvation.** `test_a_long_burst_arrives_complete` lost 189
   lines of a ConPTY burst twice on 2026-08-26 under full load, including at `HEAD`. Idle it passes
   in 6 s. Unresolved: whether the reader drops output under starvation or the deadline is too tight
   — different repairs.

8. **A correction typed while a graph runs is refused**, because "never mid-plan" is implemented
   literally. The fix, when somebody hits it, is a queue — not an exception.

9. **Palette results are not discoverable to assistive tech.** The rows are `<li role="option">`
   with an `onClick`; the query input has no `role="combobox"`, `aria-controls` or
   `aria-activedescendant`. Arrow keys and Enter work for a sighted keyboard user, but the selection
   change is not announced and the rows do not surface in an accessibility-tree query at all.
   Relevant to P11's *"the list view offers every graph action"* criterion. `TO VERIFY` whether this
   was a deliberate deferral.

10. **A dead collection root once took the whole watcher down.** `watchfiles` refuses to start on a
    path that is neither file nor directory, so one absent root disabled live re-indexing for
    **every** collection with a single warning at boot and no other symptom. Fixed by removing the
    root; the fragility is structural and will recur if a root is deleted from disk.

11. **Scheduled pipeline runs are unenforced.** PIPELINES.md §5 says "nothing above T1 unattended",
    which is not enforced because nothing schedules anything. The hook exists and is
    `check(..., max_tier=Tier.T1)`. `TO VERIFY` when something does.

---

## 11. The one thing blocking Phase 11

**`tasks` is empty. 0 rows, 0 roots, 0 superseded.** The event log holds ~400 events over 5 days.

The execution tree, the orbit, the timeline and the agent queue all render supervisor activity that
**has never happened outside a test**. ROADMAP defends scheduling OQ-14 late by citing "months of
real event data"; there are none. Building the orbit in order to judge it would be judging it
against a picture we drew ourselves.

Two ways to produce real data, both **T2-or-above and therefore a person's to run, not an agent's**:

- **`oracle-selfcheck`** — local, no egress, six steps, one approval card, ~5 minutes. Produces a
  real six-task graph with real evidence, timings and cost. This is the cheap unblock.
- **The Phase 8 scenario** — richer, and the only thing that exercises `supersedes` lineage, but it
  costs tokens and egress.

P11-T3 and T4 were built against fixtures and are done. Their acceptance criteria cannot be *judged*
without one real run.

---

## 12. State of the running system, 2026-08-26

- `oracled` **up** on 127.0.0.1:8787 — 33 tools, toolhost prewarmed, pipelines `asterim-check` and
  `oracle-selfcheck` loaded with 0 problems, graph recovery clean (0 interrupted, 0 unstarted).
- UI **up** on localhost:5273, connected over WebSocket.
- **Ollama is down** — `system.degraded`, `"Ollama is not reachable"`. Chat routing through the
  model does not work. Pipelines, slash commands, palette and search all still do: the pre-router
  matches a registered pipeline name as a whole word *before* any model call
  ([router/prerouter.py:122](../src/oracle/router/prerouter.py), reached from
  [router/pipeline.py:264](../src/oracle/router/pipeline.py)). This is the ARCHITECTURE.md §8
  degradation path working as designed, not a fault.
- `oracle-selfcheck` is **staged but not fired** — palette open, pipeline row selected. Left for a
  human because the approval card expires in 180 s and firing it unattended would write a *refused*
  run into the very table the run exists to populate.

---

## 13. If you are the next agent

1. Read [../AGENTS.md](../AGENTS.md). It is the contract, and its hard rules are enforced by
   `tests/security/`, not by good intentions.
2. Read [current_task.md](current_task.md) — that is your assignment. If it says design only,
   produce design; do not scaffold the application.
3. Check [DECISIONS.md](DECISIONS.md) before choosing any technology. 23 ADRs; most choices are made
   and have recorded reasons. Disagree by writing a superseding ADR, never by drifting.
4. Check [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md). If your task rests on an `EXPERIMENT NEEDED`, run
   the experiment and record the result before building on the assumption.
5. Use the uncertainty markers literally — `UNKNOWN`, `ASSUMPTION`, `TO VERIFY`,
   `EXPERIMENT NEEDED`. They are grepped. Do not silently resolve one.
6. On finishing: overwrite [current_report.md](current_report.md), update
   [current_task.md](current_task.md), write a dev log to `logs/development/YYYY-MM-DD-<slug>.md`
   for any non-obvious investigation — **dead ends are the most valuable thing you can record** —
   then commit and push.

The project is design-first and measurement-first. The recurring pattern in its history is that a
number contradicted a design document and the document was corrected **in place**, with the number
attached and the reason stated. Keep doing that: a spec that disagrees with the code is worse than
no spec, because the next reader cannot tell which half is wrong.
