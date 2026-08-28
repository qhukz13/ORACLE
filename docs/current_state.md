# ORACLE — Current State

> **Hand-off brief for an agent picking this project up cold.** Rewritten **2026-08-28** against
> source, the live databases and the running processes — not against the other docs. Where a doc
> and the code disagree, that disagreement is recorded here rather than smoothed over.
>
> This is a **snapshot** — overwrite it, do not append. For *what to do next* read
> [current_task.md](current_task.md); for *what was just done* read
> [current_report.md](current_report.md); for *the rules you must follow* read
> [../AGENTS.md](../AGENTS.md) first; for *what the product is for* read [VISION.md](VISION.md).

---

## 1. Read this first: what is running on the machine right now

| | |
|---|---|
| **OQ-18 corpus run** | **RUNNING**, PID 46076, started 2026-08-28 00:47. See §2 — it needs a decision. |
| `oracled` | up on 127.0.0.1:8787, started by the previous session |
| Ollama | up, `qwen3.5:0.8b` resident, started by the previous session |
| A pending T3 approval | `ai.delegate`, from the P12-T5 run. Unanswered. Harmless; it expires. |

**Do not kill processes by pattern.** A `Stop-Process` sweep matching `python` destroyed the first
OQ-18 attempt on 2026-08-27 (`LastTaskResult 3221225786` = `STATUS_CONTROL_C_EXIT`). Kill by PID,
and check what you are about to kill.

---

## 2. The one open decision: OQ-18 is 4× over its wall budget

`scripts/run_oq18_eval.cmd` says *"~2.5–3 hours of CPU on all cores"*. It has been running
**13+ hours**.

**It is not stuck.** Measured 2026-08-28 14:0x:

| | |
|---|---:|
| CPU accumulating | **19.4 cores** sustained (581 CPU-s per 30 s wall) |
| Disk I/O over 25 s | **0 reads, 0 writes** — normal for ONNX inference between checkpoints |
| CPU-seconds so far | ~93,500 of a ~250,000 budget (24 cores × 3 h) → **~37%** |
| RSS | ~1.6 GB, stable |

At the current rate it needs roughly **another 2 hours**. The 13 hours elapsed are mostly a long
stretch where it was not getting CPU; about an hour of that is explained by the previous session
running the full gate four times alongside it. **The rest is unexplained.**

**Why you cannot see progress:** the script redirects stdout to
`logs/measurements/oq18-translated.txt`, and Python **block-buffers** stdout when redirected. The
file has been 63 bytes since it started and will stay that way until the run ends. *An unchanging
file is not a stuck job here.* Check CPU on the worker instead.

> **A trap that has already cost time twice.** `.venv\Scripts\python.exe` is a **uv shim** — it is
> idle by design and its *child* does the work. Checking CPU on the shim shows 0 and looks exactly
> like a hang. The chain is `cmd.exe 45904 → shim 45692 → worker 46076`.

**The decision is yours to make, and both answers are defensible:**

- **Let it finish** (~2 h). It is genuinely computing and 37% done.
- **Kill it and fix the observability first.** It has no progress output, no checkpoints, and its
  own `ExecutionTimeLimit: PT6H` did not fire at 6 hours — so nothing about this job can currently
  be supervised. A run you cannot observe is one you cannot trust the timing of.

If you kill it: `Stop-Process -Id 46076,45692,45904 -Force`, then re-fire with
`python -u` (unbuffered) added to the `.cmd` so the next run is observable.

