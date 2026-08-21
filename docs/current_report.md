# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P4-T1 — Desktop UI v1
**Status:** `DONE` — ★ **the MVP is complete**, with one criterion that only using it can tick
**Date:** 2026-08-21

---

## Where the project is

**Phases 0–4 are done. ORACLE is a working local agent with a working interface.**

Ask it to check a repository and it runs `git.status` and shows you the card. Ask it to push and it
stops and shows you the nine commits that would leave the machine. Type in the terminal dock and the
keystrokes reach a real ConPTY inside a Job Object that HALT can kill.

| | |
|---|---|
| Tools | **29** registered, 26 offerable, 11 reachable from a routed turn |
| Tests | **370 Python** + **77 TypeScript**; the security suite is a merge gate |
| Router | 93.3% intent accuracy, **100% tool selection** on the eval set, p50 1.16 s |
| Gate | ruff · mypy `--strict` · pytest · tsc · vitest — green |

## Acceptance criteria — measured, not asserted

| criterion | result |
|---|---|
| Every MVP action without a mouse | ✅ `Ctrl+K` · `Ctrl+B` · `Ctrl+I` · ``Ctrl+` `` · `F1` · Enter · `A`/`D`/`Esc` |
| Approval readable and decidable in < 5 s | ✅ one screen: the real argv, the rule, a real dry run, a live countdown |
| T3 delete previews a real file list | ✅ verified in ORACLE's scratch scope; the preview deleted nothing |
| Terminal handles 10k lines | ✅ **10,000 / 10,000**, 1.03 MB in 6.0 s, loop p50 12.1 ms |
| Backend down → opens, explains, reconnects | ✅ killed mid-session; resumed at `since_seq=251` when it returned |
| Colour never the only carrier of meaning | ✅ asserted per component |
| Zero serious/critical axe violations | ✅ across all four components |
| ★ A full working day without a terminal | ⬜ **not something a suite can tick.** Left honest. |

## The two bugs that came from driving the app, not the tests

Both were invisible to a green suite, and both were in the surface the phase exists for.

**1. A stale approval blocked the live one.** History replays from seq 0 after a reload, so a request
issued by a backend that has since exited arrives looking brand new. It sat at the head of the queue —
with a live countdown — where nothing could ever answer it, hiding the real approval behind it.
Expiry now counts from the **server's** timestamp, and an already-expired approval never joins the
queue. That is the server's own rule, applied on arrival.

**2. `git.push` was unroutable, so the Confirmation Center could never fire.** Every routable tool was
T0 or T1. The most safety-critical surface in the product had no path to appearing in normal use, and
nothing said so. `git.push` turns out to be buildable honestly from the project alone — `origin` and
the checked-out branch are what "push my changes" means — and a test now asserts that it being the
only routable tool above T1 stays a decision rather than an accident.

## What was built

```
components/ConfirmationCenter.tsx   the card; the one UI that is part of the security model
components/ToolCard.tsx             one card per call: tier, verbatim args, Undo where real
components/CommandPalette.tsx       Ctrl+K into the pre-router; never dead-ends
components/TerminalDock.tsx         xterm.js over a real ConPTY
components/Inspector.tsx            what a turn decided, ran, and cost
core/terminal.py                    the bridge: PTY output onto the event stream
tools/terminal.py                   + term.input, term.resize (hidden)
```

### The distinction the terminal is built around

`term.write` is **the agent** typing into a shell: T2, confirmed every single time, one line per call
so an approval cannot cover a script. `term.input` is **the human** typing: T1, and hidden so the
model can never reach it.

Asking someone to approve their own keystrokes is not a security control — it is a way to teach them
to click Approve. But a tool the agent *could* call would be `term.write` with the confirmation
removed, which is the exact hole the separation exists to prevent. Hence two tools.

### Why the terminal polls

The PTY lives in the toolhost, because a runaway `npm install` must die with HALT. The parent polls
each session through the ordinary tool path and republishes the output as `term.output`. A push
channel would mean the parent's frame reader had to tell a reply from an announcement — a protocol
seam exactly where correctness matters. Polling costs one ~28 ms round trip per session per 120 ms
and reuses the gate, the audit log and the timeouts unchanged.

## Decisions worth knowing

- **The Confirmation Center was built first, not fourth.** Everything else in the phase is an
  interface to something that already worked. The card is the only piece where the UI *is* the
  security model, and a confirmation the user cannot read is worse than no prompt at all.
- **"Always for X" was deliberately not built.** A scoped standing approval makes prompts cheaper;
  the answer to prompt fatigue is *fewer* prompts, which reversibility and T1 already deliver.
- **Playwright was not added.** It meant a browser download and a second harness for journeys already
  driven against the real backend — which is where both real bugs were found. 77 vitest tests
  including axe over the rendered DOM cover the components.
- **No orbital view.** Phase 9. Building the decorative centrepiece before the functional shell is
  the classic way this kind of project dies at 80%.

## Known gaps, honestly

- **The terminal's rendering was never visually verified.** The browser pane available here does not
  composite, and xterm measures a character by rendering one — so what was verified is the pipeline
  (10k lines, no loss) and the terminal *buffer* (`C:\Projects>echo terminal-dock-works`), not the
  pixels. A guard was added for the genuinely-fixable half (attaching before layout exists); the
  headless case is documented in the component rather than papered over.
- **11 of 26 tools are reachable from a routed turn.** The rest need an argv, a command, file content
  or a remote — none derivable from *(project, one string)* without inventing something.
- **One terminal session, no sub-tabs, no search.** `Ctrl+F` in the dock is not built.
- **No task system.** The inspector shows a *turn*; tasks arrive with delegation in Phase 6.
- **[OQ-13](OPEN_QUESTIONS.md#oq-13) is still an assumption.** T1 covers writes, commits, tests,
  builds and branches, so daily use should prompt rarely — but nobody has counted yet, and now
  somebody can.

## For whoever picks this up

The next phase is **P5 — Project knowledge (RAG)**, and the MVP is behind you. Before starting it,
consider spending a day *using* ORACLE: the unticked criterion above is the only one that matters,
and it is the one most likely to produce a list of small things that make it genuinely daily-usable.
