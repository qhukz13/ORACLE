# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P6-T1 — closed 2026-08-24. **P6-T2 is set** and is the active task.
**Status:** The delegation core exists and is tested offline; nothing egresses yet without a human,
because nothing egresses at all — the approval/preview/verification layer is exactly P6-T2.
**Date:** 2026-08-24

---

## P6-T1, in five lines

| | |
|---|---|
| Contract | Recorded from the real CLI (v2.1.238), two fixtures; three stream rules the docs lacked. |
| Auth | `--bare` is unusable on subscription auth — measured. Dropped; isolation rebuilt materially. |
| Adapter | `ExternalAgentAdapter` + `ClaudeCodeAdapter`, 9 replay tests, no network. |
| Packet | Six files + json; redaction before rendering (asserted per file); 30k ceiling that raises. |
| Workspace | Worktree + scrub (no opt-out), diff excludes the scrub, discard byte-identical; snapshot fallback; `deliver()` routes on preflight. |

Full detail: [2026-08-24-p6t1-adapter-packet-worktree.md](../logs/development/2026-08-24-p6t1-adapter-packet-worktree.md)
and [2026-08-23-claude-auth-contract.md](../logs/development/2026-08-23-claude-auth-contract.md).

## The active task

**P6-T2 — Delegation you can approve, watch, and verify** ([current_task.md](current_task.md)):
egress preview as a real `ApprovalStore` approval whose digest binds the rendered packet *bytes*;
an asserted egress gate (refused/expired/mutated → no submit) in the security suite; live progress
via a coalescable `delegate.event` + the reserved `task.*` types; verified collection
(diff + `dev.run_tests` through the gate); retrieval-fed curation with taint shown in the preview;
and the UI's Confirmation Center learning to render an egress card.

## Standing state

- Branch `phase6-integration`, pushed; `main` fast-forwarded to the end of Phase 5 and pushed.
- Gate green (ruff, mypy, tsc, pytest, security, vitest) as of the last P6-T1 commit.
- **Carry-over:** the `bge-m3` full-corpus run is scheduled (05:00 local, fires on next app launch
  if missed); it writes OQ-02 + a dev log, and the model decision goes to the owner. Keep its
  commits separate from delegation work.
