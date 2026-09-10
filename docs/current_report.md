# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** continue building ORACLE — find the state, find the next task, build it.
**Status:** **Phase 11's view list is complete, and Phase 13 has everything except the one step that
changes your machine.** The orbital view was built, tested and cut. Six commits on `main`, gate green
each time.
**Date:** 2026-09-10
**Dev log:** [the centrepiece loses to a sentence](../logs/development/2026-09-10-oq14-the-centrepiece-loses-to-a-sentence.md)

---

## What happened

**[OQ-14](OPEN_QUESTIONS.md#oq-14) resolved `CUT`.** The orbital view — the picture on the cover,
specified before anything else in the UI — was built in the morning and deleted in the afternoon. It
failed its own pre-committed test: cover every label and it still said three true things, and all
three were already written in words in the chrome around it. The measurements and the argument are
in [ADR-0029](DECISIONS.md#adr-0029--the-orbital-view-is-cut). ADR-0013's stable-angle layout was
never what failed and still governs the knowledge map.

**[§8's agent queue](UI.md#8-agent-queue)** shipped, which completes P11's view list. It replaced the
sidebar's `WAITING ON ME` list, because that was its own `BLOCKED` bucket under a second name.

**P13's boot health phase** (`src/oracle/core/health.py`) probes policy, the event log,
`knowledge.db`, the router and each delegation adapter, and reports what each failure *costs*
rather than that it happened. Its acceptance criterion was run, not asserted: Ollama stopped, daemon
restarted, ONLINE in 376 ms with the loss named.

**[ADR-0030](DECISIONS.md#adr-0030--the-shell-attaches-to-a-resident-daemon-and-only-owns-one-it-started)**
had to supersede a *resolved* question. [OQ-11](OPEN_QUESTIONS.md#oq-11) made the daemon die with the
window via a kill-on-close Job Object — correct for the architecture it was asked in, and the one
thing making P13's "closing the window does not stop work" impossible by construction. The question
turned out to be about **ownership**, not lifetime.

---

## What is waiting for you

1. **The autostart task is written and not installed.** `pwsh -File scripts/install_oracled_task.ps1`
   registers a logon task; `-Uninstall` removes it. It changes what your machine does at logon, so no
   agent runs it. All three branches of its guard were tested.
2. **OQ-18's re-run fires at 23:00** against a corpus frozen at `62355e3`. On completion, compare the
   new `gated` against **61.1%** — the like-for-like baseline over the 36 still-reachable fixtures.
   **If it does not move, revert [ADR-0027](DECISIONS.md#adr-0027--rrf-is-weighted-against-the-lexical-list).**
3. **Two fixtures died in another repo.** `entitlementGuard.ts` and `rbacGuard.ts` are staged
   deletions in Asterim. Restoring them restores the 38-case set; that is your call.
4. **UI.md §1/§14/§15 are still `TO VERIFY`** — the visual references were never attached. This
   mattered less than it looked: OQ-14 was answerable by measurement without them.

---

## What I got wrong, and what it cost

- **I let a commit kill a 2-hour eval run.** `corpus_fingerprint()` hashes every embedded chunk and
  ORACLE indexes itself, so the OQ-14 commit moved the corpus under a run already 28% through. The
  checkpoint was dead before anyone asked to stop it. Fixed by freezing the corpus (`--corpus-cache`),
  which also makes runs weeks apart comparable — they never were.
- **I predicted the orbit's label layer would collapse at realistic density.** Measured: 14 of 91
  pairs overlap. Degradation, not collapse. The number is in the ADR instead of the adjective.
- **My first health phase spawned `claude --version` on every app startup**, including several
  hundred times per test run.
- **I wrote the autostart guard in batch.** Testing only the happy path would have shipped it: the
  stranger branch died with `else was unexpected at this time` because batch expands `%BODY%` inside
  an `if`, and every JSON body contains quotes. Rewritten in PowerShell, then found three more
  defects that only appear on Windows PowerShell 5.1 — no BOM means em dashes break parsing, the
  response body arrives as a `byte[]`, and `Add-Content -Encoding UTF8` writes a BOM per append.
  **Every one of those would have failed silently at logon on your machine and nowhere else.**

---

## Gaps worth knowing about

- **`clippy` and `rustfmt` are not installed** on this toolchain, so the Rust has no linter or
  formatter. `cargo test` is now in the gate; the other two need `rustup component add`.
- **Nothing verifies P12's Definition of Done end to end** — "say continue Asterim, walk away, come
  back to a completed or gated graph". It needs a human approval click, so no agent can close it.
