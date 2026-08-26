# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** **P12-T1 — the `projects` table and the registry.** Plus the vision realignment that
scheduled it, done earlier the same day.
**Status:** Done. `make check` **green, 7/7** (ruff · mypy · tsc · pytest 131 s · security 133 s ·
vitest 12 s). **1,133 Python tests**, up 62.
**Date:** 2026-08-26
**Dev logs:** [P12-T1](../logs/development/2026-08-26-p12t1-project-entity.md) ·
[vision realignment](../logs/development/2026-08-26-vision-realignment.md)

---

## What shipped

A project is no longer a directory name. Migration `0005`,
[`core/project_state.py`](../src/oracle/core/project_state.py) (550 lines), three endpoints, and 62
tests — 41 unit, 14 API, 7 security.

| | |
|---|---|
| `projects` table | identity, status, description + provenance, `first_seen`/`last_touched`, the briefing pointer, and four counters |
| Registry | register (idempotent by name) · rename · relocate · archive · touch · `refresh_presence` |
| `ProjectObservation` | branch, ahead/behind, dirty count, last commit — read fresh through `git.status`/`git.log`, both T0 |
| Counters | rebuilt from `tasks` by `recount()`; reconciled at boot along with presence |
| API | `GET /api/v1/projects` · `POST /api/v1/projects?name=` · `GET /api/v1/projects/{id}` |

---

## The rule the design turned on

> **If git knows it, do not store it. If only ORACLE knows it, store it.**

*Observed state* (branch, dirty count, build commands) belongs to git. Storing it makes a cache that
lies: switch branches in an editor and the sidebar is wrong, silently, with no event that could
correct it. *Relational state* (what ORACLE attempted, what it cost, when you last looked) has no
second copy anywhere, so it must be stored.

**`DATABASE.md` had specified the opposite.** Its pre-build `projects` sketch carried `kind`,
`has_git`, `test_command`, `build_command` and a README summary — every one of them observed state.
Corrected in place with the reason attached. Its `facts`/`attempts`/`devices` blocks are **still the
old sketch** and are now marked `TO VERIFY`; the shipped tables are `memory_facts` and
`memory_attempts`, and `devices` does not exist.

Three tests defend the rule rather than describe it, because "just cache it, git is slow" arrives
later and a schema will not resist it on its own — including one that reads `PRAGMA table_info` and
fails if a column named `branch` or `dirty` ever appears.

---

## Four things worth carrying forward

**The index needed no column.** `TaskSpec.project` lives inside the `spec` JSON, so indexing `tasks`
by project looked like a column plus a write-path change plus a backfill. It was a **generated
column** — `json_extract(spec, '$.project') VIRTUAL` — which *is* `spec` rather than a second copy of
it, so it cannot drift and nothing on the write path changed. Verified on a **copy of the live
database**, v4 → v5, with `EXPLAIN QUERY PLAN` confirming `SEARCH tasks USING INDEX ix_tasks_project`.

**`PRAGMA table_info` does not list generated columns.** That verification nearly recorded a false
negative: `table_info` reported no `project` column on a successfully migrated database. Only
`table_xinfo` shows them (`hidden=2`). Anything that introspects the schema to decide whether a
migration applied will conclude the opposite of the truth. Pinned as a test.

**Existence is observed state too, and I nearly stored it.** The first version reconciled `MISSING`
at boot and stopped there — the same stale-cache bug as a cached branch name, one field coarser.
Every surface now reports `effective_status()`, the stored value corrected by a fresh `is_dir()`.
`ARCHIVED` is never overridden.

**A vocabulary duplicated on purpose.** `recount()` needs the terminal task statuses, and importing
`orchestration.models.TERMINAL` would point a dependency upward, which ARCHITECTURE.md §4 forbids.
So it is duplicated, with `test_terminal_set_matches_orchestration` proving the copies agree.

---

## Deferred, and named rather than hidden

**[OQ-24](OPEN_QUESTIONS.md#oq-24) — the observation fan-out is unmeasured.** So
`GET /api/v1/projects` runs **no git at all** and omits branch and dirty count; only the per-project
endpoint observes. The arithmetic says ~13 projects ≈ 1 s against a 3–5 s glance budget, but
arithmetic is exactly what ADR-0004 got wrong about `qwen3.5:2b`. **If it misses, the answer is lazy
per-row observation — never a cache**, written into the OQ now while it is obvious.

---

## Security

Both claims are asserted, not documented, in `tests/security/test_project_registration.py`:

- **Registering grants nothing.** A path that raised `PathRejected` before registration still raises
  it after — which is the question that matters, since comparing scope lists would miss a widening
  that happened elsewhere.
- **Observation crosses the gate.** The module is checked against its own AST for a
  `subprocess`/`os` import and for any call named `run`/`Popen`/`system`/`spawn`, the same way
  `test_no_shell.py` checks the shell ban. A third test pins that the tools it may ask for are a
  **subset of `{git.status, git.log}`**, so an edit adding `git.stash` to "clean the tree before
  looking" fails rather than mutating someone's working copy on a page-load.

Traversal in the API is refused before any path is built: `name` must be a directory
`discover_projects()` actually found, with `../Secret`, `C:\Windows` and friends enumerated as tests.

---

## Next

**[P12-T2](current_task.md)** — the `continue` intent and unfinished-work derivation. Note the
warning carried into that file: adding an `IntentLabel` touches a **measured** surface (93.3% over 30
fixtures), so the eval must be **re-run**, not assumed — and `make eval` is documented in TESTING.md
§8 and defined nowhere, which puts it on the critical path.

**Still a person's job, ~5 minutes:** run `oracle-selfcheck` once. `tasks` is still **0 rows**, so
P11's orbit, timeline and queue continue to render activity that has never happened. P12-T5 produces
richer data but costs tokens and egress; the pipeline is local, no egress, one approval card.
