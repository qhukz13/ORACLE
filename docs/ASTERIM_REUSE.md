# ORACLE — Asterim Reuse Audit

> Audited 2026-08-24 against `C:\Projects\Asterim` (TypeScript monorepo, ~510 source files, active)
> and `C:\Projects\asterim-pipeline` (zero-dependency Node supervisor, ~8k LOC). Asterim is
> ORACLE's sibling: a local control plane that supervises CLI coding agents (Antigravity, Claude
> Code, Aider) in git worktrees with human approval gates. It has already paid for several lessons
> ORACLE's orchestration phases would otherwise re-buy. Nothing is imported as a dependency — both
> projects are TypeScript/JS and ORACLE is Python — so every entry is **port** (rewrite in
> Python), **adapt** (reuse the schema/constants/rules), or **reference** (read before designing).

## The headline

**`asterim-pipeline` is a working prototype of ORACLE's supervisor core.** It drives an
Antigravity orchestrator plus two Claude Code workers through an explicit state machine, on
Windows, today. Read it in this order before implementing Phase 7:
`src/state-machine.js` → `src/store.js` → `src/parse.js` → `src/agents.js` → `src/runner.js` →
`src/agent-stream.js` → `src/config.js`. That is ~2,000 LOC covering roughly 70% of the
supervisor's hard-won details.

## Tier 1 — port or adapt directly

| Component | Asterim implementation | Why useful | Action | Complexity | Risks |
|---|---|---|---|---|---|
| **Supervisor state machine + persisted recovery** | `asterim-pipeline/src/state-machine.js`, `store.js`, `runner.js` (`recover()`) — explicit transition table with `assertTransition`; atomic temp-file+rename persistence; corrupt state → human gate; on restart: prior agent PID alive → gate, agent gone mid-run → gate, **never auto-restart an interrupted agent** | These are exactly the crash-recovery rules ORACLE's task runner needs, empirically derived | **PORT** | low (~400 LOC) | its states hardcode three roles; generalise to a role table |
| **Agent subprocess launcher** | `asterim-pipeline/src/agents.js` — per-role timeout with tree-kill, `AbortSignal` cancellation, stdout/stderr teed to log + callback, EPIPE-safe stdin, retry-with-shell for Windows `.cmd` shims (post-CVE-2024-27980 EINVAL), a test interlock that refuses to spawn a real agent from fixtures | Every one of these edge cases bites on this platform | **PORT** (asyncio + existing Job Object; the Job Object is stronger than its `taskkill`) | low-med | none significant |
| **Result validation that distrusts exit codes** | `runner.js` `coderStep()`/`testerStep()` — hash the report file before the run, gate if unchanged after; parse required fields, gate on mismatched task id; require git evidence for a claimed change, excluding the protocol files themselves | **The most important lesson in either repo**: headless agents exit 0 having written nothing. ORACLE's diff-based collection already embodies half of this; the report-hash and task-id checks complete it | **PORT** | low-med | none |
| **Human-gate rules** | `runner.js` `gate()`, `agentAborted()` — gates on: phase completion, agent failure/timeout, N consecutive failures, malformed/mismatched reports, completion claimed without evidence, corrupt state, ambiguous recovery | A principled, tested answer to "when does the supervisor stop and ask" | **PORT** | low | none |
| **Task-DAG algebra** | `Asterim/packages/shared/src/types/pipeline.ts` — `findPipelineCycle` (returns the cycle *as a path*, iterative to survive adversarial input), `readyPipelineStepIds` (ready = pending ∧ all deps passed → fail-closed for free), `aggregatePipelineRunStatus` (precedence CANCELLED > FAILED > RUNNING > PASSED), `SKIPPED ≠ CANCELLED` | ~200 LOC of pure, dependency-free functions that *are* the task-graph semantics; `graphlib` covers topology but not the cycle-as-path diagnostic or the status algebra | **PORT** | low | none |
| **Delegation bounds + outcome vocabulary** | `Asterim/packages/shared/src/types/delegation.ts` — `MAX_DELEGATION_DEPTH = 3`, `MAX_CONCURRENT_DELEGATIONS = 4` (depth bounds height, width bounds fan-out), `COMPLETED \| FAILED \| TIMEOUT` where TIMEOUT is deliberately not folded into FAILED ("a child that ran out of time may well have done the work") | The recursion hazard and its bounding, already thought through | **ADAPT** constants + enums | trivial | ORACLE v1 forbids delegate-spawned delegates entirely (the MCP surface offers no `ai.delegate`), which is stricter |
| **Claude stream-json field knowledge** | `asterim-pipeline/src/agent-stream.js` — which fields matter per event; `result.permission_denials` called out as "the single most useful thing when a headless run does nothing"; never throws on unknown shapes | Cross-checks ORACLE's own pinned contract; `permission_denials` is not currently surfaced by `ClaudeCodeAdapter` and should be | **ADAPT** (fold into the adapter + fixtures) | trivial | vendor drift — already covered by quarterly re-verification |
| **CLI invocation matrix** | `asterim-pipeline/src/config.js`, `Asterim/.../ActiveAgentProvider.ts` — Claude: prompt on **stdin**; Antigravity: prompt as the **last argument** to `-p`, separate `--print-timeout`; `agy.cmd` Windows variants | Saves the AntigravityAdapter a day of trial and error, including the non-obvious argument ordering | **ADAPT** as config | trivial | do **not** copy `--dangerously-skip-permissions` — see below |
| **Role prompt discipline** | `config.js` `DEFAULT_PROMPTS` — "you get exactly ONE session; do not end with a progress update; nothing left running will be observed"; failure made writable ("write the report anyway with Status: BLOCKED"); paths injected by the supervisor, never chosen by the agent | The planner/worker prompts face identical failure modes | **ADAPT** into the packet renderer | trivial | none |
| **Tolerant structured-field parsing** | `asterim-pipeline/src/parse.js` — one regex tolerant of `**Status:** ✅ PASS` and friends; synonym normalisation; `{valid, problems[]}` never throws | The fallback when `--json-schema` output fails — a pragmatic middle ground before giving up on a run | **PORT** (~150 LOC) | trivial | none |
| **Output ring buffer with cursor + drop detection** | `asterim-pipeline/src/output-bus.js` — `since(cursor)` returns `{lines, cursor, dropped}` so a reconnecting client knows its cursor fell off the back | Complements `since_seq` for the high-volume delegate stream the event log deliberately doesn't persist | **PORT** if needed at Phase 7 UI | trivial | none |

