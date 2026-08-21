# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P4-T1 — Desktop UI v1: the Confirmation Center first**

**Phase:** [4 — Desktop UI v1](ROADMAP.md#phase-4--desktop-ui-v1---mvp-milestone) · **Scope:** MVP — ★ **the milestone**
**Status:** `NOT STARTED` · **Set:** 2026-08-21
**Previous task:** P3-T1 — `DONE`, all criteria verified live, see [current_report.md](current_report.md)

---

## Objective

Build the interface that makes the tools usable: chat with tool-call cards, the Confirmation Center,
the terminal dock, the command palette. Everything before this was infrastructure. **This is the
product**, and its definition of done is a working day without opening a terminal by hand.

## Why the Confirmation Center comes first

Everything else in this phase is an interface to something that already works. The approval card is
the one piece where the UI is **part of the security model**, not a view onto it:

- the runtime already emits `approval.requested` with the tier, the rule that fired, the arguments
  and a preview, and already refuses to execute without a matching answer;
- **nothing renders it.** A confirmation the user cannot read is a rubber stamp, and a rubber stamp
  is worse than no prompt at all — it trains the habit of clicking Approve.

So the card is task 1, not task 4. It also forces the tool-call card format that chat needs anyway.

## Context

P3 delivered 27 tools behind the gate, tool selection in the router (100% on the eval set), the
program allowlist, the app catalogue, and the approval round trip. 360 tests.

Established, and not to be re-derived:

- **The event contract already carries everything the UI needs.** `approval.requested` payload is the
  card: tool, tier, decision, rule, args, preview, `expires_in_s`. If a field is missing from the
  event, it must not influence the decision — add it to the event, never fetch it separately.
- **`approval.respond`** is `{approval_id, decision: "approve"|"reject"}`. `nonce` and `scope` were
  designed and deliberately dropped ([API.md](API.md#2-websocket-protocol) explains why).
- **A dry run performs nothing** and needs no approval — that is what lets the card show a real file
  list before asking. `fs.delete` and `dev.execute` support it.
- **Agent states are one vocabulary** shared by runtime and UI (`awaiting_approval`, not a
  translation of it). `apps/desktop/src/protocol.ts` already has the enum.
- The WS client, resume-on-`since_seq` and the degraded banner all work and are tested (14 TS tests).

## Requirements

1. **Confirmation Center.** Renders `approval.requested`; keyboard-driven (`y`/`n`, never a bare
   Enter on a T3); shows the rule that fired and the *real* preview; a visible countdown, because
   the request expires; a mis-click guard on T3 that is not a second modal.
2. **Tool-call cards in chat.** One card per call: tool, arguments, outcome, duration, and an
   **Undo** control when the outcome carries an `undo_id`.
3. **Command palette** (`Ctrl+K`) feeding the pre-router — the fastest path to any action, and the
   thing that makes the pre-router's zero-latency path visible.
4. **Terminal dock** on xterm.js. `term.read` returns `truncated` when more is waiting and `dropped`
   when scrollback was trimmed; **both must be visible**, or the UI repeats the bug the backend just
   had.
5. **Task list + inspector**: status, duration, tools used, files changed, result.
6. States: loading skeletons, empty states, offline banner, degraded banner.
7. Design tokens, full keyboard navigation, `prefers-reduced-motion`, focus rings.

## Constraints

- **No orbital visualisation.** It is Phase 9. Building the decorative centrepiece before the
  functional shell is how this kind of project dies at 80%.
- Colour is never the only carrier of meaning — icon + label always accompany a status colour.
- The UI never computes a tier, a rule or a digest. It renders what the event says.
- TypeScript `strict`, no `any` without a comment explaining why.

## Acceptance criteria

- [ ] Every MVP action is reachable **without a mouse**.
- [ ] An approval can be read and decided in **under 5 s** — the preview shows the actual command,
      not a description of it.
- [ ] Approving a T3 delete shows the real file list from a `dry_run` first.
- [ ] The terminal handles 10k lines without frame drops, and *says so* when output was trimmed.
- [ ] With the backend down, the app opens, explains itself, and reconnects on its own.
- [ ] An `axe` pass with zero criticals; visible focus throughout.
- [ ] ★ **A full working day without opening a terminal manually.**

## Relevant files

Create: `apps/desktop/src/components/{ConfirmationCenter,ToolCard,CommandPalette,TerminalDock}.tsx`
Modify: `apps/desktop/src/App.tsx` · `apps/desktop/src/client.ts` (send `approval.respond`, `undo`)
Read first: [UI.md](UI.md) · [API.md §2](API.md#2-websocket-protocol) ·
[SECURITY.md §5](SECURITY.md#5-confirmation-and-approvals) · `src/oracle/core/approvals.py` for the
exact payload the card is built from

## Dependencies

P3-T1 (done). No open question blocks this. [OQ-14](OPEN_QUESTIONS.md#oq-14) concerns Phase 9 only.

## Risks

| Risk | Mitigation |
|---|---|
| UI polish is infinitely expandable | The acceptance list above is the definition, not "it feels good". Timebox and ship. |
| A confirmation card that is easy to approve without reading | The card is part of the security model. Measure the 5 s target with a real T3 delete, and put the *file list* on it, not a summary. |
| WebView2 renders differently from Chrome | Test in the Tauri shell, not only in the browser dev server. |
| Terminal performance with 10k lines | xterm.js with a bounded scrollback; the backend already reports `truncated` and `dropped` — surface both rather than re-inventing a buffer. |

## Definition of done

All acceptance criteria · the gate green including TS typecheck and vitest ·
Playwright covers the core journeys · `current_report.md` overwritten ·
this file updated to **P5-T1** · ★ **the MVP milestone is reached**.
