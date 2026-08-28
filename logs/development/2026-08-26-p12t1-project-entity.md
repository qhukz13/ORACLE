# P12-T1 — a project stops being a directory name

**2026-08-26** · migration `0005`, `core/project_state.py`, three endpoints, 41 + 14 + 7 tests

The vision audit earlier today found that ORACLE had no notion of a project beyond a string.
`memory_facts`, `memory_attempts` and `TaskSpec` were all *keyed* by a project name with nothing
behind the key, and `UI.md §4` already drew `Asterim  2 tasks  branch main +3` — a line whose every
number came from a subsystem that did not exist. This is that subsystem's durable half.

Design: [PROJECT_STATE.md](../../docs/PROJECT_STATE.md) ·
decision: [ADR-0024](../../docs/DECISIONS.md#adr-0024--a-project-is-a-first-class-persistent-entity).

---

## The rule the whole thing hangs on

Writing the design out forced a distinction I did not have going in, and it turned out to decide
every subsequent question:

> **If git knows it, do not store it. If only ORACLE knows it, store it.**

*Observed state* — branch, ahead/behind, dirty count, last commit, build commands — belongs to git
and the filesystem. Storing it produces a cache that lies: switch branches in an editor and the
sidebar is wrong, silently, with no event that could correct it. `git status` on a warm repository
costs single-digit milliseconds, so the cache buys nothing and forfeits correctness.

*Relational state* — what ORACLE attempted here, what it left unfinished, what it cost, when the
owner last looked — has no second copy anywhere. That is the table.

**`docs/DATABASE.md` had sketched the opposite.** Its pre-build `projects` table carried `kind`,
`has_git`, `test_command`, `build_command` and a README summary — every one of them observed state.
That sketch has been corrected in place with the reason attached, which is the second time this week
a document written before the code turned out to have designed the wrong thing.

Three tests defend the rule rather than describe it, because the pressure to "just cache it, git is
slow" arrives later and a schema alone will not resist it:

- `test_the_projects_table_stores_nothing_git_owns` reads `PRAGMA table_info` and fails if a column
  named `branch`, `dirty`, `test_command` (and eight others) ever appears.
- `test_the_list_endpoint_reports_no_observed_state` fails if the list API grows those fields.
- `test_effective_status_corrects_a_stale_row` covers the case I nearly got wrong — see below.

---

## Four things the implementation settled

### The index on `tasks(project, status)` did not need a column

`TaskSpec.project` lives inside the `spec` JSON blob, so "index `tasks` by project" first looked like
it needed a real column plus a write-path change plus a backfill. It needed none of those:

```sql
ALTER TABLE tasks ADD COLUMN project TEXT
    GENERATED ALWAYS AS (json_extract(spec, '$.project')) VIRTUAL;
CREATE INDEX ix_tasks_project ON tasks(project, status);
```

A real column would be a second copy of a fact `spec` already holds, and two copies of a fact drift.
This one cannot — it *is* `spec`. Nothing on the write path changed, `TaskStore` is untouched, and
there is no backfill to get wrong.

Verified on a **copy of the live database**, not just on a fresh one:

```
before: schema_version=4 tasks=0 events=381
after:  schema_version=5 projects=0 events=381
query plan: SEARCH tasks USING INDEX ix_tasks_project (project=? AND status=?)
```

### A trap: `PRAGMA table_info` does not list generated columns

That verification nearly recorded a false negative. `PRAGMA table_info(tasks)` reported **no
`project` column** on the successfully-migrated database. Only `PRAGMA table_xinfo` lists generated
columns, with `hidden=2` for `VIRTUAL`.

Anything that introspects the schema to decide whether a migration applied — a health check, a repair
script, a future migration guarding itself — will conclude the opposite of the truth. Pinned in
`test_generated_columns_are_hidden_from_table_info` so the next person meets it as a passing test
rather than as a confusing hour.

### Existence is observed state too, and I almost stored it

The first version reconciled `MISSING` at boot with `refresh_presence()` and left it there. That is
the same stale-cache bug as a cached branch name, only with a coarser field: delete a directory at
10:00 and the sidebar says `idle` about it until the next restart.

The fix is `effective_status()` — the stored status corrected by a fresh `is_dir()` on every read.
`ARCHIVED` is never overridden, because a project deliberately set aside is archived whether or not
its directory survives, and reporting it as missing would invite someone to "fix" it.

I only noticed because a test I had written to assert "one absent path degrades one row, not the
surface" was passing while asserting the wrong status.

### The status vocabulary is duplicated on purpose

`recount()` needs to know which task statuses are terminal, and `orchestration.models.TERMINAL`
already says. Importing it would point a dependency **upward** — `core` reaching into the supervisor
— which ARCHITECTURE.md §4 forbids and which `memory.attempts.from_task` already dodges by taking an
`Any`.

So `TERMINAL_STATUSES` is duplicated, and `test_terminal_set_matches_orchestration` asserts the two
agree. Duplication with a test that proves it cannot drift is cheaper than an architectural
violation; duplication without one is how vocabularies fork.

---

## What was deferred, and named

**The observation fan-out has no measurement**, so `GET /api/v1/projects` runs **no git at all** and
omits branch and dirty count entirely; only the per-project detail endpoint observes. The arithmetic
says ~13 projects × (27.9 ms warm IPC + a warm `git status`) ≈ 1 s against a 3–5 second glance budget
— but arithmetic is exactly what [ADR-0004](../../docs/DECISIONS.md#adr-0004--two-tier-local-model-router--reasoner)
got wrong about `qwen3.5:2b`, so it is [OQ-24](../../docs/OPEN_QUESTIONS.md#oq-24) rather than a
number in a doc.

**The answer, if it misses, is lazy per-row observation — never a cache.** Written into OQ-24 now,
while it is obvious, rather than left for whoever hits the latency later.

---

## Security

Two claims, both asserted rather than documented, in
`tests/security/test_project_registration.py`:

**Registering grants nothing.** A `projects` row is a label on work. Register a directory that sits
outside every policy scope and the engine's scope fingerprint is unchanged — and, more to the point,
a path that raised `PathRejected` before registration still raises it after. Comparing scope lists
alone would miss a widening that happened somewhere else; asking whether the path resolves is the
question that actually matters.

**Observation crosses the gate.** `observe()` reaches git only through `git.status` and `git.log`,
both T0. The module is checked against its own AST for a `subprocess`/`os` import and for any call
named `run`/`Popen`/`system`/`spawn`, the same way `test_no_shell.py` checks the shell ban — because
"it goes through the tool layer" is an architectural claim, and architectural claims decay the moment
one import looks convenient. A third test pins that the set of tools it may ask for is a **subset of
`{git.status, git.log}`**, so an edit that added `git.stash` to "clean the tree before looking" would
fail rather than turn a page-load into a mutation of someone's working copy.

---

## Not built in T1

The `continue` intent, unfinished-work derivation, the briefing, and the sidebar rewrite. Each
depends on the entity, which is why the entity was first. T2 is the `continue` intent — and note that
adding an `IntentLabel` touches a **measured** surface (93.3% over 30 fixtures), so it requires
re-running the eval rather than assuming it holds.
