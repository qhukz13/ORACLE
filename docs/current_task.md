# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P6-T2 — Delegation you can approve, watch, and verify**

**Phase:** [6 — External agent integration](ROADMAP.md#phase-6--external-agent-integration--post-mvp) · **Scope:** Post-MVP
**Status:** `IN PROGRESS — requirements 1-7 done 2026-08-24; gate + carry-over open` · **Set:** 2026-08-24
**Previous task:** P6-T1 — **done** (all five requirements; see [current_report.md](current_report.md)).

---

## Carry-over — still the bge-m3 decision

Unchanged from P6-T1: the scheduled run (`bge-m3-full-corpus-run`, 2026-08-24 05:00 local; fires on
next app launch if it was missed) records its numbers in [OQ-02](OPEN_QUESTIONS.md#oq-02) and the
model choice goes to the owner. Nothing in this task depends on it.

## What P6-T1 built, so it is not rebuilt

The delegation *core*, offline: `ExternalAgentAdapter` + `ClaudeCodeAdapter` against a recorded
contract; the packet renderer with asserted redaction and a 30k ceiling; worktree + scrub (the
material replacement for `--bare`); `deliver()` routing on preflight. 22 tests, all replay/local.
What it deliberately did **not** build: any way for the owner to *see* an egress before it happens,
any live progress, and any independent verification of results. That is this task.

## Objective

Wire delegation into the daemon so that the §6/§8 flow actually happens in front of the owner:
**egress preview as a real approval** (through the existing `ApprovalStore` — not a second
mechanism), **live progress** over the event log, and **verified collection** (diff + an independent
`dev.run_tests` through the gate). The ROADMAP acceptance this task answers to: *"Nothing leaves
the machine without an approved egress preview — asserted by a test that fails if any egress path
skips the gate."*

## Requirements

1. ~~**`DelegationService`**~~ **DONE 2026-08-24** (`src/oracle/delegation/service.py`, wired into `AppState`; HALT-kills-child measured in `tests/test_delegation_service.py`). As designed: — the lifecycle owner, in the daemon. One delegation = one background
   task through `AppState.spawn`, so HALT already cancels it (and must also `cancel()` the child —
   assert the process dies). States over the event log: `task.created` →
   `task.updated` (`rendering` → `awaiting_egress` → `running` → `verifying`) → `task.finished`;
   `agent.state` reports `delegating` during the turn. Both `task.*` types are already in
   `CRITICAL_TYPES` — reserved for exactly this.
2. ~~**Egress preview = an approval.**~~ **DONE 2026-08-24** — rides `ApprovalStore` unchanged; digest = sha256 over the rendered packet files. As designed: The request goes through the existing `ApprovalStore` —
   digest binding, TTL, idempotent resolve, HALT-refuses-all, all inherited, no parallel mechanism.
   The digest binds the **rendered packet bytes**, so what the owner approves is what egresses,
   byte for byte; re-rendering after approval invalidates it. The preview payload is the §6 box:
   file list, token count, redaction labels with locations, allowed tools, the exact command, the
   destination (`api.anthropic.com`). Refused / expired / HALT → no submit, the packet stays on
   disk with the resolution recorded. "Edit selection" from the §6 mock is **out of scope** —
   approve/refuse only; note it in the UI copy.
3. ~~**The egress gate is asserted, not promised.**~~ **DONE 2026-08-24** — `tests/security/test_egress_gate.py`, six tests: refused, expired, HALT-before, HALT-during-preview, mutated packet, taint escalation. As designed: `submit()` on the live path must be unreachable
   without an approval bound to the current packet digest — enforced in the service seam and
   covered in `tests/security/`: a test that drives the full flow with the approval refused,
   expired, and never requested, and fails if a submit happens anyway. This is the phase's
   headline security criterion.
4. ~~**Progress streaming.**~~ **DONE 2026-08-24** (`delegate.event` in KNOWN_TYPES; `task_id` added to the wire envelope; replay asserted). As designed: Normalised `AgentEvent`s land on the event log as a new coalescable
   `delegate.event` type (add to `KNOWN_TYPES`; a dropped `thinking` is cosmetic), with state
   transitions and the final cost/session on the critical `task.*` events. The UI can replay a
   delegation from `since_seq` like everything else.
5. ~~**Verified collection.**~~ **DONE 2026-08-24** (diff + `dev.run_tests` through the executor; honest `{ran: false}` when no verifier; discard wired). As designed: On the delegate's finish: `wt.diff()` + untracked list, then
   `dev.run_tests` executed **through the policy gate** in the worktree — ORACLE's evidence, not
   the agent's claim. `task.finished` carries: diff stat, test verdict, structured result, cost,
   and the workspace path kept for inspection. Discard is wired (`wt.discard()`); merge/keep stay
   manual for now and say so.
6. ~~**Curation completes §6 steps 3–5.**~~ **DONE 2026-08-24** (`gather_project_docs` + `gather_retrieval` returning text and taint together; degradable in the daemon when index/model are absent). As designed: Packet excerpts from hybrid retrieval scoped to the target
   project (top hits for the task text, `local_foreign` sources marked), project docs
   (`AGENTS.md`/`CLAUDE.md`/`README`) when present, `gather_git_state`. Retrieved text enters
   CONTEXT.md as **attributed data**, never as instructions — the injection suite stays green
   unchanged.
7. ~~**UI: the Confirmation Center learns egress, and delegation gets a panel.**~~ **DONE 2026-08-24** (`EgressPreview` inside the confirmation card for `ai.delegate`; `DelegationPanel`; 15 new vitest cases). **Gap, recorded:** no UI affordance *starts* a delegation yet — the `delegate` WS command is the entry point until the router's complexity signal lands in the capstone. As designed: An `EgressPreview`
   rendering of the approval payload (files · tokens · redactions · destination · cost estimate);
   a delegation panel driven by `task.*`/`delegate.event` (state, live event feed, cost at the
   end, discard button). Built from events only — the UI computes nothing the server did not say.
   vitest coverage in the existing component-test style.

## Constraints

- **No second approval path.** If the egress preview cannot ride `ApprovalStore` semantics
  unchanged, that is a finding to record, not a reason to fork the mechanism.
- **No new dependencies** expected; any exception gets its TECH_STACK.md ledger line first.
- Tool count stays within the cap: if `agent.delegate` registers as a policy-visible entry it is
  34/40 — record it in TOOLS.md; the delegation itself runs in the daemon, not the toolhost
  (it is long-running and owns a child process — ADR territory, note it in the log).
- `tests/security/test_injection.py` passes unchanged, and the new egress tests join the suite.
- Retrieval taint propagates: a packet whose excerpts include `local_foreign` sources says so in
  the preview (the owner is approving tainted context knowingly).
- The scheduled bge-m3 run may land mid-task: its OQ-02/doc edits are separate commits, never
  mixed into delegation work.

## Acceptance criteria

- [x] A delegation started against the stub adapter reaches `task.finished` with diff stat, test
      verdict and cost — end to end under the daemon, asserted in an integration test.
- [x] The egress-gate security test: refused / expired / never-requested approvals each provably
      prevent `submit()`; a mutated packet after approval is also refused (digest mismatch).
- [x] HALT during `awaiting_egress` refuses the approval; HALT during `running` kills the child
      process (measured: process gone) and finishes the task as `halted`.
- [x] `delegate.event` replays via `since_seq` — a client connecting mid-run reconstructs the
      feed; asserted like the P0 resume tests.
- [x] A packet built for a project containing a planted `local_foreign` note shows the taint in
      the preview payload; the injection suite is green unchanged.
- [x] vitest: the egress card renders files/tokens/redactions from a recorded `approval.requested`
      event; the panel follows a recorded event sequence to `finished` with cost.
- [ ] The gate green including the security suite.
- [ ] *(Carry-over)* bge-m3 recorded in OQ-02, decision to the owner.

## Relevant files

New: `src/oracle/delegation/` (service) · `apps/desktop/src/components/EgressPreview.tsx` ·
`apps/desktop/src/components/DelegationPanel.tsx` (names indicative).
Modify: `src/oracle/core/events.py` (`delegate.event`) · `src/oracle/router/pipeline.py` (route
into the service) · `config/policy.yaml` (`agent.delegate` tier) · `docs/TOOLS.md` ·
`apps/desktop/src/protocol.ts` / `store.ts` / `App.tsx`.
Read first: [INTEGRATIONS.md §6–8](INTEGRATIONS.md) · `core/approvals.py` (the semantics being
inherited) · [API.md](API.md) (event contract) · [UI.md §9](UI.md#9-confirmation-center).

## Dependencies

P6-T1 (built). The knowledge index for requirement 6 — present on this machine; the tests use a
fixture store, not the developer's index.

## Risks

| Risk | Mitigation |
|---|---|
| A second approval mechanism grows by accident | Requirement 2's constraint + the security test asserts the only path is `ApprovalStore` |
| Long-running child vs. tool timeouts | Delegation is a daemon task, not a toolhost call — decided up front, recorded in the dev log |
| Digest theatre: approving a summary, not the bytes | Digest = hash of rendered packet files; re-render invalidates; asserted |
| UI shows a live countdown for a dead backend's approval | Already solved for approvals generally (issuedAt from `ev.ts`) — reuse, don't re-derive |
| Event flood from a chatty delegate | `delegate.event` is coalescable by design; state lives on `task.*` |

## Definition of done

All acceptance criteria · the ADR-worthy decisions (delegation-as-daemon-task, digest-of-bytes)
recorded in the dev log · TOOLS.md updated if the tool count moves · the gate green including the
security suite · `current_report.md` overwritten · this file updated to **P6-T3** (MCP server) or
the phase capstone, whichever the state of the phase argues for.
