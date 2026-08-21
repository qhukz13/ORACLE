# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P2-T1 — Tool system and the policy gate
**Status:** `MOSTLY DONE` — gate built and proven; process isolation deliberately deferred to Phase 3
**Date:** 2026-08-21

---

## What was done

The policy gate exists and works. ORACLE can now execute a read-only tool set against real projects,
and every path, tier, taint flag and denial passes through one chokepoint that is tested against
adversarial input. **No write tools** — that is Phase 3, and the roadmap forbids them before this.

`103 security tests` + 89 unit/API tests + 14 TS. `uv run python scripts/check.py` green, with the
security suite as its own gate step.

## Verified live, against the real `config/policy.yaml` and real projects

```
ALLOW  fs.read    C:/Projects/ORACLE/README.md            3ms   rule=tools.fs.read.tier
ALLOW  fs.list    C:/Projects/Asterim                   125ms   rule=tools.fs.list.tier
DENY   fs.read    C:/Windows/win.ini                    denied  path.outside_scope
DENY   fs.read    C:/Projects/Asterim/.env              denied  path.denied
DENY   fs.read    README.md:hidden           (ADS)      denied  path.alternate_data_stream
DENY   fs.read    //localhost/C$/Windows/... (UNC)      denied  path.device_path
DENY   fs.read    ../../Windows/win.ini      (traversal)denied  path.outside_scope
DENY   git.push   (not registered in Phase 2)           not_found
```

HALT over the WebSocket: `halted=true` with the reason recorded, a turn attempted while halted
returns `outcome='halted'`, and only an explicit `resume` clears it. Audit chain: **8 records,
intact**, every denial carrying the rule that fired.

## OQ-04 resolved — and it found the bug that would have shipped

Tested against a **real** `mklink /J` tree, not mocks
([write-up](../logs/development/2026-08-21-oq04-windows-paths.md)).

1. **`Path.is_symlink()` returns `False` for a junction.** The natural optimisation — "only resolve
   if it's a link" — walks straight past every junction. Detection must use the reparse-point
   attribute. A mocked fixture would have encoded my wrong assumption and passed.
2. **Junctions need no admin; symlinks do.** Developer Mode is off here, so the *realistic* attacker
   can make junctions but not symlinks. The suite treats junction tests as required and symlink tests
   as skippable — the right priority.
3. **`realpath` does not strip an alternate data stream.** `normal.txt:hidden` hides a payload in a
   file whose size never changes.
4. **UNC/device paths pass through `realpath` unchanged**, and must be rejected *before* the wildcard
   check — `\\?\` contains `?` and `C$` contains `$`.

Plus: Windows silently strips trailing dots, so `.env.` opens `.env`. Deny rules are matched **after**
resolution, never against the raw string.

## Bugs my own tooling caught in my own code

- **An ADS regex that rejected every absolute path.** `^(?:[A-Za-z]:)?[^:]*:(.*)$` looks correct, but
  the optional group backtracks and matches the drive-letter colon. 20 tests failed at once. Replaced
  with an explicit slice. *Regex is a poor default for security predicates.*
- **`subprocess.run` inside an async tool handler** — blocks the whole event loop, including every
  other client's event stream. Caught by ruff `ASYNC221`, fixed with `asyncio.create_subprocess_exec`.
- **`tasklist` resolved via `PATH`** — exactly the current-directory hijack SECURITY.md §4b warns
  about. Now pinned to an absolute path under `%SystemRoot%` at import, refusing anything else.
- **A test asserting `....//` is traversal.** It is an ordinary directory name on Win32. Asserting
  rejection would have baked a false belief into the suite; the correct assertion is *no escape*.

## What was built

```
src/oracle/policy/
  paths.py     canonicaliser: 12-step algorithm, TOCTOU recheck, component containment
  model.py     capabilities, tiers T0-T4, decisions, provenance
  engine.py    THE GATE: fail-closed loading, deny-by-default, taint escalation, HALT
  audit.py     hash-chained append-only log, fsynced, redacted
src/oracle/tools/
  contract.py  contracts + registry, validated at startup (40-tool cap)
  readonly.py  fs.read · fs.list · fs.stat · sys.info · sys.processes
  executor.py  validate -> resolve -> GATE -> approval -> recheck -> execute -> audit
config/policy.yaml           scopes, deny_always, per-tool tiers (Phase 3+ declared early)
scripts/audit.py             `verify` / `tail`
tests/security/              103 tests, incl. Hypothesis over 300 adversarial paths
```

## Deliberately NOT done — process isolation

**`oracle-toolhost` is still in-process.** ADR-0003 wants a separate process; I did not build it.

The reasoning, so it can be argued with: ADR-0003's three justifications are (a) a crashing tool must
not take down the agent, (b) a tool must not be able to read `ANTHROPIC_API_KEY`, (c) killing a
thread does not kill `npm install`'s grandchildren. **None bite for read-only file tools. All three
bite hard the moment Phase 3 adds `dev.execute`, `git` and `npm`.**

So it is sequencing, not omission — but it is a **hard prerequisite for the first Phase 3 tool that
spawns a process**, and the acceptance criterion *"killing the toolhost mid-call leaves the runtime
healthy"* moves with it. Recorded in [ROADMAP Phase 2](ROADMAP.md#phase-2--tool-system--policy-gate--mvp).

## Other gaps

- **The agent cannot yet call tools.** The executor is wired into the app and tested, but the router
  does not select tools — the pipeline still says "tools arrive in Phase 2" for actionable intents.
  Tool *selection* is Phase 3 work; the gate had to exist first.
- **Approvals have no UI.** `Approval` objects are bound, expiring and single-use, and the executor
  enforces them, but nothing issues one yet — the Confirmation Center is Phase 4.
- **No undo journal.** Not needed while everything is read-only; required with the first `fs.write`.
- **`sys.info` reports `cpu_percent` as 0.0** — a placeholder; it needs a sampling interval and
  should either be implemented properly or dropped from the result.

## Recommended next action

**[P3-T1](current_task.md) — but build `oracle-toolhost` first.** Do not add a process-spawning tool
until the Job Object isolation exists; that ordering is the whole reason Phase 2 came before Phase 3.

```
uv run python scripts/check.py        # gate, incl. security suite
uv run python scripts/audit.py verify # audit chain
uv run oracled                        # backend
```
