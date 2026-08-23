# 2026-08-24 — P6-T2: the egress preview is an approval, and the gate is a test

Requirements 1-7 of [P6-T2](../../docs/current_task.md), in one continuous build on top of the
previous night's core. 22 more tests (11 python, 15 vitest cases minus overlap), gate green.

## The two decisions worth an ADR paragraph each

**Delegation runs in the daemon, not the toolhost.** A delegation is minutes long and owns a child
process; a toolhost invocation is neither (ADR-0003's boundary is for tools). The gate still prices
the action under `ai.delegate` — the policy entry Phase 2 declared — so the tier, the taint
escalation and the audit trail are the ordinary ones. `DelegationService` sits on `AppState` like
`TerminalBridge`, is spawned per-run through `AppState.spawn` so HALT reaches it, and turns that
cancellation into `adapter.cancel()` — asserted by watching the child's exit code, not by trusting
the coroutine.

**The approval digest binds the rendered packet bytes.** Not the arguments that produced the packet:
sha256 over the files on disk, recomputed after the human answers. The security suite plants a
mutation between preview and approval and the run refuses with `submits == 0`. This is what makes
the preview a preview rather than a description — approving a packet does not approve a different
packet that later occupies the same directory.

## What landed where

| | |
|---|---|
| `delegation/service.py` | render → preflight → gate → approval → digest re-check → worktree → stream → collect → verify. Every blocking step off the event loop (`to_thread`) — the P5-T2 watcher lesson, applied before measurement this time. |
| `tests/security/test_egress_gate.py` | The phase's headline criterion, six ways: refused, expired, HALT-before-asking, HALT-during-preview, mutated packet, taint→T3. `SpyAdapter` counts `submit()` at the seam. |
| `core/events.py` | `delegate.event` (coalescable) joins the vocabulary; `task_id` joins the wire envelope — it existed on the model and never reached clients. |
| `api/app.py` | `delegate` / `delegate.discard` WS commands (with a `..`-traversal check on the project name), `_curate` (docs + retrieval + git state, degradable), `_worktree_verifier` (`dev.run_tests` through the executor). |
| `handoff/gather.py` | §6 steps 3-5: orientation docs above retrieval hits in eviction order; `gather_retrieval` returns text and taint in one value so forgetting the taint is a type error. |
| UI | `EgressPreview` inside the confirmation card for `ai.delegate` (destination, files, tokens, redaction occurrences, dropped-excerpt count, tainted sources); `DelegationPanel` folding `task.*`/`delegate.event`; store cases with replay-idempotence and a bounded feed. |

## Recorded gaps, deliberate

- No UI affordance **starts** a delegation; the WS command is the entry point until the router's
  complexity signal (§8 step 7) lands in the phase capstone.
- "Edit selection" from the §6 preview mock is out of scope; the card says so and offers
  deny-adjust-retry.
- `ai.monitor` / `ai.cancel` as model-visible tools are unbuilt; HALT and the panel cover the human
  paths. Tool count unchanged at 33/40 — `ai.delegate` is a policy entry, not a registry tool.

## Next

The phase capstone: ORACLE's MCP server (INTEGRATIONS.md §4), the router's delegate signal, and the
reference scenario (§8) start to finish. Plus the carry-over: the bge-m3 scheduled run's numbers
into OQ-02.
