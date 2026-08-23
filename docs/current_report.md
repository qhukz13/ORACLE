# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P6-T4 — the phase capstone. Requirements 1–4 and 6 done; **one supervised live run is all
that is left** in Phase 6.
**Status:** Delegation is complete end to end and provable offline. Gate green.
**Date:** 2026-08-24

---

## Phase 6, in one table

| Task | What it built |
|---|---|
| **P6-T1** | The core, offline: adapter against a *recorded* vendor contract, packet renderer (redaction asserted per file, 30k ceiling that raises), worktree + scrub, fallback routing on preflight. |
| **P6-T2** | The egress preview as a real `ApprovalStore` approval whose digest binds the rendered packet **bytes**; live progress on the event log; verified collection (diff + gate-run tests); the UI's egress card and delegation panel. |
| **P6-T3** | ORACLE's MCP server — the delegate calls back through *one* gate into *one* audit log. Capability tokens, six asserted refusals, no new dependency. |
| **P6-T4** | ORACLE decides for itself: escalation on a failure signature, the explicit "ask Claude to…" route, and INTEGRATIONS.md §8's reference scenario as a test that runs offline in CI. |

## What the last stretch changed in the design

- **Step 7 of the reference scenario needs no model.** The escalation signal is a *fact* about the
  turn — a verification tool reported failure — not a judgement. Deterministic is cheaper and more
  reliable, and a model deciding to spend money on a stronger model is a loop nobody asked for.
- **One prompt, not two.** Escalation *starts* the delegation; the egress preview is where a human
  says no. A refused delegation costs a packet render and nothing else.
- **A stub had outlived its phase.** "ask Claude to …" never reached the classifier — the
  pre-router has matched it since P1 and was still answering "Delegation arrives in Phase 6". That
  route is now ~5 ms and no model call.
- **Two Windows defects, found by spawning the real process** (P6-T3): `connect_read_pipe(sys.stdin)`
  is broken under `ProactorEventLoop`, and a child's stdout is the console codepage rather than
  UTF-8. Either would have made the MCP server useless on this machine while every unit test stayed
  green.

## What is left

1. **One supervised live run.** `uv run python scripts/verify_mcp_live.py` (`--dry-run` shows the
   payload and sends nothing). It closes P6-T3 requirement 1 *and* P6-T4 requirement 5: real CLI,
   real bridge, real token, and the audit log read afterwards to prove the delegate's call went
   through the gate. Needs the owner's go-ahead, like every egress.
2. **bge-m3** — the scheduled run still has not produced artefacts; it fires on next app launch and
   writes OQ-02 plus a dev log. Its commits stay separate.

Then Phase 6's Definition of Done is met apart from `AntigravityAdapter`, which OQ-05 unblocked but
which no task has scheduled — a deliberate gap to close or to record in ROADMAP.md as out of scope.

## Standing state

Branch `phase6-integration`, pushed (`52e23b1`); `main` sits at the end of Phase 5. Gate green:
ruff, mypy, tsc, pytest, security, vitest.

Logs: [capstone](../logs/development/2026-08-24-p6t4-capstone.md) ·
[MCP server](../logs/development/2026-08-24-p6t3-mcp-server.md) ·
[egress approval](../logs/development/2026-08-24-p6t2-egress-approval.md) ·
[adapter/packet/worktree](../logs/development/2026-08-24-p6t1-adapter-packet-worktree.md) ·
[auth contract](../logs/development/2026-08-23-claude-auth-contract.md)
