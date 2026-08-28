# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** the agent-doable queue while P12-T5 waits on its human click — **P11-T5** (the
switchable centre stage), **OQ-24** and **OQ-25** (both measured and resolved), and the
**OQ-18 corpus run** found dead, hardened, and re-fired.
**Status:** all four strands done; gate green (run as its seven exact steps — see the trap below);
the corpus run is computing with live progress and checkpoints, ETA ~19:30.
**Date:** 2026-08-28 (afternoon session)
**Dev log:** [2026-08-28-p11t5-and-measurements.md](../logs/development/2026-08-28-p11t5-and-measurements.md)

---

## The OQ-18 run was dead, and both mysteries are closed

The morning hand-off left it "RUNNING, needs a decision". By 15:42 it was dead —
`STATUS_CONTROL_C_EXIT` again, log still 63 bytes, nothing checkpointed. The "unexplained"
13 hours: **the machine slept from 01:44 to 13:37** (Kernel-Power 42 / Power-Troubleshooter 1);
the kill itself was a console control event in 14:05–15:42, unattributable because the
TaskScheduler operational log is disabled.

Re-fired at 16:07 after hardening the script against all three failure modes: **live progress**
(`-u` + line buffering + a rate/ETA line per checkpoint), **256-chunk atomic checkpoints with
resume** (a kill now costs ≤ ~4 minutes, and the smoke test killed and resumed a real pass to
prove it), and **`ES_SYSTEM_REQUIRED`** so the machine cannot sleep mid-measurement. On
collection (~19:30): compose `dense_mt` vs `dense_xl`, decide `translate_queries` and
`en-relay-dockerfile`, resolve OQ-18, then `Unregister-ScheduledTask ORACLE-OQ18-eval`. **Check
the answer-key line first** — this corpus printed `0/38 queries` where 2026-08-26 measured
37/38; verify the fixture file is still in the corpus before trusting recall.

## OQ-24: the fan-out misses, so the sidebar observes lazily — built

Measured by the new `scripts/measure_observation.py` under deliberate load: the 8-row fan-out
costs **1.7–2.7 s warm** (2–3× over budget), the two toolhost IPC round trips dominate, and the
toolhost serialises invocations so eager observation would queue behind real work. Applied as
OQ-24 prescribed: the **selected row** is observed fresh (`GET /api/v1/projects/{id}`) and
renders `⎇ main ↑3 ↓1 ~2`; nothing is cached; the list endpoint still runs no git.

## OQ-25: eleven labels measure better than ten, and the slot failure has a name

`make eval` now exists, the fixture set carries four `continue` cases, and the eval says:
**97.1% intent accuracy (was 93.3%)**, `continue` 4/4 on label **and** slot, one confusion
(`собери GameRecs` → continue — the feared boundary, in reverse). The live-run slot failure is
specific: the 0.8B model **never emits `ORACLE` as a project value** (9/9 deterministic; a
prompt instruction measured 6/6 no-effect and was reverted) — `_named_project`, deterministic
code, carries exactly that case by design. Also found: two fixture slot misses are **few-shot
proximity beating extraction** — texts nearly identical to a `project: null` few-shot lose the
project named in the sentence.

## P11-T5: built, tested, and verified against the live daemon

The centre stage is a mechanism now: **`ViewTabs`** (a real tablist, arrow keys, roving
tabindex) over Chat · Tasks · Events · Memory · Briefing · Knowledge; **`Ctrl+1..4`** with an
AltGr guard; **TaskTree in its own Tasks stage** with a stated empty state; **the inspector's
task branch** replacing the P12-T4 stopgap (one selection model — briefing inspect, task-row
click and turn click all drive it; evidence and claim render apart; a task id older than the
five held graphs says so); **`KnowledgeHealth` mounted** with a real wire —
`POST /api/v1/knowledge/reindex` (new, through the executor and the policy gate, T1). The
briefing keeps its once-only takeover; approvals and delegations stay above every stage —
a card behind a tab is a card that expires unseen.

**305 UI tests** (was 277), tsc strict, and the a11y audit now covers **15/15 components**
(Inspector, Citations, EgressPreview added) plus ViewTabs. Live verification against the
running daemon: the briefing auto-opened with the real crash line, keyboard switching worked in
a real browser, and the Knowledge stage rendered the real 147 MB index.

UI.md §2/§16/§20, §4, §6, §6b corrected in place with dates and reasons (the `Ctrl+1..4`
table named two views that do not exist); PROJECT_STATE.md and ROADMAP's P12 checkbox updated;
API.md documents the new endpoint.

## Debts paid alongside

`pytest-timeout` (120 s/test — a hung gate becomes a named failure; TECH_STACK §11) ·
`make eval` defined, `make perf` honestly removed from TESTING.md §8 · the stale
"`store.ts` never populates `dependsOn`" claim corrected at its source.

## The trap that shaped the mechanics, and two notes for the next session

- **The resident daemon holds `.venv\Scripts\oracled.exe`**, so any syncing `uv` command fails
  with `os error 32` while `oracled` runs. This session's gate therefore ran as the **seven
  exact steps of `scripts/check.py` with `--no-sync` appended** — same commands, same scope:
  ruff format ✓ · ruff lint ✓ · mypy ✓ (117 files) · tsc ✓ · pytest ✓ · security ✓ · vitest ✓
  (305). Run a plain `uv sync` once the daemon is stopped to reconcile the entry-point exe.
- **The running daemon predates `POST /api/v1/knowledge/reindex`** — the route goes live on its
  next restart; the API tests cover it fully meanwhile.
- **Do not fire the full reindex** (the Rebuild button, ~1 h, synchronous) until the OQ-18 run
  has been collected — they would contend for every core.

## What is still not done

**`tasks` is 0 rows.** Unchanged, and still one human click away — approve a fresh T3 card from
`continue ORACLE` (the daemon, Ollama, and now the dev UI at :5273 are all up for it), or run
`oracle-selfcheck`. The orbit's go/no-go (OQ-14), the execution tree's acceptance against real
data, and `TaskTree`'s wire-recorded fixture all still wait on it.

## Next

[current_task.md](current_task.md) — unchanged in essence: P12-T5's human click closes Phase 12,
then [P13](ROADMAP.md#phase-13--residency-boot--the-briefing--residency-arc). The OQ-18
collection (~19:30) is the one timed item.
