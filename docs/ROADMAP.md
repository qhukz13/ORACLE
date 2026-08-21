# ORACLE — Roadmap

> Sequenced so that **every phase ends with something usable**, and so that no phase can execute a
> side effect before the machinery that governs side effects exists.
>
> Scope labels: **MVP** · **Post-MVP** · **Experimental** · **Future**.

## Phase map

```
 MVP ────────────────────────────────────────────────┐
  P0  Foundation & walking skeleton                  │
  P1  Local LLM + agent runtime                      │
  P2  Tool system + policy gate      ← security first│
  P3  PC & dev control tools                         │
  P4  Desktop UI v1                  ★ MVP MILESTONE │
 ────────────────────────────────────────────────────┘
 Post-MVP
  P5  Project knowledge (RAG)
  P6  External agent integration (Claude, Antigravity)
  P7  Pipelines
  P8  Mobile
  P9  Advanced UI (orbit, timeline, global search)
 Experimental
  P10 Voice
 Continuous
  P11 Hardening
```

**Why this order.** Two constraints fix it almost completely: (1) nothing may perform a side effect
before the policy gate exists, which pins P2 ahead of P3; (2) the MVP must be something I use daily,
because a personal tool that isn't used daily is never finished — which pins a real UI at P4 rather
than at P9. Knowledge and delegation are the *interesting* parts and are therefore the easiest to
start with and the easiest to never finish; they come after there is a working product to attach them
to.

### The MVP, stated once

> ORACLE runs on my PC, holds a conversation with a local model, executes a guarded set of file, git
> and dev commands against my real projects, shows me what it's doing in a desktop UI with a working
> terminal, asks before anything risky, and logs everything.

Anything not required by that sentence is not in the MVP. This paragraph is the scope-creep test.

---

## Phase 0 — Foundation & walking skeleton  **[MVP]**

**Objective.** An end-to-end path from a UI keystroke to a backend event and back, with persistence
and logging — carrying no intelligence at all.

**Why it exists.** Every later phase plugs into this seam. Building it first means integration risk
is paid on day one, when it is cheap, instead of at P4 when it is not. It also establishes the event
log, which everything else assumes.

**Depends on.** Nothing.

**Architecture touched.** L2 API/Event, L3 Runtime (skeleton), L9 Storage.

**Components.** `packages/api`, `packages/core` (event log, sessions), `packages/storage`,
`apps/desktop` (Tauri shell + minimal React), the logging subsystem.

