# ORACLE — External Agent Integrations

How ORACLE delegates real work to Claude Code, Antigravity, and anything that comes next — and how it
verifies what came back.

> All CLI facts below were checked against primary documentation on **2026-08-21** and are marked
> `VERIFIED`. Vendor CLIs drift; re-verify quarterly ([ROADMAP P14](ROADMAP.md#phase-14--hardening--continuous)).

## 1. Integration tiers

Stated honestly, because the difference between "documented" and "works when I pipe it" is where this
kind of project actually breaks.

| Tier | Meaning | Members |
|---|---|---|
| **Supported** | Documented, stable, tested against fixtures, safe to depend on | Claude Code CLI · Anthropic Messages API · ORACLE's own MCP server · **Antigravity CLI (`agy`)** |
| **Potential** | Official interface exists, but an unresolved blocker stands between us and it | — |
| **Experimental** | Might work, no stability promise, never on the critical path | Antigravity SDK · other CLI agents (Gemini CLI, Codex, Aider) |
| **Fallback** | Always works, no vendor dependency at all | **Handoff Packet** |

**The fallback is a first-class design element, not a consolation prize.** It is the only path
guaranteed to survive a vendor changing its CLI, and it works with agents that do not exist yet.
Everything else is an optimisation on top of it.

---

## 2. The adapter interface

```python
class ExternalAgentAdapter(Protocol):
    id: str
    def capabilities(self) -> AgentCaps: ...
        # streaming? resume? structured_output? workspace_scoped? cost_reporting?
    async def preflight(self) -> Preflight: ...
        # binary present? authenticated? version? → drives graceful degradation
    async def submit(self, packet: HandoffPacket, ws: Workspace) -> AgentHandle: ...
    async def events(self, h: AgentHandle) -> AsyncIterator[AgentEvent]: ...
    async def cancel(self, h: AgentHandle) -> None: ...
    async def collect(self, h: AgentHandle) -> AgentResult: ...
```

`AgentEvent` is ORACLE's own vocabulary (`started`, `thinking`, `tool_use`, `text`, `error`,
`finished`) — vendor event shapes are normalised at the adapter boundary and never leak upward. This
is what keeps the UI, the event log and the task inspector identical regardless of which agent ran.

`preflight()` is what makes degradation graceful: if the binary is missing or unauthenticated, ORACLE
knows *before* building a packet and can route to the fallback with a clear explanation instead of
failing halfway through.

---

## 3. Claude Code CLI — **Supported**

Installed on this machine: **v2.1.238** (checked 2026-08-23).

### Authentication — measured 2026-08-23, and it changed the contract

The owner authenticates Claude Code with a **Claude Max subscription** (OAuth login) and has **no
API credit**. That kills the original `--bare` + `ANTHROPIC_API_KEY` design, measured, not assumed:

- `--bare` ignores the OAuth login. `VERIFIED` on v2.1.238: `apiKeySource: "none"`, the run fails
  with *"Not logged in · Please run /login"* at zero cost — nothing egresses.
- `--bare` also does not read `CLAUDE_CODE_OAUTH_TOKEN` (the `claude setup-token` subscription
  token). Documented explicitly: bare mode authenticates only via `ANTHROPIC_API_KEY` or an
  `apiKeyHelper`.
- A **non-bare** `-p` run picks up the existing subscription login with no key and no token.
  `VERIFIED` on v2.1.238 on this machine: `is_error: false`, structured result collected.
  Subscription use for headless `-p` is permitted; `setup-token` exists precisely for it.

So ORACLE runs **without `--bare`**, and rebuilds its isolation materially instead of by flag —
see the worktree scrub below. The `ANTHROPIC_API_KEY`-from-Credential-Manager path
([SECURITY.md §7](SECURITY.md#7-secrets-and-egress)) remains documented as the alternative for a
machine with API billing, and `--bare` would be preferred again there.

### Invocation

```bash
claude -p "<task>" \
  --output-format stream-json --verbose \
  --json-schema '<result schema>' \
  --setting-sources user \
  --strict-mcp-config \
  --allowedTools "Read,Edit,Bash(git diff *),Bash(npm test *)" \
  --permission-mode dontAsk \
  --add-dir "<worktree>" \
  --append-system-prompt-file "<constraints.md>"
```

**Precondition — the worktree scrub.** Without `--bare`, a `-p` session loads hooks from the target
project's `.claude/settings.json` and connects MCP servers from its `.mcp.json`, *even in a folder
never trusted* (`VERIFIED` on v2.1.234; the reason `--bare` was chosen originally). Delegates only
ever run in ORACLE's **disposable worktree**, so the fix is material rather than a flag: after
`git worktree add` and before invocation, ORACLE **deletes `.claude/` and `.mcp.json` from the
worktree**. Hooks cannot load from files that do not exist, and the diff-based result collection is
indifferent to their absence. Residual exposure: user-level (`~/.claude`) hooks and plugins still
load — those are the owner's own configuration on the owner's machine, and they are listed in
`system/init` (`plugins`, `mcp_servers`), which the adapter checks and logs.

Each flag is load-bearing:

| Flag | Why ORACLE uses it |
|---|---|
| *(no `--bare`)* | Blocked by auth on this machine, see above. Its isolation is rebuilt by the worktree scrub + the two flags below. |
| `--setting-sources user` | Skip project/local settings sources — belt to the scrub's braces for hooks that arrive via settings. |
| `--strict-mcp-config` | Only MCP servers passed via `--mcp-config` exist; the project's `.mcp.json` is ignored even if the scrub missed one. |
| `--output-format stream-json` | Live progress events → ORACLE's event stream → the UI. |
| `--json-schema` | Forces a structured result into `structured_output`, so ORACLE parses a typed object rather than scraping prose. |
| `--permission-mode dontAsk` | `VERIFIED`: denies anything outside the allow rules — the right posture for an unattended run. `-p` starts in Manual mode otherwise. |
| `--allowedTools` | Prefix-matched rule syntax, e.g. `Bash(git diff *)`. **The space before `*` matters**: `Bash(git diff*)` would also match `git diff-index`. |
| `--add-dir` | Scopes the run to the isolated worktree. |
| `--resume <session_id>` | Follow-up turns. `VERIFIED`: since v2.1.223 a session is findable by ID from any directory. |

### Stream events consumed

`VERIFIED` shapes (re-recorded on v2.1.238, `tests/fixtures/claude_stream/smoke-v2.1.238.jsonl`):
`system/init` (session id, model, tools, `mcp_server_errors`, `plugin_errors`) · assistant/user
messages, where `parent_tool_use_id` identifies subagent output · `system/api_retry` (attempt,
delay, error category) — surfaced as a "retrying" state rather than a failure · `result` with
`total_cost_usd` for the cost display in the Task Inspector.

Three corrections the v2.1.238 recording forced, each one an adapter rule:

- **`result` is the terminal *semantic* event, not the last line** — a `system/task_summary`
  follows it. The adapter finishes on `result` and drains the rest.
- **`system/init` is not necessarily first** — user-level hooks emit `system/hook_started` /
  `hook_response` *before* init. The adapter waits for init rather than assuming position 0.
- **Unknown event types are logged and skipped, never fatal** — v2.1.238 already emits kinds the
  v2.1.234 doc never listed (`system/thinking_tokens`, `rate_limit_event`, `system/task_summary`),
  so the vocabulary will grow again.

### Process control

`VERIFIED`: **SIGINT** ends the current turn cleanly; **SIGTERM** exits 143, leaves the turn
unfinished, terminates the process tree of any running Bash command, then runs `SessionEnd` hooks.
ORACLE's `ai.cancel` therefore sends SIGINT first and escalates to SIGTERM after a grace period —
and the enclosing Job Object is the final backstop ([ARCHITECTURE §3](ARCHITECTURE.md#3-process-model)).
Exit code 0 = success, non-zero = failure; failures inside a run are printed as the result on stdout.

Piped stdin is capped at **10 MB** — so ORACLE passes large context as *files in the worktree*, never
piped. This is a real constraint on packet design, not a detail.

---

## 4. ORACLE as an MCP server — **Supported**

The most interesting direction of this integration is **inbound**.

Instead of granting a delegated agent broad `Bash` access, ORACLE exposes its own guarded tools over
MCP and passes them in with `--mcp-config`. Claude then calls `oracle.run_tests` or `oracle.search`
instead of running raw shell commands.

```
Claude Code ──MCP──▶ ORACLE tool server ──▶ policy gate ──▶ toolhost ──▶ OS
```

Why this is worth building: every action a delegated agent takes lands in ORACLE's audit log, obeys
ORACLE's scopes and tiers, and appears in the UI. Without it, delegation is a hole in the security
model — a second agent with its own permission system, operating outside the policy engine that the
rest of the design depends on.

`--allowedTools` then narrows to MCP tools plus a minimal Bash set, and `mcp_server_errors` in
`system/init` is checked so a silently unloaded server fails the run instead of degrading into raw
shell use.

### As built (P6-T3, 2026-08-24)

**A bridge, not a second runtime.** `python -m oracle.mcp` is spawned by the delegate's CLI over
stdio and forwards every call to the daemon on loopback. It holds no registry, no scopes and no
engine: tools execute through the *same* `ToolExecutor` as everything else, so one gate, one audit
log, one event stream. A bridge that evaluated policy would be the second permission system this
section exists to delete.

**Transport: the wire format, not the SDK.** `mcp==2.0.0` installs and runs here, but brings 24
packages — `cryptography`, `pywin32`, `opentelemetry-api`, `jsonschema` among them — into the
daemon's trusted base for a protocol that is four methods of newline-delimited JSON-RPC 2.0
(`initialize`, `notifications/initialized`, `tools/list`, `tools/call`). ORACLE implements those
four and pins them with tests that drive raw frames, the same way P6-T1 pinned the vendor stream by
recording it. Protocol version `2025-06-18`. **If a future client rejects this, take the SDK** — the
measurement above is the justification, and it is why there is no ledger line for `mcp` today.

**Delegation capability, not a bearer token.** `TokenStore` mints an HMAC-signed capability per
delegation naming its tool allowlist, its worktree, and its expiry; the key is process-lifetime and
never written to disk. The daemon re-derives every limit on each call. It is revoked on **every**
exit path — success, refusal, crash, HALT — along with the `--mcp-config` file that carried it.

**The lent surface**, deliberately small: `fs.read`, `fs.list`, `git.status`, `git.diff`,
`know.search`, `dev.run_tests`. Read and verify. The delegate edits with its own tools inside its
own disposable worktree — ORACLE does not need to mediate an edit it is going to diff anyway.

**T2+ is refused, never prompted.** An unattended delegate that could raise confirmation dialogs at
the owner would be prompt fatigue as a service ([SECURITY.md §2](SECURITY.md#2-design-principles)).
The refusal tells the delegate to ask in its result instead, where a human decides.

Every delegated call emits `tool.started` / `tool.finished` with the `task_id` and `actor:
"delegate"`, so the UI shows them under the delegation and "who did this" stays answerable once a
second agent exists.

---

## 5. Antigravity — **Supported** (unblocked 2026-08-21)

`VERIFIED 2026-08-21`: Antigravity 2.0 shipped at I/O 2026 with a standalone app, a **CLI (`agy`,
v1.1.x)**, an **SDK (v0.1.x)**, and a Managed Agents API. Headless mode is documented:

```bash
agy -p "<task>" --output-format json|stream-json \
    --model <slug> --effort low|medium|high \
    --json-schema <schema|path> --print-timeout 10m \
    [--continue | --conversation <id>] [--sandbox]
```

Stream events: `init` → `step_update`(×N) → `result`. Status values: `SUCCESS`, `ERROR`, `CANCELED`,
`INTERRUPTED`, `WAITING`, `INVALID`, `RUNNING`. stdout carries the response; stderr carries
diagnostics. Projects define the folder boundaries an agent may access. Tools needing approval are
**soft-denied** in headless mode unless pre-authorised.

### The former blocker — resolved

Open issue [google-antigravity/antigravity-cli#76](https://github.com/google-antigravity/antigravity-cli/issues/76)
(21 May 2026) reports that **`agy -p` silently drops stdout when stdout is not a TTY** — precisely how
ORACLE invokes it.

**Tested here on 2026-08-21 with `agy` v1.1.14, stdout redirected to a file**
([evidence](../logs/development/2026-08-21-oq05-antigravity-stdout.md)):

| mode | result |
|---|---|
| `--output-format json` | exit 0, 257 bytes, complete valid JSON |
| `--output-format stream-json` | exit 0, 2278 bytes, 5 NDJSON lines |

The bug affects **default text mode** only. **→ Always pass `--output-format`. Never rely on default
text output from a subprocess.** One rule, zero cost, since ORACLE wants structured output anyway.

### Stream envelope — undocumented shape

The discriminator is an `event` field, and the payload sits under a key **named after the event** —
*not* a `type`/`payload` pair:

```json
{"event": "init",        "init":        {"cwd": "...", "tools": [...], "permission_mode": "..."}}
{"event": "step_update", "step_update": {"step_index": 0, "state": "ACTIVE", "text_delta": "..."}}
{"event": "result",      "result":      {"status": "SUCCESS", "response": "...", "usage": {...}}}
```

The adapter parses `body = obj[obj["event"]]`.

**Cost caveat:** `agy` used **14,119 input tokens** to answer "say hello" — it injects a large system
prompt. Delegation is inherently expensive, but this makes Antigravity a poor fit for small calls;
route those to Claude or the local model.

Two of the three items OQ-05 left untested were measured in P6-T5 below (`--json-schema`,
cancellation). The third — unauthenticated behaviour — resisted measurement and is still
`UNKNOWN`; see *What could not be observed*.

### Contract cross-checks  `2026-08-24`

- The official headless docs (antigravity.google/docs/cli/headless, fetched 2026-08-24) confirm
  the surface above and add: `--input-format stream-json` for multi-turn within one process,
  `--print-timeout` default 5m, `--continue`/`--conversation` resume. `VERIFIED`.
- Asterim's working integration (`ASTERIM_REUSE.md`) confirms two things the docs understate:
  the prompt rides as the **value of `-p`** (last argument), not stdin — the opposite of Claude —
  and `agy.cmd` shim handling matters on Windows. `VERIFIED` in Asterim's usage.
- Asterim passes `--dangerously-skip-permissions`; ORACLE **will not**. Headless approval prompts
  are soft-denied by default, which is the posture ORACLE wants: a denial surfaces in the result,
  where the planner ladder or a human handles it. Same reasoning as Claude's `dontAsk`.
- No official Antigravity SDK could be confirmed; no native ACP support (open feature request).
  The CLI is the integration surface. `VERIFIED` absence as of 2026-08-24.

### As built (P6-T5, 2026-08-24)

`src/oracle/integrations/antigravity.py`, against fixtures recorded from **`agy` v1.1.19** by
`scripts/record_agy_stream.py` and replayed offline in `tests/test_antigravity_adapter.py`
(15 tests, in `make check`). What the recording changed:

**Vendor drift is continuous, not quarterly.** The CLI updated itself from v1.1.17 to v1.1.19
*during this task's recording session*, roughly forty minutes apart. The first fixture set was
discarded and re-recorded rather than mixed. Re-verification cadence is a floor, not a schedule.

**Without `--dangerously-skip-permissions`, headless `agy` is read-only.** Measured, not
inferred: `view_file` and `find_by_name` run unprompted; `write_to_file` is soft-denied
(`permission check failed … user denied permission`), the model retries via `run_command` and is
denied again, and the run terminates `status: ERROR` with **exit code 1**. ORACLE will not pass
that flag, so this is now a stated capability, not a surprise:

> **Antigravity can hold `planner`, `reviewer` and `researcher`. It can never hold `coder`.**

That is a happy accident of alignment — every role the capability registry assigns it
(PLANNER.md §4) is read-only — but it must be written down, because a packet with write tools
would otherwise fail looking like a model failure.

**Exit code and vendor status are both required.** A soft denial yields exit 1 *and*
`status: ERROR`; the schema path yields exit 0 *and* `SUCCESS`. `collect()` requires the
conjunction, and a test pins the disagreeing case.

**`--json-schema` works, including `$defs`/`$ref`.** The `result` body carries
`structured_output` — already **filtered to the requested schema** — beside a `json_schema` echo
of what was asked for. The raw `response` string carries extra vendor keys (`toolAction`,
`toolSummary`) that `structured_output` does not. ORACLE reads `structured_output`; the prose is
never parsed.

**Cancellation, timed to the millisecond**
(`tests/fixtures/agents/antigravity/cancel-v1.1.19.timing.txt`): `CTRL_BREAK` at 12.00 s →
terminal `result` at 12.11 s → child exits 1 at 12.15 s. Interrupt alone suffices; terminate and
kill were never reached; nothing was left in the workspace. But the status is **`ERROR` with the
message "timeout waiting for response"** — never the documented `CANCELED`/`INTERRUPTED`. So **a
cancelled run and a genuine vendor timeout are indistinguishable from the stream alone**; only
ORACLE's own record of having sent the signal separates them, which is why the adapter never
infers cancellation from the stream.

**Tool steps arrive twice** — `ACTIVE`, then `DONE` or `ERROR`, under one `step_index`. Only the
first becomes a `tool_use` event, or every call would be double-counted in the inspector.

**ORACLE's tool server cannot be lent to `agy`.** There is no `--mcp-config`: MCP servers are
global config edited by `agy mcp`. Honouring a packet's `mcp_config` would mutate machine state
for one delegation; ignoring it would run a delegate that believes it holds ORACLE's guarded
tools. `command()` therefore **fails closed** and the packet routes to Claude. Nor is there an
allow-list flag — `--add-dir` scopes the filesystem, and nothing scopes the toolset.

**Cost, again.** ~15k input tokens per *turn*, so multi-turn runs accumulate fast: 31k for a
two-turn read, 46k for the denied write, 55.6k for a planning call. `usage` reports tokens and
never money — this is quota-metered — so `capabilities().cost_reporting` is **false** and
`AgentResult.cost_usd` stays `None` rather than carrying an invented figure.

#### What could not be observed

`preflight()` distinguishes three states, and only two were observed for real. The probe for the
third is `agy models` — a vendor round trip that costs **no model tokens**, which matters because
every `-p` call costs ~15k before the model reads a word.

| State | Observed? | How |
|---|---|---|
| binary missing | yes | `shutil.which` on a name that is not there |
| ready | yes | `--version` → `1.1.19`, `models` → the model list |
| present but **unauthenticated** | **no** — `UNKNOWN` | see below |

Redirecting `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA` and `XDG_CONFIG_HOME` to an empty
directory **did not deauthenticate `agy`**: it ran the task normally. Its credentials do not come
from any of those. The Antigravity IDE was running throughout, which is a plausible source and an
untested hypothesis, not a finding. The real configuration was never touched, deliberately. So
the unauthenticated branch is written from the vendor's documented behaviour and is marked
`ASSUMPTION` in the adapter until someone signs out, or runs it on a machine that never signed
in. **Do not infer it from a green preflight here.**

### As planner — the role it did not get  `measured 2026-08-24`

The adapter serves two roles through the same interface. As a **worker** (reviewer/researcher) it
is a peer of the Claude adapter, and that is the role it keeps.

As the **planner** it would be invoked with a planning TaskSpec and `--json-schema <ExecutionPlan>`,
returning a structured plan and executing nothing (authority, validation, taint and fallbacks in
[PLANNER.md](PLANNER.md)). The design made it the default holder because its ~15k-token prompt
overhead amortises over a whole graph. **The P6-T5 spike measured it and the ladder promoted**
([OQ-20](OPEN_QUESTIONS.md#oq-20)): 12/16 plans valid on first attempt — **75% against a 90%
gate** — at ~55k tokens and 27–43 s per plan. Claude authors plans now; the mechanism is unchanged,
only the registry line.

Three findings behind that number, all of which outlive the verdict:

* **`--effort high` is where it broke.** Every failure was a high-effort run in which the planner
  browsed the filesystem — reaching for the owner's home directory from an empty workspace — and
  was denied by the vendor's permission gate, which ended the run. If it is ever reconsidered for
  this role, `--effort low` is pinned (SECURITY.md §10).
* **`structured_output` can be silently emptied.** One `SUCCESS` returned a schema-valid plan with
  `tasks: []` while its raw `response` held six well-formed tasks: the vendor's filter drops
  non-conforming items without saying so. Never treat the field's presence as proof of content.
* **Conformant ≠ schedulable.** Only 7 of 12 valid plans declared any dependency at all.

---

## 6. The Handoff Packet — **Fallback, and the core abstraction**

A self-contained, vendor-neutral task description. Every adapter renders *from* this; the fallback
simply writes it to disk.

```
.oracle/handoff/<task-id>/
├── TASK.md              goal, acceptance criteria, constraints
├── CONTEXT.md           curated excerpts with sources — NOT a repo dump
├── ATTEMPTS.md          what was tried before and why it failed
├── FILES.md             the files that matter, and why each is included
├── STATE.md             git branch/status, failing tests, recent errors
└── packet.json          machine-readable form of all of the above
```

```markdown
# TASK
Fix authentication token refresh in Asterim.

## Acceptance criteria
- [ ] `pnpm test auth` passes
- [ ] No change to the public API of TokenService
- [ ] The 401-after-15-minutes case is covered by a test

## Constraints
- Do not modify apps/server/db/migrations/**
- TypeScript strict mode must still pass
- Commit to branch fix/auth; do not push

## Prior attempts
- 2026-08-19, claude: added a null check in refresh(); tests still failed —
  the token was expiring server-side, not client-side. Do not repeat this.
```

### Context assembly for the packet

The rule: **curated, not dumped.** A repository dump wastes the delegate's context, costs money, and
buries the signal. Selection order:

```
1  files named by the user / the current diff / the failing test's imports
2  symbol-level neighbours of those (call sites, definitions) via tree-sitter
3  top hybrid-retrieval hits for the goal, scoped to the project     (RAG.md §5)
4  project docs: README, AGENTS.md, CLAUDE.md, decisions.md
5  git state: branch, uncommitted diff, last 5 commits touching these files
6  prior attempts                                                    (MEMORY.md §4)
   ─── then ───
7  REDACT (secret scan, every chunk)                        (SECURITY.md §7)
8  BUDGET (cap: 30k tokens by default — an explicit ceiling, not "as much as fits")
9  EGRESS PREVIEW
```

Step 4 is worth calling out: Asterim already contains `AGENTS.md`, `CLAUDE.md` and `decisions.md`.
Those files exist precisely to orient a coding agent, and forwarding them is far higher-value per
token than more source code.

### Egress preview

Before any bytes leave the machine:

```
┌──────────────────────────────────────────────────────┐
│ ⚠  SENDING TO CLAUDE (api.anthropic.com)             │
│                                                      │
│ 14 files · 28,400 tokens · est. $0.09                │
│                                                      │
│ TASK.md · CONTEXT.md · 11 source files · STATE.md    │
│ ▸ preview full payload                               │
│                                                      │
│ 2 redactions applied:                                │
│   .env.example line 4  → [REDACTED:api_key]          │
│   token.ts line 12     → [REDACTED:jwt]              │
│                                                      │
│ [ Send ]  [ Edit selection ]  [ Cancel ]             │
└──────────────────────────────────────────────────────┘
```

This is the concrete meaning of "local-first" in a system that talks to a cloud API: not *never send*,
but *never send without seeing it*.

---

## 7. Workspace isolation and result collection

**Delegated agents never work in the live project directory.**

```
1  git worktree add .oracle/wt/<task-id> -b oracle/<task-id>
2  record base commit + a hash of the working tree
3  run the agent, scoped to the worktree (--add-dir / project boundary)
4  on completion:
     git diff <base>..HEAD          → the actual change
     dev.run_tests in the worktree  → independent verification
     policy check on touched paths  → did it stay in scope?
5  present: diff + test results + summary + cost
6  I decide: merge · keep the branch · discard (worktree removed)
```

Two properties this buys, both essential:

- **Verification is independent of the agent's own report.** An agent claiming "tests pass" is a
  claim; ORACLE running the tests is evidence. ORACLE reports the evidence.
- **A bad run is free to discard** — remove the worktree and the branch; the working tree was never
  touched, and nothing was staged, committed or pushed in the real project.

For non-git projects (GrowAMonster, MonsterGarden have no `.git`), the fallback is a **snapshot copy**
into scratch with the same before/after diff, plus a strong recommendation to `git init`. Recorded as
a limitation, not silently ignored.

---

## 8. Reference scenario

"Check why Asterim authentication is broken." — the brief's example, as it actually executes:

| # | Actor | Action | Tier |
|---|---|---|---|
| 1 | pre-router | no match → model | — |
| 2 | router | intent `investigate`, project `Asterim` (conf 0.81) | — |
| 3 | tools | `git.status` → branch `fix/auth`, 3 modified | T0 |
| 4 | knowledge | hybrid search "auth" scoped to Asterim → 6 chunks | T0 |
| 5 | tools | `fs.read` ×3 on top hits | T0 |
| 6 | tools | `dev.run_tests(Asterim, "auth")` → 2 failures, captured | T1 |
| 7 | router | complexity + failure signature ⇒ **delegate** | — |
| 8 | context | build Handoff Packet incl. prior attempt from 19 Aug | T0 |
| 9 | **user** | **egress preview → approve** | **T2** |
| 10 | integration | worktree + `claude -p --bare …`, events streamed live | — |
| 11 | collect | diff + independent test run in the worktree | T1 |
| 12 | respond | summary, diff, test results, cost, merge/discard options | — |

Steps 1–6 cost nothing but local compute and typically take a few seconds. Step 7 — recognising that
this needs a stronger model — is the actual intelligence in the system, and it is a decision a small
model can make reliably because it is a classification, not a solution.

### As executed (P6-T4, 2026-08-24)

This scenario is now a test — `tests/test_reference_scenario.py` — running the real pipeline, gate,
approval store, packet renderer, worktree and collection, with only the local model and the vendor
CLI replaced by replays. What it asserts is the **order**, because the order is the design: the tool
call precedes the approval request, the approval precedes any egress, and the report follows the
collection.

Two things the design sketch above got slightly wrong, corrected by building it:

- **Step 7 needs no model at all.** The escalation signal is "a verification tool reported failure in
  this turn" — a fact about `dev.run_tests`, not a judgement. A model deciding to spend money on a
  stronger model is a loop nobody asked for, and the fact is cheaper *and* more reliable. What
  escalates is narrow on purpose: a test suite that ran and failed, never a tool that could not run
  (a denial or a missing runner would hit the delegate the same way).
- **Step 9 is the only prompt.** An earlier reading had ORACLE ask "shall I delegate?" and then
  "here is what would be sent". That is two questions for one decision. Escalation *starts* the
  delegation; the egress preview is where a human says no, and a refused delegation costs a packet
  render and nothing else.

The prior attempt from step 6 is carried into the packet's `ATTEMPTS.md` — the failing test names
and what ORACLE already ran — so the delegate does not spend its first turns rediscovering it. The
explicit route ("ask Claude to …") is recognised by the **pre-router** in ~5 ms rather than by the
model, and a project the user did not name is asked about rather than guessed.

### The live run (2026-08-24)

The one claim no offline test can make — §4's "the delegate calls `mcp__oracle__fs_read` instead of
shelling out, and the call lands in ORACLE's audit log" — was proven live with
`scripts/verify_mcp_live.py`: real `claude` CLI under the machine's subscription login, real
`python -m oracle.mcp` bridge, a throwaway daemon on loopback, payload previewed and approved before
egress. The delegate loaded the server, called the ORACLE tool rather than its own Read, answered
correctly, and the call is in the audit log. The exchange is pinned as
`tests/fixtures/mcp/live-verify.jsonl`.

It took three attempts, and both failures were the design working:

- **A token minted outside the daemon is a bad signature.** The script first minted from its own
  `TokenStore`; the daemon verified with its own process-lifetime key and refused
  (`mcp.tools_rejected: bad signature`), exactly as "one per daemon" intends. The script now mints
  from the running daemon's store — which is also the only way a real delegation ever gets one.
- **The audit log lives under `log_dir`, not `data_dir`.** The throwaway daemon inherited the real
  `log_dir`, so its one audit entry landed in the owner's live audit log (harmless — a genuine,
  gated `fs.read`) while the check read an empty file. The verification daemon now scopes `log_dir`
  into its workspace like everything else it touches.

---

## 9. The external landscape — surveyed 2026-08-24, decisions in ADR-0022

Recorded here so the next evaluation starts from evidence, not memory. Full analysis:
[`logs/development/2026-08-24-supervisor-replan.md`](../logs/development/2026-08-24-supervisor-replan.md).

| Candidate | License | Verdict | Trigger to revisit |
|---|---|---|---|
| **Claude Agent SDK** (Python, 0.x) | MIT `ASSUMED` | keep the pinned CLI contract; the SDK wraps the same transport with typed events + PreToolUse hooks that could enforce the gate in-process — genuinely better, and not worth replacing a working, fixture-pinned contract for while it is 0.x | next breaking CLI drift ([OQ-19](OPEN_QUESTIONS.md#oq-19)) |
| **ACP** (Agent Client Protocol) | Apache-2.0 | JSON-RPC/stdio agent protocol; permission-request channel maps well onto the gate — but Claude and Antigravity both require Node adapter shims, so today it adds a hop to reach agents ORACLE reaches natively | a third-party agent worth integrating |
| **OpenHands Software Agent SDK** | MIT `VERIFIED` | closest reference architecture (event stream, modular tools/workspace); adopting its server would duplicate the runtime | reference only |
| **LangGraph** | MIT `VERIFIED` | durable execution/checkpointing duplicate the event-sourced runtime; graph needs are ~300 LOC of pure functions | never for the graph |
| **CrewAI** | MIT | LLM manager-agent is the anti-pattern ADR-0019 rejects; role vocabulary referenced | — |
| **AutoGen / MS Agent Framework** | MIT `ASSUMED` | AutoGen in maintenance mode; successor's typed workflow graphs are reference material | — |
| **A2A** (Linux Foundation) | Apache-2.0 | peer-agent interop over HTTP; out of scope for a local supervisor | ORACLE ever exposing itself as an agent |
| **MCP spec 2026-07-28** | — | stateless core + Tasks extension + MRTR; ORACLE's hand-rolled server speaks `2025-06-18` and works | a client rejecting the surface ([OQ-21](OPEN_QUESTIONS.md#oq-21)) |
| **Pydantic-AI** (v2) | MIT `VERIFIED` | strongest candidate *if* the local-model worker tier ever wants a library; not needed for routing as built | the three-tier model stack getting scheduled |

No copyleft exposure exists on any considered path.

## 10. Adding a new agent

1. Implement `ExternalAgentAdapter`; normalise its events into ORACLE's vocabulary.
2. Implement `preflight()` honestly — the degradation path depends on it.
3. Record **fixtures** of its real output in `tests/fixtures/agents/<id>/` and test the adapter against
   a stub CLI replaying them. No network, no cost, deterministic.
4. Document its tier in this file with the date it was verified.
5. Never place a non-Supported agent on a default path.