On collection: compose `dense_mt` against `dense_xl`, confirm or flip
`Settings.translate_queries`, decide `en-relay-dockerfile`, resolve
[OQ-18](OPEN_QUESTIONS.md#oq-18), state the answer-key correction wherever pre-2026-08-26 recall
numbers are quoted, then `Unregister-ScheduledTask -TaskName ORACLE-OQ18-eval`.

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

**The governing idea:** the most common correct action is not to call the LLM at all. What the
product is *for* — as a day rather than a diagram — is [VISION.md](VISION.md).

---

## 4. Where the project actually is

```
 P0–P6  foundation                        done
 P7–P9  supervisor arc                    done
 P10    pipelines                         done
 P11    execution vis & advanced UI       IN PROGRESS — T1/T3/T4 done; T5 deferred; T2 blocked on data
 P12    project state & the continue loop  T1–T5 all done 2026-08-26/28  ← the last five commits
 P13    residency, boot & the briefing    next
 P14 mobile · P15 voice · P16 tiers (GPU-conditional) · P17 hardening
```

**Phase 12 shipped in one arc** ([ROADMAP.md](ROADMAP.md#phase-12--project-state--the-continue-loop--residency-arc)):
a project became a durable entity (ADR-0024), `continue <project>` became answerable, the briefing
was built, both surfaces were rendered, and the loop was run for real.

### The caveat that still matters most

**`tasks` is 0 rows.** It always has been. The P12-T5 run reached the egress approval and stopped
there — correctly, because approving it is a person's job — so no plan was authored and no graph
compiled. Everything that renders supervisor activity is still rendering it from fixtures:

- [OQ-14](OPEN_QUESTIONS.md#oq-14), the orbital view's go/no-go — still unanswerable
- the execution tree's acceptance criteria — still unjudgeable
- `TaskTree.test.tsx` — still green on a fixture the running app cannot produce
- the sidebar's counters and the briefing's arithmetic — never fed real input

**One human click ends this.** Either approve the pending T3 card, or run `oracle-selfcheck`
(local, **no egress**, six steps, one card, ~5 min) which produces a real six-task graph.

### Branch

```
  phase6-integration   <- HEAD, 61 commits ahead of origin/main, pushed
  origin/main          <- stale, Phase 5-era
```

The branch name is a fossil: Phases 6–12 all live on it. Whether to merge or rename is a decision
nobody has made.

---

## 5. Data — the real numbers

**`D:\ORACLE\data\oracle.db`** — schema **v7**, WAL. Back it up; it is not rebuildable.

| Table | Rows |
|---|---:|
| `events` | 461 |
| `sessions` | 16 |
| `projects` | **1** (`ORACLE`, registered by the T4 live run) |
| `meta` | 1 (`briefing.system_seq`) |
| `tasks` | **0** |
| `memory_facts` / `memory_attempts` | **0** / **0** |

**`D:\ORACLE\knowledge.db`** — 142 MB, disposable. Delete to force a full reindex.

Migrations added this arc: `0005_projects`, `0006_project_column_tolerates_bad_json`,
`0007_briefing`.

---

## 6. Tests and the gate

**1,237 Python tests** · **277 UI tests** · `tests/security/` is a merge gate and is not optional.

```
uv run python scripts/check.py     # ruff format -> ruff lint -> mypy -> tsc -> pytest -> security -> vitest
```

**Gate status: GREEN**, 7/7, run 2026-08-28 at `6cba956`.

> **Run the gate's own command, not a subset.** Three separate failures in this arc came from
> checking less than the gate does: `ruff check src/oracle` instead of `src tests`; `tsc` run
> *before* the test files existed; and tests passing while referencing an undefined `aiosqlite`
> (annotations are strings under `from __future__ import annotations`, so only ruff caught it).
> **Passing tests are evidence about behaviour, not about the code being well-formed.**

---

## 7. What the last session found by running things

Two defects that fixture tests could not have found, both on the safety surface:

**A planning card that understated its own egress** (P12-T5, fixed). It hardcoded
`sends_repo_contents: False` while sending 2,820 characters of `docs/current_task.md` and
`docs/ROADMAP.md`, and the gate priced the call `tainted: False` seconds after `continue.derived`
recorded `tainted: true`. Now escalates T2 → **T3 `confirm_strong`** and computes the flag. The
rule, in [SECURITY.md §6](SECURITY.md): **a preview field that is a literal is a claim nobody
re-checks.**

**A generated column that could detonate** (P12-T1/0006, fixed). `json_extract` *raises* on
malformed JSON, and the column was indexed — one corrupt `spec` row would have failed the
migration at `CREATE INDEX` and made every subsequent read raise. `json_valid()` guards it.

And one measurement finding: **`continue` routes correctly but its project slot does not.**
`intent: continue` both runs; `project: null` both runs, on an input whose second word is a
registered project. A string-matching fallback (`_named_project`, written for `delegate`) is
carrying it. [OQ-25](OPEN_QUESTIONS.md#oq-25).

---

## 8. Known defects and doc-vs-code drift

1. **`make eval` and `make perf`** are documented in TESTING.md §8 and **defined nowhere**.
   [OQ-25](OPEN_QUESTIONS.md#oq-25)'s documented resolution path therefore does not exist.
2. **The intent eval has not been re-run** since `continue` became an eleventh label — deferred
   deliberately by the owner, mitigations shipped and pinned instead.
3. **[OQ-24](OPEN_QUESTIONS.md#oq-24): the project-observation fan-out is unmeasured**, so
   `GET /api/v1/projects` runs no git and the sidebar shows no branch or dirty count. **If it
   misses the budget, observe lazily per row — never cache.**
4. **The a11y audit covers 12 of 15 components.** Uncovered: `Inspector`, `Citations`,
   `EgressPreview`. (An earlier revision of this file claimed only `Inspector`; that was wrong.)
5. **The briefing's inspector affordance is a stopgap** — `onInspect` routes a *task* id into the
   *turn* selector, because the inspector has no task branch. That is P11-T5.
6. **`TaskTree.test.tsx` is green on a fixture the app cannot produce** — `store.ts` never
   populates `dependsOn`. Re-record from the wire after the first real graph run.
7. **`DATABASE.md`'s `facts`/`attempts`/`devices` blocks are still the pre-build sketch.** Only
   `projects`, `meta` and the task/event indexes have been reconciled against source. The shipped
   tables are `memory_facts` and `memory_attempts`; `devices` is not built.
8. **The `chunker_version` guard does not fire on the indexes it was written for.** 57% of 14,586
   live rows exceed the 1200-char cap, longest 4,055. The database wants a reindex.
9. **A merge-gate test fails under CPU starvation** (`test_a_long_burst_arrives_complete`), and
   **`pytest-timeout` is not installed**, which makes a hang expensive to bisect. One gate run
   hung under concurrent load on 2026-08-26 and passed on retry.
10. **Palette results are not discoverable to assistive tech** — `<li role="option">` with
    `onClick`, no `role="combobox"`, no `aria-activedescendant`.
11. **A correction typed while a graph runs is refused**, because "never mid-plan" is implemented
    literally. The fix, when somebody hits it, is a queue — not an exception.
12. **The visual references for the UI vision were never attached** and are not in the repository.
    UI.md §1/§14/§15 are `TO VERIFY` against them; two surfaces shipped without them.

---

## 9. If you are the next agent

1. Read [../AGENTS.md](../AGENTS.md). Its hard rules are enforced by `tests/security/`.
2. Read [current_task.md](current_task.md) — that is your assignment.
3. Decide the OQ-18 question in §2 before starting anything CPU-heavy; the gate takes ~6 minutes
   and will contend with it.
4. Check [DECISIONS.md](DECISIONS.md) before choosing any technology — **26 ADRs**. Disagree by
   writing a superseding ADR, never by drifting.
5. Check [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md). If your task rests on an `EXPERIMENT NEEDED`, run
   the experiment first.
6. On finishing: overwrite [current_report.md](current_report.md), update
   [current_task.md](current_task.md), write a dev log to `logs/development/YYYY-MM-DD-<slug>.md`
   — **dead ends are the most valuable thing you can record** — then commit and push.

**Two habits this arc earned the hard way.** Run the gate's own command rather than a faster
subset (§6). And when something looks stuck, check what it is actually doing — CPU on the right
process, and I/O counters — before killing it; a pattern-matched `Stop-Process` already destroyed
one three-hour measurement here.

The project is design-first and measurement-first. The recurring pattern in its history is that a
number contradicted a design document and the document was corrected **in place**, with the number
attached. Keep doing that.
