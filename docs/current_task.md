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

## The one timed item: OQ-18 runs at 04:00 tomorrow — collect it in the morning

The owner moved the corpus run off working hours (2026-08-28 ~21:45): the task now has a
**one-time 04:00 2026-08-29 trigger with `WakeToRun`**, so the machine wakes for it and the
script's own sleep guard holds it awake through the ~2.5–3 h pass — done by ~07:00 on an idle
box. The evening attempt was stopped at ~60% for PC-performance reasons; its checkpoint will
be **refused** at 04:00 if any tracked file changed after 20:14 (they did — evening commits),
so expect a clean restart, which is the guard working, not a failure. Live progress in
`logs/measurements/oq18-translated.txt` (rate + ETA per 256-chunk checkpoint); **an unchanging
log IS a stuck job** — the old "empty file is normal" caveat is dead. If it needs a re-fire:
`Start-ScheduledTask ORACLE-OQ18-eval` resumes from the last checkpoint.

On collection:

1. **Check `all 38 fixture sources present`** (it printed green on tonight's run). Ignore the
   *"answer-key documents in the lexical candidates of 0/38 queries"* line in tonight's output:
   that diagnostic was born broken on 2026-08-26 (it compared the lengths of two `[:12]`
   slices, which can essentially never differ) and printed 0 from day one. A 2026-08-28 probe
   measured the truth — **38/38 queries carry an answer-key chunk in their top-12 lexical
   candidates, ranking 0–3** — so the exclusion is load-bearing and the recall numbers
   (computed by `score_set`, which filters the full ranking) were never affected. The fixed
   diagnostic ships in `eval_embeddings.py` and prints honestly from the next run.
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
- ~~Branch.~~ **Resolved 2026-08-28 evening:** the owner merged PR #1 (`phase6-integration`
  → `main`) and set the standing rule — work on `main`, commit and push after every task
  (AGENTS.md updated). The fossil branch can be deleted at leisure.
- **P11 remainder:** T2 orbit (blocked on OQ-14 → blocked on the click above) · the knowledge
  graph (OQ-22 measurements first) · the agent queue (needs live task data) · notifications.
  *Timeline (§7) and global search (§11, backend + overlay) shipped 2026-08-28 evening.*
- **⚠ Restart `oracled` before trusting three things:** `POST /api/v1/knowledge/reindex`,
  `GET /api/v1/search`, and — more importantly — the **`know.*` model fix**: the tool layer
  had pinned `multilingual-e5-base` since before the 2026-08-24 bge-m3 switch, so live
  retrieval tools have been failing `bind()` for days. The fix is in source; the process up
  since 01:05 predates it.
