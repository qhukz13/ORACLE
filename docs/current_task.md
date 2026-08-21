# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P3-T1 — Process isolation, then PC & dev control tools**

**Phase:** [3 — PC & dev control tools](ROADMAP.md#phase-3--pc--dev-control-tools--mvp) · **Scope:** MVP
**Status:** `NOT STARTED` · **Set:** 2026-08-21
**Previous task:** P2-T1 policy gate — `MOSTLY DONE`, see [current_report.md](current_report.md)

---

## Objective

Build `oracle-toolhost` as a real separate process, **then** the tools that make ORACLE useful: git,
tests, files, apps, terminal.

## Why the ordering is not negotiable

P2 deferred process isolation on the argument that ADR-0003's justifications don't bite for read-only
file tools. **They all bite the moment a tool spawns a process**, which is the first thing this task
does:

- a hung `npm install` must not take down the agent, the UI and the event stream;
- `git` and `npm` must not run in an address space holding `ANTHROPIC_API_KEY`;
- killing a thread does not kill `npm install`'s grandchildren — only a Job Object does, and HALT's
  credibility depends on it.

**Do not add a single process-spawning tool before the toolhost exists.** That is the same rule that
put the policy gate before write tools, applied one level down.

## Context

P2 delivered: the path canonicaliser (12 steps, TOCTOU recheck, junction-aware), the policy gate
(fail-closed, deny-by-default, taint escalation, HALT), the hash-chained audit log, tool
contracts/registry, and five read-only tools — 103 security tests.

Established and not to be re-derived:
- **`Path.is_symlink()` is False for junctions.** Use the reparse-point attribute
  ([OQ-04](OPEN_QUESTIONS.md#oq-04)).
- **Pin every program to an absolute path at startup**, never resolve via `PATH` at call time.
- **Never call blocking `subprocess` from an async handler** — use `asyncio.create_subprocess_exec`.
  ruff `ASYNC221` enforces it.
- Constraints in tool-argument schemas must be **decoder-enforceable**
  ([ADR-0017](DECISIONS.md#adr-0017--constrain-what-the-decoder-can-enforce)).
- Tests stay hermetic (`Settings.llm_enabled=False`); the security suite is a merge gate.

## Requirements

1. **`oracle-toolhost` as a separate process.** JSON-RPC over a pipe; a Windows **Job Object** with
   `KILL_ON_JOB_CLOSE` around the whole tree (the pattern already proven in
   `apps/desktop/src-tauri/src/backend.rs` for the sidecar); per-call timeouts; argv lists only,
   never `shell=True`; a constructed environment, never inherited `os.environ`.
2. **Supervision**: the runtime restarts a crashed toolhost; the in-flight step is marked `failed`
   and is **never silently retried if it had side effects**.
3. **Undo journal + trash.** `fs.write` backs up first; `fs.delete` moves to trash, never unlinks.
4. `fs.write`, `fs.patch`, `fs.move`.
5. `git.*`: status, diff, log, add, commit (undo `reset --soft HEAD~1`), branch, stash, push (T2).
6. `dev.run_tests` with **structured** results (pytest/vitest/jest/cargo autodetect), `dev.build`,
   `dev.lint`, `dev.execute` (allowlisted program + argv).
7. `app.launch` via `config/apps.yaml` aliases; `sys.processes` already exists.
8. `term.*` via `pywinpty` ([OQ-09](OPEN_QUESTIONS.md#oq-09)); `term.write` confirmed every time.
9. Project registry upgrade: detect type, test/build commands, read `AGENTS.md`/`CLAUDE.md`.
10. **Tool selection in the router** — the agent can finally act. Feed only intent-filtered tool
    schemas into the context budget (`registry.for_intent`), which is already load-bearing for latency.
11. Approval issuance + the `approval.requested` / `approval.resolved` event round trip.

## Constraints

- **No tool that spawns a process before the toolhost exists.**
- No `shell=True`, no `os.system`, no string-built commands.
- Every new tool: contract, policy rule in `config/policy.yaml`, and a `tests/security/` case.
- The 40-tool cap is real; `MAX_TOOLS` will start refusing registrations.
- The gate stays green, security suite included.

## Acceptance criteria

- [ ] Killing the toolhost mid-call leaves the runtime healthy; the step is marked `failed`.
- [ ] HALT terminates a `ping -t` process tree within 2 s from a cold hotkey press.
- [ ] "commit my changes in Asterim with message X" works end to end and is undoable.
- [ ] "run the Asterim tests" returns structured pass/fail counts, not scraped text.
- [ ] `git.push` prompts, and approving executes **exactly** the previewed argv.
- [ ] A soak test of 100 tool calls leaves **zero** orphaned processes (verified by enumeration).
- [ ] A tool whose program is not on the allowlist is refused, naming the rule.
- [ ] `grep -r "shell=True"` returns nothing; lint enforces it.
- [ ] Project detection correctly classifies all seven projects in `C:\Projects`.

## Relevant files

Create: `src/oracle/toolhost/` (process, job objects, RPC) · `src/oracle/tools/{fs,git,dev,term,app}.py`
· `src/oracle/tools/undo.py` · `config/apps.yaml`
Modify: `src/oracle/tools/executor.py` (dispatch via toolhost) · `src/oracle/router/pipeline.py`
(tool selection) · `config/policy.yaml`
Read first: [ARCHITECTURE.md §3](ARCHITECTURE.md#3-process-model) · [TOOLS.md](TOOLS.md) ·
[SECURITY.md §4b](SECURITY.md#4b-command-safety) ·
[`backend.rs`](../apps/desktop/src-tauri/src/backend.rs) for the working Job Object pattern

## Dependencies

P2-T1 (done). [OQ-09](OPEN_QUESTIONS.md#oq-09) (`pywinpty` on 3.12, ConPTY resize/encoding) must be
answered before `term.*`.

## Risks

| Risk | Mitigation |
|---|---|
| **Orphaned process trees** — the failure HALT exists to prevent | Job Object with `KILL_ON_JOB_CLOSE`; the soak test asserting zero orphans is the real check, not the happy path |
| Test-runner output parsing is fragile | Prefer machine-readable output (`--json`, `--junit-xml`); treat scraping as a fallback and say so in the result |
| IPC overhead makes tools feel slow | Budget < 50 ms. For scale: the router already costs ~1.5 s, so 50 ms is not the bottleneck |
| Tool count blows past the cap and degrades routing | `MAX_TOOLS` refuses at 40; merge before adding |
| ConPTY encoding mojibake on a Russian-locale Windows | Spike it early ([OQ-09](OPEN_QUESTIONS.md#oq-09)) with Cyrillic output and a mid-stream resize |

## Definition of done

All acceptance criteria pass · security suite green and extended for every new tool ·
ADR-0003 confirmed against the real implementation ·
[OQ-09](OPEN_QUESTIONS.md#oq-09) resolved and recorded ·
`current_report.md` overwritten · this file updated to **P4-T1**.
