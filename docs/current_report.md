# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P6-T5 — the Antigravity adapter, and the planning spike. **Done: all six acceptance
criteria, with one answered honestly rather than favourably.**
**Status:** Adapter built and fixture-tested (15 tests, `make check`) · OQ-20 **answered NO**
(75% vs a 90% gate) · the fallback ladder promoted: **Claude is Phase 8's default planner** ·
one plan-authored task executed end to end and verified by ORACLE.
**Date:** 2026-08-24

---

## The headline

**The spike's central question came back "no", and that is the result it was designed to be able to
produce.** Antigravity returned a valid `ExecutionPlan` on 12 of 16 supervised calls — **75%
against a 90% gate** — so [PLANNER.md §6](PLANNER.md#6-fallbacks)'s ladder promoted. Claude authors
plans now; Antigravity keeps `reviewer` and `researcher`. The change was **one line of the
capability registry**, which is the whole reason the ladder was designed before the spike ran.

The findings are worth more than the verdict, and one of them is not about planning at all.

## What was built

- **`AntigravityAdapter`** (`src/oracle/integrations/antigravity.py`) behind the same
  `ExternalAgentAdapter` seam as Claude: pinned invocation, envelope normalisation, three-state
  preflight, SIGINT-first cancellation, structured collection.
- **Four recorded fixtures** (`tests/fixtures/agents/antigravity/`, `agy` v1.1.19) + a stub CLI +
  **15 contract tests** that run offline and deterministically in `make check`.
- **Three scripts**: `record_agy_stream.py` (the contract recorder, egress-previewed),
  `verify_agy_planning.py` (the resumable measurement harness), `verify_plan_task_live.py`
  (one plan-authored task through the real delegation lifecycle).

Full analysis, every number, and every dead end:
[`logs/development/2026-08-24-p6t5-antigravity-planning.md`](../logs/development/2026-08-24-p6t5-antigravity-planning.md).

## The eight findings

1. **Headless `agy` is read-only.** Without `--dangerously-skip-permissions` (which ORACLE
   refuses), `view_file` runs and `write_to_file`/`run_command` are soft-denied — the run ends
   `ERROR`, exit 1. So **Antigravity can hold `planner`, `reviewer`, `researcher`; never `coder`.**
   That happens to match every role the registry gave it.
2. **Cancellation, timed to the millisecond.** `CTRL_BREAK` → terminal `result` 110 ms later →
   exit 1, nothing left on disk, interrupt alone sufficient. But the status is `ERROR` with
   *"timeout waiting for response"* — never the documented `CANCELED`. **A cancelled run is
   indistinguishable from a vendor timeout in the stream**; only the supervisor's own record
   separates them.
3. **`--json-schema` works**, `$defs`/`$ref` included; `structured_output` arrives pre-filtered to
   the schema. Read that field, never the prose beside it.
4. **The planner browses, and only the permission gate stops it.** All three hard planning failures
   were `--effort high` runs that tried to read **`C:\Users\qhukz`** — the owner's home directory —
   from an empty workspace. The vendor's gate denied them, and that gate is in the picture *only*
   because ORACLE refuses the skip-permissions flag Asterim passes. Under that flag those calls
   would have read the owner's home directory and sent what they found to the vendor. Recorded in
   [SECURITY.md §10](SECURITY.md#10-the-multi-agent-surface--added-2026-08-24-phases-78); it is the
   most valuable thing the spike produced.
5. **`structured_output` can be silently emptied.** One `SUCCESS` returned a schema-valid plan with
   `tasks: []` while the raw response held six well-formed tasks — the vendor's filter drops
   non-conforming items without saying so. PLANNER.md §2 gained **check 0: non-empty**.
6. **Valid ≠ schedulable.** Only 7 of 12 valid plans declared *any* dependency; five were DAGs with
   no edges. Acceptance criteria named files that do not exist. Plan criteria are a hint for the
   worker, never the verification contract.
7. **Verification is a delta, not a threshold.** A *pristine* worktree of this repo fails 28 tests
   for environment reasons; the delegate's worktree failed the same 28 and passed 5 more. A VERIFY
   task reading "failures > 0" would reject every correct delegation. (The first version of the
   verifier also printed a green line for tests that never ran — fixed, and recorded, because it
   was the exact false-green this project exists to avoid.)
8. **A delegation's result exists only while its worktree does.** Delegates are forbidden git
   commands, so the diff is uncommitted; `discard()` deletes it. Correct for one delegation, a hole
   for a graph. **P7 owes a harvest step** — and this run's own artifact was lost proving it.

Plus one about process: **the CLI self-updated mid-session** (v1.1.17 → v1.1.19, forty minutes
apart). The first fixture set was discarded and re-recorded. "Re-verify quarterly" is a floor.

## What could not be measured

`preflight()` distinguishes binary-missing · unauthenticated · ready, and **only two of the three
were observed for real**. Hiding `HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA`/`XDG_CONFIG_HOME`
did not deauthenticate `agy`; its credentials live elsewhere (the Antigravity IDE was running — a
hypothesis, untested). The unauthenticated branch is written from vendor documentation and marked
`ASSUMPTION` in the adapter. **Observing it needs a sign-out or a second machine — an owner
decision, not something to infer from a green preflight.**

## The end-to-end proof

One `role: coder` task from a real plan — "surface `permission_denials` in the Claude adapter" —
ran through the untouched delegation lifecycle: packet rendered (6 files, 632 tokens, 0
redactions) → T2 approval, nothing egressing before it → scrubbed worktree → Claude → collect →
**ORACLE's own diff and test run**. 87 diff lines, the delegate's structured claim matching the
evidence, no regression against a baseline worktree. A plan authored by one vendor, executed by
another, verified by neither. The seam is real.

## Cost

~1.2M Antigravity tokens across 20 live calls (55k median per plan, 27s at low effort / 43s at
high) plus one Claude delegation of ~7.5 minutes. The grid was trimmed from 24 calls to 16 by the
owner after the pilot showed the per-call cost was 4× the estimate — so **OQ-20 is narrowed with
numbers, not closed at the ≥ 20 the task specified**, and the docs say so rather than rounding up.

## Next

**P7-T1** ([current_task.md](current_task.md)): the task graph — model, `tasks` migration, Asterim's
DAG algebra, and a scheduler running fake work. No planner, no vendor, fully deterministic. It
carries six of this spike's findings in as design inputs, including the two that came from things
going wrong (verification baselines, and the harvest step).

## Unresolved

[OQ-18](OPEN_QUESTIONS.md#oq-18) (recall 61% vs an 80% gate — Phase 9) ·
[OQ-19](OPEN_QUESTIONS.md#oq-19) (Agent SDK, trigger-based) ·
[OQ-21](OPEN_QUESTIONS.md#oq-21) (MCP spec migration, watch) · `agy`'s unauthenticated preflight
state (above) · whether `agy` works at all with the Antigravity IDE closed — never tested, and it
would change what `preflight()` means.
