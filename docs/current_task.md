# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P2-T1 — Tool system and the policy gate**

**Phase:** [2 — Tool system + policy gate](ROADMAP.md#phase-2--tool-system--policy-gate--mvp) · **Scope:** MVP
**Status:** `NOT STARTED` · **Set:** 2026-08-21
**Previous task:** P1-T1 router — `DONE` (93.3% intent accuracy), see [current_report.md](current_report.md)

---

## Objective

Build the full capability / policy / isolated-execution machinery, and prove it with **read-only
tools only**.

## Why this task exists

This is the phase that makes ORACLE safe to keep building. Every security control lands here, before
a single write tool exists. Retrofitting a policy engine after tools exist is exactly how this class
of project ends up as an unrestricted shell wrapper — and the roadmap's second sequencing rule
forbids any write tool before this is done.

## Context

P0 gave the transport, event log and shell. P1 gave a router that classifies intent at 93.3% and a
pipeline that *says* what the user wants but cannot act. The `_PENDING` branch in
[`src/oracle/router/pipeline.py`](../src/oracle/router/pipeline.py) is the seam where tools plug in.

Established and not to be re-derived:
- Router: `qwen3.5:0.8b`, num_ctx 16384, **`think: false`**.
- **Constrain only what the decoder enforces** ([ADR-0017](DECISIONS.md#adr-0017--constrain-what-the-decoder-can-enforce)) —
  enums and required fields, never `minimum`/`pattern`. This applies directly to tool-argument schemas.
- Context budget is per call type; `ContextAssembler` already carries `provenance` for taint.
- Tests must stay hermetic (`Settings.llm_enabled=False`); no test may require Ollama.

## Requirements

1. **Resolve [OQ-04](OPEN_QUESTIONS.md#oq-04) first.** Does `os.path.realpath` fully resolve Windows
   junctions and mount points? Build the fixture tree (symlink + junction + mount point) — it is
   needed for the security suite regardless of the answer. If `realpath` falls short, use
   `GetFinalPathNameByHandleW` via `ctypes`.
2. **Path canonicaliser** implementing all 12 steps of
   [SECURITY.md §4](SECURITY.md#4-path-safety-windows-specific), including the TOCTOU re-check.
3. **Tool registry + contract decorator** ([TOOLS.md §2](TOOLS.md#2-tool-contract)); startup
   validation; JSON Schema generation. Resolved types `ScopedPath`, `ProjectRef`, `ProgramRef` — a
   bare `str` path in a tool signature is a review rejection.
4. **Policy engine**: `config/policy.yaml`, scopes, capabilities, risk tiers, `deny_always`, and
   **fail-closed loading** (unparseable policy → read-only, loudly).
5. **`oracle-toolhost` as a separate process**: JSON-RPC over pipe, Windows Job Object, timeouts,
   argv-only. It receives a pre-authorised `ToolInvocation` and nothing else.
6. **Approvals**: `arg_hash` binding, expiry, single-use, re-check immediately before execution.
7. **Taint tracking**: provenance → tier escalation; `Assembled.tainted` already exists.
8. **Hash-chained audit log** + `oracle audit verify`.
9. **HALT**: API → runtime → job-object termination → deny-all → manual resume. The P0 stub in
   `app.py` becomes real.
10. **Read-only tools only**: `fs.read`, `fs.list`, `sys.info`, `sys.processes`, `oracle.*`.
11. **`tests/security/`** — the red-team suite listed in [TESTING.md §3](TESTING.md#3-security-tests-are-a-merge-gate),
    wired into `scripts/check.py` as a **merge gate**.

## Constraints

- **No write tools, no `fs.write`, no `git.commit`.** Those are P3, after the gate is proven.
- **No `shell=True`, no `os.system`, no string-built commands.** Enforced by lint and a security test.
- The toolhost must not be able to read policy, secrets, or re-enter the runtime.
- The gate stays green; `mypy --strict` covers `src/oracle`.

## Acceptance criteria

- [ ] Every red-team case is **denied**, and each denial names the rule that fired.
- [ ] A corrupt or missing `policy.yaml` yields read-only mode, loudly — never open access.
- [ ] Killing the toolhost mid-call leaves the runtime healthy; the step is marked `failed`.
- [ ] HALT terminates a `ping -t` process tree within 2 s from a cold hotkey press.
- [ ] Tampering with one audit line makes `oracle audit verify` fail.
- [ ] An approval issued for args A cannot execute args B.
- [ ] `grep -r "shell=True"` returns nothing; a lint rule enforces it.
- [ ] Tool-argument schemas use only decoder-enforceable constraints (ADR-0017).
- [ ] Security suite is part of `scripts/check.py` and green.

## Relevant files

Create: `src/oracle/tools/` (registry, contracts, types) · `src/oracle/policy/` (engine, scopes,
tiers, taint) · `src/oracle/toolhost/` (separate process, job objects) · `config/policy.yaml` ·
`tests/security/`
Modify: `src/oracle/router/pipeline.py` (the `_PENDING` branch) · `src/oracle/api/app.py` (real HALT)
Read first: [SECURITY.md](SECURITY.md) · [TOOLS.md](TOOLS.md) ·
[ARCHITECTURE.md §3](ARCHITECTURE.md#3-process-model) · [TESTING.md §3](TESTING.md#3-security-tests-are-a-merge-gate)

## Dependencies

P1-T1 (done). [OQ-04](OPEN_QUESTIONS.md#oq-04) must be answered inside this task, before the
canonicaliser is built on top of an assumption.

## Risks

| Risk | Mitigation |
|---|---|
| **Windows path edge cases are genuinely hard** — junctions, ADS, 8.3 names, UNC | Property tests (`hypothesis`) over generated adversarial paths, plus a **real** fixture tree with actual symlinks and junctions. A mock encodes the bug you are hunting. |
| IPC overhead makes tools feel slow | Measure; budget < 50 ms per call. Note the router already costs ~1.5 s, so 50 ms is not the bottleneck. |
| Policy engine becomes a mini programming language | Policy is **data**, evaluated by a small deterministic engine. If it needs branching, the design is wrong. |
| Security suite lags the tools | Suite is a merge gate **from this phase on** — a tool without a security test does not merge. |

## Definition of done

All acceptance criteria pass · security suite wired into `scripts/check.py` and green ·
[OQ-04](OPEN_QUESTIONS.md#oq-04) resolved and recorded in `logs/development/` ·
ADR-0003 and ADR-0005 confirmed against the implementation ·
`current_report.md` overwritten · this file updated to **P3-T1**.