**Tasks.**
1. `git init`; repo layout per [ARCHITECTURE.md §9](ARCHITECTURE.md#9-component-inventory); `uv` project on Python 3.12.
2. `oracled`: FastAPI + uvicorn on loopback; `/health`, `/api/v1/status`.
3. WS endpoint with the versioned envelope; global monotonic `seq`; `since_seq` replay.
4. SQLite bootstrap: `oracle.db` + migration runner; `sessions`, `events` tables.
5. Event log: append, fan-out, resume; a synthetic `echo` "agent" that emits realistic events.
6. Logging: JSONL sink, rotation, redaction filter, `trace_id` propagation ([LOGGING.md](LOGGING.md)).
7. Tauri shell wrapping a minimal React app: connect, send a message, render the event stream.
8. `make check` → ruff + mypy + pytest + vitest.

**Deliverables.** A running desktop app that echoes through the real backend and persists a
replayable event log.

**Acceptance criteria.**
- Send a message from the desktop app → an event is persisted → it renders. Reload → history intact.
- Kill the backend mid-session → the UI shows offline, reconnects, and catches up via `since_seq`
  with **no gaps and no duplicates**.
- Two clients (Tauri + browser tab) see identical event streams.
- Logs contain `trace_id` end to end; a planted fake secret is redacted at the sink.

**Testing.** Unit: event-log sequencing, resume-from-seq. Integration: WS reconnect under forced
disconnect. E2E: Playwright happy path.

**Risks.** Tauri↔sidecar lifecycle on Windows (orphaned backend on force-quit) → own the process from
Rust with a Job Object and verify explicitly. Port conflicts → pick a port, persist it, expose it to
the frontend.

**Definition of done.** All acceptance criteria pass; `make check` is green; `docs/current_report.md`
updated.

---

## Phase 1 — Local LLM + agent runtime  **[MVP]**  ·  **P1-T1 DONE 2026-08-21**

**Objective.** ORACLE answers questions with a local model, with a real turn pipeline, state machine,
streaming, and cancellation. **No tools, no side effects.**

**Why it exists.** The model choice is the project's biggest unknown and its hardest constraint
(3.5 GB VRAM). Settling it early — with measurements, not opinions — prevents an architecture built
on a model that turns out not to fit.

**Depends on.** P0.

**Architecture touched.** L3 Runtime, L4 Router, L5 Context (basic), L8 LLMProvider.

**Tasks.**
1. ~~Benchmark placement and latency~~ — **DONE 2026-08-21**, ahead of the phase.
   Router is **`qwen3.5:0.8b` @ 16k, 100% GPU, `think:false`**
   ([results](../logs/development/2026-08-21-oq01-router-benchmark.md)).
   **Still owed in this phase:** the 30-case intent + tool-selection fixture set that answers the
   *accuracy* half of [OQ-01](OPEN_QUESTIONS.md#oq-01). Build it **before** the router logic, not after.
2. `LLMProvider` protocol + `OllamaProvider` + `FakeProvider` (replay).
3. Structured output: JSON Schema → validate → one repair → deterministic fallback; track the failure rate.
4. Turn pipeline stages 0–3 and 8; the state machine; streaming deltas over WS.
5. Pre-router: slash commands + a first palette action set.
6. Context Assembler v1: priority bands, real tokenizer counting, budget enforcement.
7. Cancellation tokens through the whole loop; per-turn timeout.
8. Ollama supervision: detect not-running, model-not-pulled, and degrade with a clear banner.

**Deliverables.** Streaming local chat; intent classification; a measured, documented model choice.

**Acceptance criteria.** (latency targets now grounded in the measured TTFT curve, not guessed)
- ~~`route` TTFT < 900 ms p50~~ — **MISSED, and the gate was mis-derived.** It came from OQ-01's
  prompt-eval numbers alone and never budgeted for generation or Ollama's ~600 ms fixed per-request
  overhead. Measured routed turn: **p50 1542 ms**. Restated target: a routed turn under ~1.5 s, and
  **>50% of turns resolved by the pre-router at ~5 ms** ([OQ-15](OPEN_QUESTIONS.md#oq-15)).
- ✅ Structured-output failure rate **< 2%** — measured **0.00%** over the fixture set.
- ✅ Intent classification **≥ 85%** — measured **93.3%**; clarify behaviour **100%**.
- Cancel mid-stream stops token generation within 500 ms.
- With Ollama stopped, slash commands still work and the UI says why chat doesn't.
- Context never exceeds the **per-call-type** budget — asserted, not hoped.
- The router model stays resident: no reload between consecutive turns (measured load cost is
  7–14 s warm, 51 s cold, so a reload is user-visible and unacceptable).

**Testing.** `FakeProvider` replay tests for the whole pipeline (deterministic). Property tests on
budget allocation. A recorded fixture suite for intent accuracy that reruns on any prompt change.

**Risks.**
- ~~*The 2b model doesn't fit.*~~ **Confirmed — it doesn't** (36%/64% CPU/GPU at every context length).
  Already handled: 0.8b is the router.
- ***0.8b is too weak on the accuracy fixtures.*** **The main risk of this phase.** Nothing smaller is
  worth using and nothing larger fits this GPU. Mitigations in order: try
  `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` and a text-only build ([OQ-10](OPEN_QUESTIONS.md#oq-10))
  to pull `2b` onto the card; accept `2b` hybrid (~20 tok/s, ~3.4 s TTFT); or shift load to the
  deterministic pre-router. **Decide with fixture data, not impressions.**
- *Ollama drops Pascal* ([OQ-03](OPEN_QUESTIONS.md)) → CPU fallback path must be tested in this phase,
  not discovered later.
- *Prompt fiddling becomes the whole phase.* → the fixture suite is the stop condition; when the
  numbers pass, stop.

**Definition of done.** Acceptance met; benchmark written up; ADR-0004 updated with the real numbers.

---

## Phase 2 — Tool system + policy gate  **[MVP]**

> **P2-T1 mostly DONE 2026-08-21** — gate built and proven with 103 security tests;
> process isolation deliberately deferred to Phase 3 (see the note at the end of this phase).

**Objective.** The full capability/policy/execution machinery, proven with **read-only tools only**.

**Why it exists.** This is the phase that makes ORACLE safe to keep building. Every security control
lands here, before a single write tool exists. Retrofitting a policy engine after tools exist is how
these projects end up as unrestricted shell wrappers.

**Depends on.** P1.

**Architecture touched.** L6 Capability, POLICY GATE, L7 Tool Host.

**Tasks.**
1. Tool registry + contract decorator; startup validation; JSON Schema generation.
2. Resolved types: `ScopedPath`, `ProjectRef`, `ProgramRef`.
3. **Path canonicaliser** with the full Windows algorithm ([SECURITY.md §4](SECURITY.md#4-path-safety-windows-specific)),
   plus the `realpath`-vs-junction experiment ([OQ-04](OPEN_QUESTIONS.md)).
4. Policy engine: `config/policy.yaml`, scopes, capabilities, tiers, deny-always, fail-closed loading.
5. `oracle-toolhost` as a **separate process**: JSON-RPC over pipe, Job Object, timeouts, argv-only.
   **DEFERRED to Phase 3 — see the note below.**
6. Approvals: bound `arg_hash`, expiry, single-use, re-check before execution.
7. Taint tracking: provenance on context items; escalation rules.
8. Hash-chained audit log + `oracle audit verify`.
9. HALT: API → runtime → job-object termination → deny-all → manual resume.
10. Read-only tools: `fs.read`, `fs.list`, `sys.info`, `sys.processes`, `oracle.*`.
11. **`tests/security/` red-team suite** — traversal, symlinks, junctions, ADS, 8.3 names, approval
    replay, mutated arguments, injected instructions in fixture files.

**Deliverables.** A policy-gated tool system; an isolated executor; an audit log; a security suite.

**Acceptance criteria.**
- Every red-team case is **denied**, and each denial names the rule that fired.
- A corrupt/absent `policy.yaml` yields read-only mode, loudly — never open access.
- Killing the toolhost mid-call leaves the runtime healthy; the step is marked `failed`.
- HALT terminates a `ping -t` process tree within 2 s, from a cold hotkey press.
- Tampering with one audit line makes `audit verify` fail.
- An approval for args A cannot execute args B.
- `grep -r "shell=True"` returns nothing; a lint rule enforces it.

**Testing.** Hypothesis property tests on the canonicaliser (generated adversarial paths). The
security suite is a **merge gate** from here on.

**Risks.** Windows path edge cases are genuinely hard → property testing plus a real fixture tree with
actual symlinks and junctions, not mocks. IPC overhead → measure; budget < 50 ms per call.

**Note on process isolation (ADR-0003).** The toolhost is specified but not yet a separate process;
the executor runs in-process. ADR-0003's three justifications are (a) a crashing tool must not take
down the agent, (b) a tool must not be able to read `ANTHROPIC_API_KEY`, (c) killing a thread does not
kill `npm install`'s grandchildren. **None of them bite for Phase 2's read-only file tools, and all
three bite hard in Phase 3** when `dev.execute`, `git` and `npm` start spawning real process trees.
Building it at the point where it is load-bearing is deliberate sequencing, not an omission — but it
is a **hard prerequisite for the first Phase 3 tool that spawns a process**, and the acceptance
criterion "killing the toolhost mid-call leaves the runtime healthy" moves with it.

**Definition of done.** Security suite green and wired into `scripts/check.py`; audit verification
working; ADR-0005 confirmed against the implementation; ADR-0003 confirmed at the start of Phase 3.

---

## Phase 3 — PC & dev control tools  **[MVP]**  ·  **P3-T1 DONE 2026-08-21**

**Objective.** The tools that make ORACLE useful: git, tests, files, apps, terminal.

**Why it exists.** First real value. This is where the daily-use habit forms — or doesn't.

**Depends on.** P2 (hard: no write tool ships before the gate).

**Tasks.** All done — plus process isolation, which P2 deferred here.
0. ~~`oracle-toolhost` as a real separate process, with supervision and a Job Object.~~ **DONE**
1. ~~Undo journal + trash; `fs.write`/`fs.patch`/`fs.move`/`fs.delete`.~~ **DONE**
2. ~~`git.*`: status, diff, log, add, commit, branch, stash, push (T2).~~ **DONE** — plus the
   hidden `git.undo` the journal dispatches, because reversing a commit needs a process and the
   parent must not spawn one.
3. ~~`dev.run_tests` with structured results, `dev.build`, `dev.lint`, `dev.execute`.~~ **DONE**.
   `dev.run_script` was dropped: detection already runs only what `package.json` declares.
4. ~~`app.launch` via `config/apps.yaml`; `sys.*`.~~ **DONE** — and it is the one tool that runs
   in the parent ([ADR-0018](DECISIONS.md#adr-0018--a-launched-application-is-not-a-tool-call)).
5. ~~`term.*` on ConPTY; `term.write` confirmed every time.~~ **DONE** ([OQ-09](OPEN_QUESTIONS.md#oq-09)).
6. ~~Project registry: detect type, test/build/lint commands, read `AGENTS.md`/`CLAUDE.md`.~~ **DONE**
7. ~~Confirmation flow end to end, including `dry_run` previews for T3.~~ **DONE** — the
   `approval.requested` / `approval.resolved` round trip, and a dry run that performs nothing.
8. ~~Tool selection in the router.~~ **DONE**, and measured: 100% on 18 cases.

**Deliverables.** A tool set that handles a real morning's work on Asterim.

**Acceptance criteria.** All verified live, not inferred from unit tests.
- [x] "commit my changes in Asterim with message X" works end to end and is undoable —
      routed through the real 0.8B model to `git.commit`; undo returns HEAD and leaves the work staged.
- [x] "run the Asterim tests" returns structured pass/fail counts — `1 passed, 1 failed`, `junit-xml`.
- [x] `git.push` prompts; approving executes exactly the previewed argv — and the bare remote received it.
- [x] A recursive delete shows a real file list from `dry_run` before asking — and a dry run no longer
      needs the approval it exists to inform, which was circular.
- [x] The terminal streams a long burst without dropping bytes or blocking the event loop —
      **2000/2000 lines, 204,090 bytes, loop ticks p50 13.5 ms.** This one failed first and found two
      real bugs; see `logs/development/2026-08-21-terminal-loses-output.md`.
- [x] Project detection correctly classifies all seven projects in `C:\Projects` (eight directories,
      including an empty one that correctly reports `unknown`).
- [x] A tool whose program is not on the allowlist is refused, naming the rule.
- [x] `grep -r "shell=True"` returns nothing; lint enforces it, and so does a security test.

**Testing.** 360 tests, 1 skipped. Integration tests against a real git repo, a real ConPTY and a
real toolhost. A soak of 100 tool calls leaving zero orphaned processes. Two measurement scripts that
need Ollama and are therefore deliberately not tests: `eval_intent.py`, `eval_selection.py`.

**Risks.** Test-runner output parsing is fragile → prefer machine-readable output (`--json`,
`--junit-xml`) and treat scraping as a fallback. Windows PTY quirks → budget real time for
`pywinpty`; ConPTY resize and encoding are the usual traps.

**Definition of done.** All acceptance criteria; every new tool has policy rules and a security test.

**What actually got built.** 27 tools (26 offerable), the program allowlist, the app catalogue, the
approval round trip, and tool selection in the router. Four development logs, three of them about
things that were nearly ticked off without being measured.

**Deviations from the plan, and why.**
- **`push` and `delete` shipped in MVP**, having been deferred. The phase that makes a commit
  meaningful is the same one that makes pushing it meaningful; both arrived with their tiers and
  their security tests, which was the real requirement.
- **`dev.run_script` and `fs.open_in_os` were dropped.** The first is subsumed by project detection;
  the second is `ShellExecute` by another name and cannot be a promise about what happens.
- **One ADR came out of implementation** ([ADR-0018](DECISIONS.md#adr-0018--a-launched-application-is-not-a-tool-call)):
  a launched application cannot live inside the Job Object, and that had to be argued rather than
  assumed.

---

## Phase 4 — Desktop UI v1  ★ **MVP MILESTONE**

**Objective.** The interface I actually use: chat, tasks, terminal, approvals, command palette.

**Why it exists.** The MVP is complete here. Everything before this is infrastructure; this is the
product.

**Depends on.** P3.

**Scope discipline.** **No orbital visualisation in this phase.** It is P9. Building the decorative
centrepiece before the functional shell is the classic way this kind of project dies at 80%.

**Tasks.**
1. App shell: command bar, left sidebar, center stage, right inspector, bottom dock ([UI.md](UI.md)).
2. Chat view with streaming, tool-call cards, citations, and errors as typed cards.
3. **Command palette** (`Ctrl+K`) — feeds the pre-router; the fastest path to any action.
4. Confirmation Center: approvals with the real preview, keyboard-driven, with a mis-click guard.
5. Terminal dock: xterm.js, four states, stdout/stderr distinction, search.
6. Task list + Task Inspector (status, duration, tools used, files changed, logs, result).
7. Design tokens: colour/status semantics, `prefers-reduced-motion`, focus rings, full keyboard nav.
8. States: loading skeletons, empty states, offline banner, degraded (LLM down) banner.

**Deliverables.** A desktop application usable as a daily driver.

**Acceptance criteria.**
- Every MVP action is reachable **without a mouse**.
- An approval can be read and decided in **under 5 s** — the preview shows the actual command.
- The terminal handles 10k lines of output without frame drops.
- With the backend down, the app opens, explains, and reconnects on its own.
- Full keyboard navigation with visible focus; `prefers-reduced-motion` honoured.
- Colour is never the only carrier of meaning (icon + label always accompany status colour).

**Testing.** Playwright E2E for the core journeys; visual regression on the shell; an axe
accessibility pass with zero criticals.

**Risks.** UI polish is infinitely expandable → timebox; the acceptance list above is the definition,
not "it feels good". WebView2 rendering differences → test in WebView2, not only in Chrome.

**Definition of done.** ★ **I use ORACLE for a full working day without opening a terminal manually.**
That is the real acceptance test for the MVP.

---

## Phase 5 — Project knowledge (RAG)  **[Post-MVP]**

**Objective.** ORACLE understands my projects and notes: hybrid search, incremental indexing,
attributed retrieval.

**Why it exists.** It is what turns "an agent with tools" into "an agent that knows my work", and it
is the prerequisite for context assembly good enough to delegate well (P6).

**Depends on.** P4.

**Tasks.**
1. `knowledge.db`: sqlite-vec + FTS5 schema ([DATABASE.md](DATABASE.md)).
2. **Collection registry** — explicit opt-in per source. Never "index Documents": that folder
   contains game saves and Paradox Interactive data. See [RAG.md](RAG.md#2-what-gets-indexed).
3. Parsers: tree-sitter (code), heading-aware Markdown with Obsidian wikilinks, `pypdfium2` (PDF).
4. Chunking strategies per type; embeddings via ONNX on CPU ([OQ-02](OPEN_QUESTIONS.md) first).
5. Hybrid retrieval: dense + BM25 + RRF; metadata pre-filtering by project/collection.
6. Incremental indexing: content hash + mtime; `watchfiles` with debounce; respect `.gitignore` and `.oracleignore`.
7. `know.*` tools; citations rendered in the UI.
8. Index health view: what's indexed, when, how big, what failed.

**Deliverables.** Working project- and note-aware retrieval with source attribution.

**Acceptance criteria.**
- Full index of all projects + vaults completes in **< 10 min** on this CPU; incremental update of a
  changed file in **< 5 s**.
- Retrieval p95 **< 400 ms** over the full corpus.
- On a 20-question fixture set, the correct source appears in the top 5 **≥ 80%** of the time.
- Deleting `knowledge.db` and reindexing reproduces equivalent results — the index is truly disposable.
- `node_modules`, `target/`, `.git/objects`, binaries and media are never indexed. Asserted.
- Every retrieved chunk carries a real, clickable source.

**Testing.** Golden retrieval fixtures (query → expected source). Indexer idempotency: index twice,
identical state. Injection fixtures: a note containing "ignore previous instructions" must set taint
and must not change behaviour.

**Risks.** Embedding quality on mixed RU/EN → resolve via [OQ-02](OPEN_QUESTIONS.md) before building
on it. Watcher storms during a `npm install` → debounce plus exclusion rules. Index bloat → size
budget with an alert.

**Definition of done.** Acceptance met; the fixture suite is a merge gate; indexing strategy documented.

---

## Phase 6 — External agent integration  **[Post-MVP]**

**Objective.** Delegate real coding work to Claude Code, collect results verifiably, and report.

**Why it exists.** The headline capability. It only works if P5 gave it good context and P2 gave it a
safe egress path — which is exactly why it is sixth and not second.

**Depends on.** P5.

**Tasks.**
1. `ExternalAgentAdapter` protocol; `ClaudeCodeAdapter` using
   `claude -p --bare --output-format stream-json` ([INTEGRATIONS.md](INTEGRATIONS.md)).
2. **Handoff Packet** builder: task, constraints, acceptance criteria, curated file set, prior attempts.
3. **Egress preview** UI — exact payload, redactions visible, approve/edit/cancel.
4. Git-worktree isolation; verifiable result collection by diff + tests.
5. Progress streaming: map the adapter's events onto ORACLE's event types.
6. ORACLE's **MCP server**, so the delegated agent calls back into guarded tools instead of raw shell.
7. `AntigravityAdapter` — **only after** [OQ-05](OPEN_QUESTIONS.md#oq-05) resolves the non-TTY stdout question.
8. The vendor-neutral **fallback**: write the packet to disk, watch for the diff.

**Deliverables.** End-to-end delegation with verified results.

**Acceptance criteria.**
- The reference scenario ("why is Asterim auth broken") runs start to finish and produces a diff plus
  a test result.
- Nothing leaves the machine without an approved egress preview. Asserted by a test that fails if any
  egress path skips the gate.
- A planted secret in a candidate context file is redacted before the preview renders.
- Cancelling a delegation kills the child process tree and leaves the worktree clean.
- If the CLI is missing, the fallback packet path engages automatically with a clear explanation.

**Testing.** Adapter contract tests against a **stub CLI** that emits recorded stream-json — no
network, no cost, deterministic. One live smoke test, run manually.

**Risks.** *Vendor CLI surfaces change under us* → pin the contract in INTEGRATIONS.md, test against
recorded fixtures, and treat the fallback as a first-class path rather than an afterthought.
*Antigravity's non-TTY bug* ([OQ-05](OPEN_QUESTIONS.md)) → do not build on it until verified; the
adapter is optional by design.

**Definition of done.** Claude adapter in daily use; fallback proven by disabling the CLI; Antigravity
either working or explicitly documented as blocked with evidence.

---

## Phase 7 — Pipelines  **[Post-MVP]**

**Objective.** Declarative, repeatable local workflows over registered tools.

**Depends on.** P3 (tools) and P6 (so a pipeline step can delegate).

**Scope guard.** Linear steps with conditions, timeouts, retries and artifacts. **Not** a CI system:
no matrix builds, no remote runners, no caching layer, no container-as-step beyond `dev.docker` calls.

**Tasks.** YAML schema + validator; the executor (steps → tool invocations through the same policy
gate); per-step logs and artifacts; discovery from `.oracle/pipelines/*.yaml`; a run view in the UI;
tier inheritance (a pipeline's tier is the max of its steps, computed at validation).

**Acceptance criteria.**
- The `asterim-check` pipeline (git status → backend tests → frontend tests → build → report) runs
  end to end and reports per-step results.
- A failing step honours its `on_failure` policy.
- A pipeline containing a T2 step asks **once, up front**, not mid-run.
- Cancelling a pipeline stops the current step and marks the rest `skipped`.
- An invalid pipeline fails validation with a line number, before anything executes.

**Risks.** DSL creep → if a pipeline needs branching and variables, it wants to be a script; say no
and keep the DSL small.

**Definition of done.** Two real pipelines in daily use.

---

## Phase 8 — Mobile  **[Post-MVP]**

**Objective.** Control and observe ORACLE from my phone on the LAN, safely.

**Depends on.** P4 (UI primitives), P2 (approvals model).

**Tasks.** PWA served by the backend; TLS with a stable self-signed cert; QR pairing with SPKI
pinning; device tokens (Argon2id) and per-device capability profiles; mDNS discovery; reconnect via
`since_seq`; mobile layouts for chat, tasks, approvals, logs, system; remote HALT; device management
and revocation UI.

**Acceptance criteria.**
- Pairing takes **< 30 s** from QR scan to connected.
- Approving a T2 action from the phone works; **T3 is refused on mobile** with an explanation.
- Killing Wi-Fi for 60 s and returning resumes with no lost or duplicated events.
- An unpaired device on the same LAN gets nothing (verified by an actual attempt from another device).
- A changed server certificate causes the client to refuse to connect.
- Remote HALT works from a locked phone in under 5 s.

**Risks.** Self-signed TLS and service workers: browsers require a secure context, and an untrusted
cert may block PWA install and Web Push ([OQ-06](OPEN_QUESTIONS.md)). → v1 uses in-app WS
notifications only; push is deferred until the cert question is settled.

**Definition of done.** Used from the phone for a week without falling back to the desktop for
approvals.

---

## Phase 9 — Advanced UI  **[Post-MVP]**

**Objective.** The orbital core view, activity timeline, agent queue, global search, notifications,
system monitor.

**Why it is late.** These are amplifiers, not foundations. Built at P4 they would be guesses about
what information matters; built here they are informed by months of real event data.

**Tasks.** Orbital core with deterministic polar layout and real state semantics; node interaction →
inspector; activity timeline over the event log; agent queue (now/next/waiting/done); global search
across projects, files, notes, tasks, logs and git history; notification system; compact system
monitor.

**Acceptance criteria.**
- The orbit conveys **information**: state, category, recency, activity — verified by covering the
  labels and still being able to answer "what is ORACLE doing?".
- Node positions are **stable across sessions** (deterministic layout, no physics jitter).
- With `prefers-reduced-motion`, all orbital animation stops and the view stays fully usable.
- A screen-reader user gets an equivalent list view of every node.
- Global search returns results across all six sources in **< 300 ms**.
- The orbit costs **< 5% CPU** when idle and pauses entirely when the window is unfocused.

**Risks.** Beautiful and useless → the covered-labels test above is the gate; if it fails, cut it.

**Definition of done.** The orbit is genuinely the view I leave open. If it isn't, it gets deleted —
that outcome is acceptable and should be recorded as an ADR.

---

## Phase 10 — Voice  **[Experimental]**

**Objective.** Speak to ORACLE and hear it answer, without touching the agent core.

**Depends on.** P4 only — voice is just another client of the same API.

**Tasks.** A separate `oracle-voice` process; Silero VAD; faster-whisper STT (Russian needs `small`+);
openWakeWord; TTS chosen at implementation time — **Piper was archived in Oct 2025**, so re-evaluate
against Windows SAPI/WinRT and Silero TTS ([TECH_STACK.md §10](TECH_STACK.md#10-voice-phase-10--deliberately-unresolved));
barge-in; a push-to-talk fallback.

**Acceptance criteria.**
- Wake word → transcript in **< 2 s**; Russian and English both usable.
- Voice runs on the CPU without evicting the router model from VRAM.
- **Zero changes to `packages/core`** to add voice. If a core change is needed, the client-peer
  architecture failed and that is worth knowing.
- Dangerous actions are **never** approvable by voice alone.

**Risks.** STT competing for CPU with indexing → priority classes and mutual exclusion. Accuracy on
mixed-language speech is genuinely hard → push-to-talk is the honest fallback.

---

## Phase 11 — Hardening  **[Continuous]**

Not a final phase; a standing workstream that begins at P2.

**Ongoing.** Security suite growth with every new surface · dependency and CVE review · audit-log
verification in CI · backup/restore procedure for `oracle.db` and secrets · performance budgets as
tests · crash reporting · **quarterly re-verification of the vendor CLI contracts** (they will drift)
· the Pascal/CUDA watch item ([OQ-03](OPEN_QUESTIONS.md)).

**Future / explicitly not planned.** Multi-user, cloud sync, plugin marketplace, mobile-native apps,
model fine-tuning, arbitrary web browsing by the agent. Each would need its own design pass and none
serves the stated purpose.

---

## Sequencing rules

1. **No phase starts before its predecessor's Definition of Done.** Skipping the DoD is how P4 arrives
   with three half-finished phases underneath it.
2. **No write tool before P2.** Non-negotiable.
3. **Security tests are a merge gate from P2 onward.**
4. **Every phase updates `docs/current_task.md` and `docs/current_report.md`.**
5. **An `EXPERIMENT NEEDED` blocking a phase is resolved *in* that phase, before the code that
   depends on it.**
