# ORACLE — Current State

> **Hand-off brief for an agent picking this project up cold.** Rewritten **2026-08-28 evening**
> against source, the live databases and the running processes — not against the other docs.
> Where a doc and the code disagree, that disagreement is recorded here rather than smoothed over.
>
> This is a **snapshot** — overwrite it, do not append. For *what to do next* read
> [current_task.md](current_task.md); for *what was just done* read
> [current_report.md](current_report.md); for *the rules you must follow* read
> [../AGENTS.md](../AGENTS.md) first; for *what the product is for* read [VISION.md](VISION.md).

---

## 1. Read this first: what is running on the machine right now

| | |
|---|---|
| **OQ-18 corpus run** | **SCHEDULED: 04:00 2026-08-29, `WakeToRun`.** Stopped at ~60% on the owner's instruction (PC performance); expect a clean restart at 04:00 (evening commits changed the corpus, so the checkpoint will be refused — the guard working). See §2 and current_task.md. |
| `oracled` | up on 127.0.0.1:8787 since 01:05:42 — **stale: predates `POST /knowledge/reindex`, `GET /search`, and the `know.*` model fix** (live retrieval tools fail `bind()` until it restarts) |
| Ollama | up, `qwen3.5:0.8b` resident |
| Vite dev UI | on 5273 when a session runs it (`npm --prefix apps/desktop run dev`) |

**Do not kill processes by pattern.** Two OQ-18 attempts have now died to console-control kills
(`3221225786` = `STATUS_CONTROL_C_EXIT`); one was an agent's `Stop-Process` sweep. Kill by PID,
and check what you are about to kill.

**The `uv` trap while `oracled` runs:** the daemon holds `.venv\Scripts\oracled.exe`, so any
*syncing* `uv` command fails with `os error 32`. Use `uv run --no-sync …`, or stop the daemon
first (then one plain `uv sync` to reconcile the entry-point exe — content is current, only dev
deps changed on 2026-08-28).

---

## 2. OQ-18, third attempt: now supervisable

