# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** the P11 queue, on the owner's three mid-evening calls: OQ-18 moved to **04:00**
(`WakeToRun`), **`main` is the branch** (PR #1; push after every task — AGENTS.md), keep working.
**Status:** four tasks shipped and pushed: the **Timeline** (§7), the **global search backend**
(`GET /api/v1/search`) whose measurement flushed out a four-day live defect, the **search
overlay** (`Ctrl+Shift+F`), and this ledger.
**Date:** 2026-08-28, evening arc (into the small hours)
**Dev logs:** [search, timeline, and a four-day defect](../logs/development/2026-08-28-search-timeline-and-a-four-day-defect.md) ·
[the afternoon arc](../logs/development/2026-08-28-p11t5-and-measurements.md)

---

## The defect that matters: `know.*` was dead in the live system since 2026-08-24

Measuring global search's 300 ms target returned refusals instead of numbers, and the reason
was real: `tools/knowledge.py` had **hardcoded `E5_BASE`** while the indexer moved to `bge-m3`
on 2026-08-24 — so every `know.search` / `know.read_context` / `know.reindex` through the
toolhost has failed `bind()` with a SchemaMismatch for four days. Fixture tests never saw it:
an empty tmp index binds whatever the tool asks for, self-consistently — green on a world the
machine does not have, the `TaskTree` lesson one layer down. Fixed (the tool layer aliases
`embedding.DEFAULT` once; a security test pins it), and mercifully `bind()` raises before the
store opens, so the wrong-model rebuild was never reachable.

**⚠ The fix and both new endpoints reach the live system on the next `oracled` restart.** The
running daemon (up since 01:05) predates `POST /api/v1/knowledge/reindex`, `GET /api/v1/search`
**and** the `know.*` fix — one restart lights up the Rebuild button, the search overlay, and
live retrieval at once.

## What shipped

1. **Timeline (UI.md §7)** — the flat events table became the grouped, filterable stream on
   `Ctrl+3`, contiguity-grouped (never re-sorted), per-group `[inspect]` into the app-wide
   selection. The a11y audit caught a `nested-interactive` violation before first commit —
   the disclosure is a proper `button[aria-expanded]` because of it. Verified live: 14 groups
   from the real 500-event stream.
2. **`GET /api/v1/search`** — six groups: files/notes via `know.search` through the gate
   (taint rides through), projects/tasks/events as SQL over stored rows, GIT only when a
   project is named (an all-repo sweep is OQ-24's fan-out under a new name). Each group fails
   alone; LIKE is escaped. **Measured: warm p50 681 / p95 1,270 ms** for the retrieval half —
   §11's pre-bge-m3 300 ms target is missed 4× and recorded in place.
3. **The overlay** — palette-style combobox, six labelled groups, 300 ms debounce,
   `elapsed_ms` on screen. Enter does only what the app can honestly do (select a project —
   not `continue` — inspect a task, jump to the Timeline); files/notes/git are previews and
   Enter refuses to pretend; `Ctrl+Enter` deferred until a context-package API exists.

Also this evening, before the queue: the palette became a real combobox (the audit's last
debt), DATABASE.md was reconciled to the seven tables that actually exist (five sketched
tables never were), and the eval's answer-key diagnostic — which had printed `0/38` since
birth — was fixed after a probe measured the truth at 38/38.

## Suites

**834 + 411 python · 327 UI**, gate green per task before each push. One first-run 120 s
timeout (the first-ever successful bge-m3 load in a test, cold cache) named by
`pytest-timeout` and clean on every rerun.

## The morning's two items

1. **OQ-18 collects after ~07:00.** Fires 04:00 with `WakeToRun`; the evening checkpoint will
   be refused (tonight's commits changed the corpus — the guard working) so it runs clean on
   an idle box. Steps in [current_task.md](current_task.md); the `0/38` answer-key line is the
   old broken diagnostic — the fixed one prints from this run onward.
2. **Restart `oracled`** (then the human click that ends `tasks = 0`): the daemon, Ollama and
   `npm --prefix apps/desktop run dev` — then `continue ORACLE`, approve the T3 card, and the
   Tasks stage, orbit go/no-go and briefing arithmetic all get their first real rows.