## Tier 2 — reference before designing

| Component | Where | The idea worth taking |
|---|---|---|
| Event persist/replay/redact | `Asterim/apps/server/src/services/EventBus.ts`, `sockets/socketManager.ts` | Persist durable events, ring-buffer the firehose (`agent.log` in a 500-entry ring, not the DB), replay-and-merge on join, redactor injected at the bus so an echoed secret never reaches any subscriber. ORACLE's event log already does most of this; the ring/DB split for delegate output is the missing piece. |
| Pipeline engine principles | `Asterim/.../PipelineEngine.ts` (header) | "A step is a delegation — no second way to run an agent" · "fail-closed" · "the row is the record, not the memory" · "cancellation is a separate answer". Adopted verbatim as Phase 7 design rules. |
| Delegation service shape | `Asterim/.../AgentDelegationService.ts` (first ~40 lines) | Parent parks in `WAITING_FOR_CHILD`; child runs through the *same* path as user-started work so it inherits every guarantee; parent released on every exit path. |
| Turn lock | `Asterim/.../AgentTurnLock.ts` | FIFO decided synchronously at enqueue, before any `await`; every transition broadcasts the whole queue. Pure logic, no I/O — testable. |
| Approval risk heuristics | `Asterim/.../ApprovalManager.ts` | Tool-name → risk-family table as a cheap pre-classifier. ORACLE's argument-resolved tiers are strictly stronger; useful only for foreign MCP tools with unknown contracts. |
| DAG rendering | `Asterim/apps/web/.../PipelineDagGraph.tsx` | Column = **longest** path from a root ("shortest would claim a parallelism the DAG does not have"); plain SVG + real buttons, no graph library — matches ADR-0013's philosophy. Port `dagColumns()` for the Phase 11 execution tree. |
| MCP client timeouts | `Asterim/.../McpStdioClient.ts` | Two-tier timeouts (handshake 5 s vs tool 30 s — "a handshake's patience is the wrong measure for someone else's code"), bounded queue depth with refusal. |
| Env sanitisation | `Asterim/.../ProcessManager.sanitizeAgentEnv()` | Allow-list, not deny-list ("a deny-list only protects against the variables someone remembered to name"). ORACLE's constructed-env already is an allow-list; keep it that way. |

## Do not copy

- **The terminal-screen-scraping FSM** (`Asterim/packages/adapters/.../TerminalFSM.ts`, 587 LOC +
  parser): infers agent state by diffing an `@xterm/headless` buffer and string-matching TUI chrome.
  Impressive engineering on a bad foundation; it exists only because that agent lacked structured
  output. ORACLE drives `--output-format stream-json` and must never parse a TUI.
- **`--dangerously-skip-permissions` as a default.** Asterim sets it for its orchestrator with a
  written apology that prompt text is the only enforcement left. ORACLE's answer is
  `--permission-mode dontAsk` + explicit allow rules + the MCP callback surface — already built.
- **`ClaudeAdapter.ts` and `AiderAdapter.ts`** in the adapters package are stubs (the Claude one
  launches `node -e 'console.log(...)'`). The real Claude integration lives in `asterim-pipeline`.
- **`QuestionManager` resolving to option 1 on timeout "for safety"** — that is a bug pattern. A
  timed-out question fails or escalates; it never picks an answer.
- **`wmic`-based process discovery** — removed on current Windows; ORACLE's Job Object + `psutil`
  is the correct mechanism.
- **Socket.IO `cors: {origin: '*'}`** and the `'*'` catch-all event fan-out — self-described MVP
  shortcuts.
- **Everything enterprise-shaped** (RBAC, billing, relay, fleet policy) — a large fraction of the
  34k LOC, irrelevant to a single-user local supervisor.

## Licensing

Both repos are the owner's own work; there is no third-party licensing question in porting from
them. Ported code follows this repo's conventions and tests, not the originals'.