The second attempt died like the first — console control event, unattributable (the
TaskScheduler operational log is disabled) — after losing **12 hours to the machine sleeping**
(Kernel-Power 42 at 01:44, wake 13:37; that was the hand-off's "unexplained" stretch). Fifteen
hours, zero artifacts, because the script only saved at the very end.

The relaunch removed all three failure modes, and the smoke test killed a real pass mid-flight
and resumed it to prove the mechanism:

- `logs/measurements/oq18-translated.txt` updates **live** (rate + ETA per 256-chunk
  checkpoint). **An unchanging file now IS a stuck job** — the old caveat is inverted.
- The forward pass checkpoints atomically to `D:/ORACLE/scratch/oq18-vectors-bge-m3.npz` and
  `--load-vectors` resumes it: a kill costs ≤ one slice (~4 min). If it died again, just
  `Start-ScheduledTask ORACLE-OQ18-eval` — it continues where it stopped.
- `SetThreadExecutionState(ES_SYSTEM_REQUIRED)` holds the machine awake for the pass.

Measured mid-run: ~1.3–1.7 chunks/s over ~16.5k semantic chunks (the recalibrated 1200-char
chunker makes the corpus ~50% bigger than the 11,727 recorded 2026-08-25) → **~3 h of
compute per uninterrupted attempt**. A third kill (a literal `^C` at 46%, ~17:38) proved the
checkpoint machinery **and** its limit in one evening: the resume was correctly *refused*,
because this session's commits had changed the corpus under it — ORACLE indexes ORACLE, so
a measurement that spans working sessions is measuring a moving target. The attempt running
now started ~20:14 against the evening's corpus and holds it in memory, so later edits
cannot corrupt it. Collection steps are in [current_task.md](current_task.md); the
`0/38 answer-key` line in its output is a diagnostic that was born broken (fixed 2026-08-28
evening — the truth is 38/38, probe-measured).

---

## 3. What ORACLE is

A **local-first supervisor of agents** on one Windows machine. It takes intent, assembles context,
decides who should do the work, enforces what that worker may touch, verifies the result, and
reports with evidence. It is not a chatbot with tools, and it is deliberately not the smartest
model in its own system:

| Concern | Handled by |
|---|---|
| Orchestration, state, scheduling, permissions, verification | deterministic Python |
| Intent, routing, short answers | local 0.8B model via Ollama |
| Plan authorship | Claude (measured winner — [OQ-20](OPEN_QUESTIONS.md#oq-20)) |
| Implementation, debugging | Claude Code, in a git worktree |
| Review, research | Antigravity (`agy`), read-only |
| git / tests / search / launch | plain code, no model |

**The governing idea:** the most common correct action is not to call the LLM at all — and
OQ-25's resolution is the idea working: the one input the 0.8B model deterministically cannot
slot (`continue ORACLE`) is carried by a deterministic string match, not by prompt surgery.

---

## 4. Where the project actually is

```
 P0–P6  foundation                        done
 P7–P9  supervisor arc                    done
 P10    pipelines                         done
 P11    execution vis & advanced UI       IN PROGRESS — T1/T3/T4/T5 done; T2 (orbit) blocked on data
 P12    project state & the continue loop  T1–T5 built; ONE HUMAN CLICK from its DoD
 P13    residency, boot & the briefing    next
 P14 mobile · P15 voice · P16 tiers (GPU-conditional) · P17 hardening
```

**P11-T5 shipped 2026-08-28** ([dev log](../logs/development/2026-08-28-p11t5-and-measurements.md)):
the centre stage is a real mechanism — `ViewTabs` (a proper tablist) over Chat · Tasks · Events ·
Memory · Briefing · Knowledge, `Ctrl+1..4`, TaskTree in its own stage, the inspector's task
branch (the P12-T4 stopgap is gone; one selection model app-wide), `KnowledgeHealth` mounted with
a real wire (`POST /api/v1/knowledge/reindex`, T1, through the gate). UI.md corrected in place
where its §16/§20 predated the views that exist.

### The caveat that still matters most

**`tasks` is 0 rows.** It always has been. Everything that renders supervisor activity still
renders fixtures: [OQ-14](OPEN_QUESTIONS.md#oq-14) unanswerable, the execution tree's acceptance
unjudgeable, `TaskTree.test.tsx` green on a hand-written shape. **One human click ends this** —
approve the T3 card a `continue ORACLE` produces (daemon, Ollama and the dev UI are all up for
it), or run `oracle-selfcheck` (local, no egress, six steps, one card).

### Branch

**`main`, since 2026-08-28 evening.** The owner merged PR #1 (`phase6-integration` → `main`)
and set the standing rule: work on `main`, commit and push after every completed task
(AGENTS.md). The old branch survives as a pointer and can be deleted at leisure.

---

## 5. Data — the real numbers

**`D:\ORACLE\data\oracle.db`** — schema **v7**, WAL. Back it up; it is not rebuildable.

| Table | Rows |
|---|---:|
| `events` | ~500+ and growing with live sessions |
| `sessions` | 16+ |
| `projects` | **1** (`ORACLE`) |
| `tasks` | **0** |
| `memory_facts` / `memory_attempts` | **0** / **0** |

**`D:\ORACLE\knowledge.db`** — 147 MB, disposable, live-updated by the watcher (15,271 chunks /
14,336 vectors as rendered by the new Knowledge stage). **57% of live rows exceed the 1200-char
cap** — it wants a reindex, for which the endpoint and button now exist; fire it only after
OQ-18 is collected, and only against a restarted daemon.

---

## 6. Tests and the gate

**1,245 Python tests, measured** (834 main + 411 security + 1 skip) · **327 UI tests** —
2026-08-28 added ~50 across ViewTabs, stage keybindings, the inspector's task branch, lazy
observation, the Timeline, global search, the palette's combobox contract, and axe cases
taking the a11y audit to **every component** · `tests/security/` is a merge gate and is not
optional.

```
uv run python scripts/check.py     # ruff format -> ruff lint -> mypy -> tsc -> pytest -> security -> vitest
```

**Gate status: GREEN, 2026-08-28 evening** — run as the seven exact steps with `--no-sync`
appended (the daemon-holds-the-exe trap in §1), same commands and scope as `check.py`.
`pytest-timeout` (120 s/test) now turns a hang under load into a named failure. One run of step
5 dropped `TestDebounce::test_a_burst_becomes_one_group` under the OQ-18 load (an
`asyncio.sleep(0)` hop outran the 50 ms debounce window) and passed solo and on retry — the
third documented member of the wall-clock-under-load family (TESTING.md §6).

> **Run the gate's own command, not a subset** — and when the daemon is up, the whole command
> set with `--no-sync`, which is the same thing. Passing tests are evidence about behaviour,
> not about the code being well-formed; three separate failures in this project's history came
> from checking less than the gate does.

---

## 7. What this session measured (all three by running things)

1. **[OQ-24](OPEN_QUESTIONS.md#oq-24), resolved:** the 8-row observation fan-out costs
   **1.7–2.7 s warm under load** — 2–3× over budget — and the toolhost **serialises**
   invocations, so eager observation queues behind real work. The sidebar now observes the
   selected row only, fresh each time, cached nowhere (`scripts/measure_observation.py`).
2. **[OQ-25](OPEN_QUESTIONS.md#oq-25), resolved:** **97.1% intent accuracy at eleven labels**
   (was 93.3% at ten); `continue` 4/4 on label and slot. Two sharp findings: the model
   deterministically refuses to emit `ORACLE` as a project (9/9; prompt instruction measured
   ineffective and reverted; deterministic fallback carries it), and **few-shot proximity beats
   slot extraction** — two fixtures nearly identical to a `project: null` few-shot lose the
   project named in their own sentence.
3. **The OQ-18 post-mortem** (§2): sleep, not starvation; a console kill, not a hang; and a
   measurement script that saved nothing until the end — now checkpointed, resumable, observable
   and sleep-proof.

---

## 8. Known defects and doc-vs-code drift

1. **Palette results are not discoverable to assistive tech** — `<li role="option">` with
   `onClick`, no `role="combobox"`, no `aria-activedescendant`. The last a11y debt.
2. **`DATABASE.md`'s `facts`/`attempts`/`devices` blocks are still the pre-build sketch.**
3. **`test_a_long_burst_arrives_complete` fails under CPU starvation** — bounded now by
   pytest-timeout, but the wall-clock assumption stands (TESTING.md §6 discusses).
4. **The knowledge index wants a reindex** (57% oversized chunks) — machinery exists as of
   today; sequencing note in current_task.md.
5. **A correction typed while a graph runs is refused** — the fix is a queue, not an exception.
6. **The visual references for the UI vision were never attached**; UI.md §1/§14/§15 remain
   `TO VERIFY`.
7. **The running daemon predates today's reindex endpoint** — restart `oracled` before trusting
   the Knowledge stage's Rebuild button against the live process.
8. **`TaskTree.test.tsx`'s graph fixture is still hand-written** — re-record from the wire
   after the first real graph run (the standing fix; blocked on §4's click).

---

## 9. If you are the next agent

1. Read [../AGENTS.md](../AGENTS.md). Its hard rules are enforced by `tests/security/`.
2. Read [current_task.md](current_task.md) — the active task is a **person's**; your queue is
   its "carried over" list, plus collecting OQ-18 if it has finished.
3. Check what is running (§1) before anything CPU-heavy or any `uv` sync.
4. Check [DECISIONS.md](DECISIONS.md) before choosing any technology — **26 ADRs**. Disagree by
   writing a superseding ADR, never by drifting.
5. Check [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md). If your task rests on an `EXPERIMENT NEEDED`,
   run the experiment first — this session resolved two that way, and both measurements
   contradicted the arithmetic that preceded them.
6. On finishing: overwrite [current_report.md](current_report.md), update
   [current_task.md](current_task.md), write a dev log to `logs/development/` — **dead ends are
   the most valuable thing you can record** — then commit and push.

The project is design-first and measurement-first. The recurring pattern in its history is that a
number contradicted a design document and the document was corrected **in place**, with the number
attached. Keep doing that.
