# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P6-T3 — Close the delegation hole: ORACLE's MCP server**

**Phase:** [6 — External agent integration](ROADMAP.md#phase-6--external-agent-integration--post-mvp) · **Scope:** Post-MVP
**Status:** `IN PROGRESS — requirements 2-6 done 2026-08-24; live recording + gate open` · **Set:** 2026-08-24
**Previous task:** P6-T2 — **done** (requirements 1–7, gate green; see [current_report.md](current_report.md)).

---

## Carry-over — still the bge-m3 decision

Unchanged: the scheduled run (`bge-m3-full-corpus-run`, fires on next app launch if 05:00 was missed)
writes [OQ-02](OPEN_QUESTIONS.md#oq-02) and a dev log; the model choice goes to the owner. Its
commits stay separate from delegation work. Nothing here depends on it.

## Why this task exists

[INTEGRATIONS.md §4](INTEGRATIONS.md#4-oracle-as-an-mcp-server) states the problem exactly:

> Without it, delegation is a hole in the security model — a second agent with its own permission
> system, operating outside the policy engine that the rest of the design depends on.

P6-T2 made egress visible and approvable. But once approved, the delegate works with **its own**
tools under **its own** permission model. Its file reads, its test runs and its searches are invisible
to ORACLE's audit log and ungoverned by ORACLE's gate. This task closes that: the delegate calls back
in, and every call it makes lands in the same audit log, obeys the same scopes and tiers, and shows
up in the same UI as anything ORACLE does itself.

## The decision this task opens with

**The MCP server is a bridge process, not a second runtime.** The delegate's CLI spawns it over
stdio (`--mcp-config`), but the tools must execute in the daemon — one policy engine, one audit log,
one event stream. So the bridge forwards to the daemon over loopback with a **per-delegation token**,
and holds no policy of its own. A bridge that evaluated policy locally would be the second permission
system this task exists to delete.

**The SDK question, measured before writing code against it** (the OQ-09 rule): `mcp==2.0.0` installs
and `MCPServer.run_stdio_async` works on this machine — but it brings **24 packages**, including
`cryptography`, `pywin32`, `opentelemetry-api` and `jsonschema`, into the daemon's dependency tree
for a protocol whose wire format is newline-delimited JSON-RPC 2.0. Requirement 1 settles it with a
recorded contract rather than an opinion.

## Requirements

1. **Settle the transport, with evidence.** **MOSTLY DONE 2026-08-24** — hand-rolled surface built and pinned by raw-frame tests; the SDK measurement (24 packages) is written into TECH_STACK.md §9 and INTEGRATIONS.md §4. **Open: the one supervised live run** against the real CLI. As designed: Implement the stdio JSON-RPC surface ORACLE needs
   (`initialize`, `tools/list`, `tools/call`, `notifications/initialized`) and prove it against the
   **real** client — the installed Claude CLI, the only client that matters here — the same way the
   vendor stream contract was settled in P6-T1: record the exchange as fixtures. If the real client
   rejects it, take the SDK and give it a TECH_STACK ledger line with this measurement as the
   justification. Either outcome is a result; guessing is not.
2. ~~**Scoped, expiring delegation tokens.**~~ **DONE 2026-08-24** (`mcp/tokens.py`; HMAC, process-lifetime key, revoked on every exit path). As designed: Minted by `DelegationService` per delegation, carrying:
   the task id, the worktree path, the tool allowlist, an expiry. Verified in the daemon on every
   call. Not a bearer token for the whole API — a capability for one delegation.
3. ~~**`POST /api/v1/mcp/call`**~~ **DONE 2026-08-24** (`mcp/calls.py` + the endpoint; T2+ refused with a message telling the delegate to ask in its result). As designed: in the daemon: token → verify → **the same `ToolExecutor.execute`**
   every other path uses. No bypass, no second gate, no separate approval flow. T2+ tools are
   **refused** rather than prompting: an unattended delegate must not be able to raise a
   confirmation dialog at the owner (that is prompt fatigue as a service).
4. ~~**The exposed surface is a deliberate subset**~~ **DONE 2026-08-24** (`DEFAULT_TOOLS`; a test asserts every lent tool is T1 or below). As designed:, not the whole registry: `fs.read`, `fs.list`,
   `git.status`, `git.diff`, `know.search`, `dev.run_tests`. Read and verify, scoped to the
   delegation's worktree. Writes stay with the delegate's own tools inside the disposable worktree —
   ORACLE does not need to mediate an edit it is going to diff anyway.
5. ~~**Wire it into the delegation.**~~ **DONE 2026-08-24** (`--mcp-config` written beside the packet with the token inside and deleted on every exit; `mcp_server_errors` now ends the run). As designed: `ClaudeCodeAdapter` gains `--mcp-config` (written per run, token
   inside, deleted after) and `--strict-mcp-config`; `--allowedTools` narrows to the MCP tools plus
   the delegate's own edit tools. `mcp_server_errors` in `system/init` **fails the run** rather than
   letting it degrade into raw shell use — INTEGRATIONS.md §4 says so and nothing asserts it yet.
6. ~~**Every delegate tool call is visible.**~~ **DONE 2026-08-24** (`tool.started`/`tool.finished` with `task_id` and `actor="delegate"`; audit trail asserted through the real app). As designed: `tool.started` / `tool.finished` on the event log with
   `task_id` set and an actor that says it was the delegate, so the UI's existing tool cards show
   them under the delegation. The audit log entry names the delegate too — "who did this" must not
   become ambiguous the moment a second agent exists.

## Constraints

- **One policy engine.** The bridge holds no rules. If a call cannot be evaluated by the daemon it
  fails closed.
- **A delegation token grants strictly less than the owner has**: only the listed tools, only within
  the worktree, only until expiry, only while that delegation is running.
- **T2+ is refused, never prompted**, over MCP. Approval belongs to the human-facing path.
- No new dependency unless requirement 1 proves one is needed; then it gets a ledger line first.
- `tests/security/` grows: token forgery, expiry, path escape from the worktree, tier refusal,
  tool-not-in-allowlist, and use-after-delegation-ends.
- Tool count unchanged: MCP re-exposes registry tools, it does not add any.

## Acceptance criteria

- [ ] The transport question is settled by a **recorded exchange with the real CLI**, and whichever
      way it went is written into TECH_STACK.md and INTEGRATIONS.md §4 with the measurement.
      **Half done:** the SDK-vs-hand-rolled measurement is recorded in both docs and the surface is
      pinned by raw-frame tests plus a real-subprocess spawn; the live CLI recording is the one
      remaining step, and it needs the owner's go-ahead like every egress.
- [x] A delegated run calls back through MCP and the call appears in ORACLE's audit log and event
      log — asserted end to end, not by inspection.
- [x] Security suite: a forged token, an expired token, a token used after its delegation finished,
      a path outside the worktree, a tool outside the allowlist, and a T2 tool each fail closed —
      six tests, each asserting the call never reached the executor.
- [x] `mcp_server_errors` in `system/init` fails the delegation with a clear reason.
- [x] The MCP config file (with its token) is deleted when the delegation ends, including on HALT.
- [x] The gate green including the security suite — `check: OK` 2026-08-24 after the MCP work.
- [ ] *(Carry-over)* bge-m3 recorded in OQ-02, decision to the owner.

## Relevant files

New: `src/oracle/mcp/` (bridge + tokens) · `tests/security/test_mcp_tokens.py` ·
`tests/test_mcp_server.py` · `tests/fixtures/mcp/` (recorded exchange).
Modify: `src/oracle/api/app.py` (the call endpoint) · `src/oracle/delegation/service.py` (mint,
write config, revoke) · `src/oracle/integrations/claude.py` (`--mcp-config`, init errors) ·
`docs/INTEGRATIONS.md` §4 · `docs/TECH_STACK.md` §9 · `docs/API.md`.
Read first: [INTEGRATIONS.md §4](INTEGRATIONS.md#4-oracle-as-an-mcp-server) ·
[SECURITY.md](SECURITY.md) (scopes, tiers, audit) · `tools/executor.py` (the one execution path).

## Dependencies

P6-T2 (built). The installed Claude CLI for requirement 1's recording — one supervised live run,
payload previewed first, exactly as P6-T1 did it.

## Risks

| Risk | Mitigation |
|---|---|
| Hand-rolled protocol drifts from what the client sends | Requirement 1: record the real exchange as fixtures; the fixtures are the contract, and they re-run in CI |
| The bridge grows policy | It has no engine and no registry; it forwards. Asserted by the security suite, which drives the bridge with a stub daemon and shows refusals come from the daemon |
| A leaked token outlives its delegation | Scoped + expiring + revoked on finish; "use after end" is one of the six security tests |
| A delegate prompts the owner into fatigue | T2+ refused over MCP, never queued as an approval |

## Definition of done

All acceptance criteria · the transport decision recorded in TECH_STACK.md and INTEGRATIONS.md §4 ·
the security suite green · `current_report.md` overwritten · this file updated to **P6-T4** (the
phase capstone: the router's delegate signal and the reference scenario end to end).
