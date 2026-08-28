# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P12-T5 — the first real `continue` run.** ⚠ **A person's to run, not an agent's.**

**Phase:** [12 — project state & the continue loop](ROADMAP.md#phase-12--project-state--the-continue-loop--residency-arc) · **Scope:** Residency arc
**Status:** `SET` · **Set:** 2026-08-26 · **Blocked on:** a human (everything else is up and waiting)

**Done in Phase 12:** T1–T4, and T5's first run — which stopped at the egress approval (where an
agent stops) and fixed the safety defect it found. What remains of T5 is **one approval click**.
**Done since (2026-08-28 afternoon, agent session):** P11-T5 (switchable centre stage, task
inspector, KnowledgeHealth mounted) · OQ-24 and OQ-25 measured and resolved · OQ-18 re-fired
hardened. [Report](current_report.md) ·
[dev log](../logs/development/2026-08-28-p11t5-and-measurements.md)

---

## Why this is not an agent's task

Every step is gated and previewable, and that is the point: approvals expire in **180 s**, so
firing this unattended writes a *refused* run into the very table the run exists to populate.

## What to run

Everything is already running (daemon since 01:05, Ollama, and the dev UI at
`http://localhost:5273` left up by the afternoon session). If starting cold:

```bash
uv run oracled
```

```bash
npm --prefix apps/desktop run dev
```

Then, in the command bar: **`continue ORACLE`** — or click the ORACLE row in the sidebar, which
sends the same thing. Approve the **T3 `confirm_strong`** card (it now truthfully says it sends
two repo files — that price is the P12-T5 fix working). `oracle-selfcheck` remains the cheaper
first fill: local, **no egress**, six steps, one card, ~5 min, a real six-task graph.

## What one approval unblocks

`tasks` stops being 0 rows, and with it: **OQ-14** (the orbit's go/no-go, finally against real
data) · the execution tree's acceptance criteria · re-recording `TaskTree`'s fixture from the
wire · first real input for the sidebar counters and the briefing arithmetic. The new **Tasks
stage (`Ctrl+2`)** and the **inspector's task branch** will render it live as it runs.

---

## The one timed item: collect OQ-18 (~19:30 tonight)

The corpus run was re-fired **hardened** at 16:07 — live progress in
`logs/measurements/oq18-translated.txt` (rate + ETA per 256-chunk checkpoint), atomic resumable
checkpoints at `D:/ORACLE/scratch/oq18-vectors-bge-m3.npz`, and a sleep guard. **An unchanging
log now IS a stuck job** — the old "empty file is normal" caveat is dead. If it was killed
again, just `Start-ScheduledTask ORACLE-OQ18-eval`: it resumes from the last checkpoint.

On collection:

1. **Check `all 38 fixture sources present` and the answer-key line first.** This corpus printed
   *"answer-key documents in the lexical candidates of 0/38 queries"* where 2026-08-26 measured
   37/38 — verify `tests/fixtures/retrieval/cases.yaml` is still in the corpus before trusting
   recall numbers.
2. Compose `dense_mt` against `dense_xl` (mechanism vs ceiling), confirm or flip
   `Settings.translate_queries`, decide `en-relay-dockerfile`.
3. Resolve [OQ-18](OPEN_QUESTIONS.md#oq-18); state the answer-key correction wherever
   pre-2026-08-26 recall numbers are quoted.
4. `Unregister-ScheduledTask -TaskName ORACLE-OQ18-eval`.
5. Only after collection: consider firing the **reindex** (57% of live rows exceed the 1200-char
   cap). The UI's Knowledge stage now has the button and `POST /api/v1/knowledge/reindex` exists
   — but the running daemon predates the route, so **restart `oracled` first**, and the full
   rebuild is ~1 h synchronous.

## Operational note for any session while the daemon runs

The daemon holds `.venv\Scripts\oracled.exe`, so **syncing `uv` commands fail with `os error
32`** while it is up. Use `uv run --no-sync …` (the gate = `scripts/check.py`'s seven steps with
`--no-sync` appended), or stop the daemon first — by PID, never by pattern. Run one plain
`uv sync` after the daemon stops to reconcile the entry-point exe.

---

## Carried over, not forgotten

- **Palette results are not discoverable to assistive tech** — `<li role="option">` with
  `onClick`, no `role="combobox"`, no `aria-activedescendant`. (The rest of the a11y audit is
  now 15/15 components.)
- **`DATABASE.md`'s `facts`/`attempts`/`devices` blocks are still the pre-build sketch.**
- **A merge-gate test fails under CPU starvation** (`test_a_long_burst_arrives_complete`) —
  `pytest-timeout` (120 s) now bounds hangs, but the underlying wall-clock assumption stands.
- **A correction typed while a graph runs is refused** — the fix, when somebody hits it, is a
  queue, not an exception.
- **Scheduled pipeline runs** are post-MVP; PIPELINES.md §5's "nothing above T1 unattended" is
  unenforced because nothing schedules anything.
- **The visual references for the UI vision were never attached**; UI.md §1/§14/§15 remain
  `TO VERIFY` against them.
- **Branch.** `phase6-integration` is a fossil name carrying Phases 6–12, ahead of a stale
  `origin/main`. Merge-or-rename is still nobody's decision.
- **P11 remainder:** T2 orbit (blocked on OQ-14 → blocked on the click above) · the knowledge
  graph (OQ-22 measurements first) · timeline proper (§7), global search, notifications.
