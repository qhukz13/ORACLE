# ORACLE — Data Model

Two SQLite files, deliberately separate. Rationale in
[ADR-0006](DECISIONS.md#adr-0006--sqlite-only-storage-two-files-sqlite-vec--fts5).

```
D:\ORACLE\data\
├── oracle.db       operational: sessions, events, tasks, projects, memory, meta
└── knowledge.db    index: documents, chunks, vectors, links   ← DISPOSABLE
```

Both in WAL mode with `foreign_keys=ON`, `synchronous=NORMAL`, `busy_timeout=5000`.
Migrations are numbered `.sql` files applied by a small in-house runner recording `schema_version`.

`knowledge.db` being disposable is a design feature: a bad chunking change or a corrupt index is fixed
by deleting one file, and it can never damage session history.

---

## 1. `oracle.db`

### Events — the spine

```sql
-- migration 0001, reconciled against source 2026-08-28.
CREATE TABLE events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,  -- global, gap-free, drives WS resume
  ts          TEXT    NOT NULL,                   -- ISO8601 UTC
  type        TEXT    NOT NULL,                   -- 'tool.finished', …
  session_id  TEXT REFERENCES sessions(id) ON DELETE CASCADE,
  turn_id     TEXT,
  task_id     TEXT,       -- deliberately NOT a foreign key: tasks are a projection the
                          -- log can rebuild (ADR-0010), and the log must not depend on it
  trace_id    TEXT    NOT NULL,
  actor       TEXT    NOT NULL,                   -- user | agent | tool | system | external
  payload     TEXT    NOT NULL,                   -- JSON
  critical    INTEGER NOT NULL DEFAULT 0          -- never dropped under backpressure (API.md §2)
);
CREATE INDEX ix_events_session_seq ON events(session_id, seq);
CREATE INDEX ix_events_trace       ON events(trace_id);
CREATE INDEX ix_events_type_ts     ON events(type, ts);
-- ix_events_task arrived with the briefing (migration 0007) and is partial; it is shown
-- in the projects section below, not here.
```

This one table underpins the WS resume protocol, the activity timeline, replay testing, and after-the-
fact debugging. `AUTOINCREMENT` is required, not incidental: it guarantees monotonicity even after
deletes, which the resume protocol depends on.

Retention: full events for 90 days; older turns collapse to a summary event, with the originals moved
to a compressed archive rather than dropped.

### Sessions and tasks

**Reconciled against source 2026-08-28.** The pre-build sketch here had four tables —
`turns`, `tasks`, `steps`, `approvals` — and three of them were never built as tables,
because [ADR-0010](DECISIONS.md#adr-0010--events-are-the-source-of-truth) already gives
each of them a home:

| Sketch table | Where the concept actually lives |
|---|---|
| `turns` | an in-memory object while live; durable as its `turn.*` / `agent.state` events |
| `steps` | `tool.started` / `tool.finished` events, plus the hash-chained audit log for the security record |
| `approvals` | `approval.requested` / `approval.decided` events; expiry is in-memory, which is *why* an unanswered card simply lapses at 180 s and a daemon restart cannot resurrect it |

A second durable copy of any of these would be a projection that gets to disagree with the
log. The one projection that earned a table is the task graph, because the scheduler, crash
recovery and a person six months later all need to read it without folding the log first:

```sql
-- migration 0002 (+0004 timeout_s, +0005/0006 the generated project column, shown below).
-- The row is the record, not the memory: a projection the event log can rebuild, which is
-- why nothing here is a foreign key into `events`.
CREATE TABLE tasks (
  id           TEXT PRIMARY KEY,
  root_id      TEXT    NOT NULL,
  parent_id    TEXT,                     -- replanning lineage, NOT execution order
  plan_id      TEXT,                     -- the ExecutionPlan that authored it (P8)
  kind         TEXT    NOT NULL,         -- tool | delegation | planning | verify | report
  status       TEXT    NOT NULL,         -- TaskStatus; TIMEOUT != FAILED, SKIPPED != CANCELLED
  agent        TEXT,                     -- resolved executor id; NULL until assignment
  spec         TEXT    NOT NULL,         -- JSON: TaskSpec
  depends_on   TEXT    NOT NULL,         -- JSON array of task ids
  attempt      INTEGER NOT NULL DEFAULT 1,
  max_attempts INTEGER NOT NULL DEFAULT 1,
  supersedes   TEXT,                     -- the failed task this one replaces
  created_at   TEXT    NOT NULL,
  started_at   TEXT,
  finished_at  TEXT,
  result       TEXT,                     -- JSON: TaskResult (evidence and claim, separate)
  timeout_s    REAL                      -- 0004: NULL = the per-kind default
);
CREATE INDEX ix_tasks_root   ON tasks(root_id, created_at);
CREATE INDEX ix_tasks_status ON tasks(status);  -- recovery scans by status, not the log

CREATE TABLE sessions (   -- migration 0001
  id             TEXT PRIMARY KEY,
  created_at     TEXT NOT NULL,
  last_active_at TEXT NOT NULL,
  title          TEXT,
  project_id     TEXT,   -- no FK: sessions predate the projects table (0001 vs 0005)
  origin         TEXT NOT NULL          -- desktop | browser | mobile | voice | api
);
```

### Projects, memory, devices

**Corrected 2026-08-26 against the built schema.** The sketch this section used to carry
stored `kind`, `has_git`, `test_command`, `build_command` and a README summary — that is,
**everything git and the filesystem already know**. [ADR-0024](DECISIONS.md#adr-0024--a-project-is-a-first-class-persistent-entity)
rejected exactly that shape: a stored branch or build command is wrong the moment someone
switches branches or edits their `package.json`, silently, with no event that could correct
it. Detection is by marker file, on demand ([PROJECT_STATE.md §2](PROJECT_STATE.md#2-the-distinction-that-makes-this-design-work)).

```sql
-- migration 0005. Relational state only: what ORACLE did here, not what git can answer.
CREATE TABLE projects (
  id TEXT PRIMARY KEY,              -- "pj_..."; identity, stable across a rename
  name TEXT NOT NULL UNIQUE,        -- the label the intent classifier resolves
  root TEXT NOT NULL,
  status TEXT NOT NULL,             -- active|idle|archived|missing
  description TEXT NOT NULL DEFAULT '',
  description_source TEXT NOT NULL DEFAULT 'user',   -- user|derived (taint provenance)
  first_seen TEXT NOT NULL,
  last_touched TEXT,                -- when ORACLE acted here; NOT the last commit
  briefed_through_seq INTEGER NOT NULL DEFAULT 0,    -- PROJECT_STATE.md §6
  -- A projection over `tasks`, rebuildable and never authoritative. Present only
  -- because the briefing has a 3-5 second budget.
  open_tasks INTEGER NOT NULL DEFAULT 0,
  failed_tasks INTEGER NOT NULL DEFAULT 0,
  tokens_spent INTEGER NOT NULL DEFAULT 0,
  usd_spent REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX ix_projects_status ON projects(status, name);

-- The project a task belongs to lives inside `spec` (TaskSpec.project). A GENERATED
-- column rather than a real one: a real column would be a second copy of a fact `spec`
-- already holds, and two copies drift. This one cannot -- it *is* `spec`.
ALTER TABLE tasks ADD COLUMN project TEXT
  GENERATED ALWAYS AS (json_extract(spec, '$.project')) VIRTUAL;
CREATE INDEX ix_tasks_project ON tasks(project, status);

-- migration 0007. Daemon-level scalars — values ORACLE computes about itself.
-- Deliberately NOT configuration: that lives in config/*.yaml where a human edits it and
-- git records the edit. Today it holds `briefing.system_seq`, the watermark for briefing
-- items that belong to no project (a restart, a degradation).
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- The briefing asks "which tasks in this project saw activity after seq N", which joins
-- events.task_id to tasks.id. None of the log's other indexes helps that join.
CREATE INDEX ix_events_task ON events(task_id, seq) WHERE task_id IS NOT NULL;

-- migration 0003, reconciled against source 2026-08-28. Facts and preferences SHARE this
-- table: MEMORY.md §2 lists them as rows of different `kind`, not as different stores —
-- same shape, same write policy, same conflict rule, so one table to reason about.
CREATE TABLE memory_facts (
  id                TEXT PRIMARY KEY,
  kind              TEXT NOT NULL,              -- fact | preference
  scope             TEXT NOT NULL,              -- global | project | collection
  scope_ref         TEXT,                       -- e.g. "Asterim"; NULL for global
  key               TEXT NOT NULL,              -- "test_command"
  value             TEXT NOT NULL,              -- "pnpm test"
  confidence        REAL NOT NULL DEFAULT 1.0,
  source            TEXT NOT NULL,              -- user_stated | user_corrected | observed | inferred
  evidence          TEXT NOT NULL,              -- JSON array: event ids / file paths
  origin            TEXT NOT NULL DEFAULT '',   -- the turn/task that caused this write
  created_at        TEXT NOT NULL,
  last_confirmed_at TEXT NOT NULL,
  hit_count         INTEGER NOT NULL DEFAULT 0,
  superseded_by     TEXT                        -- the fact that replaced it; never deleted
);
CREATE INDEX ix_memory_live
  ON memory_facts(scope, scope_ref, key) WHERE superseded_by IS NULL;

-- migration 0003. Prior attempts (MEMORY.md §4) — separate from `tasks` on purpose: a
-- task row is what the supervisor is doing now; an attempt is what was tried, in a
-- vocabulary that outlives the graph it happened in. `claim` is ABSENT as enforcement,
-- not convention: what a worker said about its own work is not evidence, and attempts
-- are read back into planning prompts and handoff packets — the two places where prose
-- becomes instructions.
CREATE TABLE memory_attempts (
  id             TEXT PRIMARY KEY,
  task_signature TEXT NOT NULL,                 -- normalised goal + project, for matching
  goal           TEXT NOT NULL,
  project        TEXT NOT NULL DEFAULT '',      -- by NAME, not projects.id — see below
  approach       TEXT NOT NULL DEFAULT '',
  agent          TEXT NOT NULL DEFAULT '',
  outcome        TEXT NOT NULL,                 -- success | failure | abandoned
  what_failed    TEXT,                          -- the actual error, ORACLE's own record
  files_touched  TEXT NOT NULL DEFAULT '[]',    -- JSON array
  task_id        TEXT,                          -- the row this was recorded from (a join, not a derivation)
  at             TEXT NOT NULL
);
CREATE INDEX ix_memory_attempts_sig     ON memory_attempts(task_signature, at);
CREATE INDEX ix_memory_attempts_project ON memory_attempts(project, at);
```

The partial index on `memory_facts` serves the one read every assembled turn makes — live
facts for a scope, by key — while superseded rows stay readable, so "why does it believe
that?" is answerable about beliefs it no longer holds. Note it is an ordinary index, not the
unique one an earlier sketch promised: the one-live-fact rule is enforced by the write path
(supersede-then-insert), and the index only has to make the live read cheap.

**`memory_attempts.project` is a name, not a foreign key** — written before `projects` grew
identity rows (migration 0005). Both tables are 0 rows today, so ROADMAP's Phase 12 note
stands: re-keying is an empty backfill *now* and a real migration the day after the first
attempt is recorded.

**`devices` is not built.** It is Phase 14's pairing table (MOBILE.md: token hash, per-device
capability ceiling, revocation), and nothing shipped references a device id — approvals are
events, not rows, and they carry no device column. It gets its DDL here when P14 builds it,
not before.

---

## 2. `knowledge.db`

**Reconciled against source 2026-08-28** (`src/oracle/rag/store.py::_schema`). Two sketch
tables were never built: **`collections`** — collection definitions live in
`config/collections.yaml`, where a human edits them and git records the edit, and storing a
second copy in the disposable file would let the two disagree — and **`symbols`**, which
waits on the tree-sitter chunker (RAG.md §3; the shipped chunker is regex-approximate and
carries no symbol spans to store). `meta` holds the schema/model stamp whose mismatch makes
`stale: true` in the health view.

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE documents (
  id            TEXT PRIMARY KEY,          -- collection/path, stable across machines
  collection_id TEXT NOT NULL,             -- names a config/collections.yaml entry; no FK
  project_id    TEXT,
  path          TEXT NOT NULL,             -- absolute, for opening the citation
  rel_path      TEXT NOT NULL,             -- corpus-relative, for displaying it
  kind          TEXT NOT NULL,
  mtime_ns      INTEGER NOT NULL,
  size          INTEGER NOT NULL,
  content_hash  TEXT NOT NULL,             -- gates re-embedding; mtime alone lies on Windows
  provenance    TEXT NOT NULL,             -- local_owned | local_foreign (SECURITY.md §6)
  indexed_at    TEXT NOT NULL,
  parse_error   TEXT
);
CREATE UNIQUE INDEX ux_documents_rel ON documents(collection_id, rel_path);

CREATE TABLE chunks (
  id          TEXT PRIMARY KEY,            -- sha256(rel_path + ordinal + text)
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal     INTEGER NOT NULL,
  text        TEXT NOT NULL,
  anchor      TEXT,                        -- 'TokenService.refresh' | heading path
  token_count INTEGER NOT NULL
);
CREATE INDEX ix_chunks_document ON chunks(document_id);

-- dense: brute-force KNN is correct at this corpus size (RAG.md §1). Partitioned by
-- collection so a scoped search never scans the whole space.
CREATE VIRTUAL TABLE chunk_vectors USING vec0(
  chunk_id      TEXT PRIMARY KEY,
  collection_id TEXT partition key,
  project_id    TEXT,
  embedding     FLOAT[1024]                -- the model's dim; 768 before 2026-08-24
);

-- lexical: BM25 over the same chunks, same file, same transaction
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text, anchor, ident, rel_path UNINDEXED, chunk_id UNINDEXED,
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE links (              -- Obsidian [[wikilinks]] → one-hop expansion
  from_document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  to_path TEXT NOT NULL, kind TEXT NOT NULL,
  PRIMARY KEY (from_document_id, to_path, kind)
);
```

**The tokenizer, corrected 2026-08-22 by measurement** ([OQ-08](OPEN_QUESTIONS.md#oq-08),
[log](../logs/development/2026-08-22-oq08-fts5-russian.md)). The claim that once stood here — that
`unicode61` "handles Cyrillic poorly" — is **wrong**: it case-folds Cyrillic correctly in both
directions, and `remove_diacritics 2` is a Latin concern that neither helps nor harms Russian. The
two things it genuinely cannot do are why the schema above has an `ident` column:

* **No stemming.** `токен` does not match `токена`, and Russian is inflected. Handled at query time
  by prefix-expanding Cyrillic terms (`токен*`), not in the schema.
* **No camelCase splitting**, and no configuration of `unicode61` can add it — `separators` adds
  separator *characters*, and a case transition is not one. So `ident` holds each identifier exploded
  into its parts (`entitlementGuard` → `entitlement Guard`), written at index time. An unqualified
  `MATCH` searches every column, so `entitlement` finds the row without the query knowing why.

**Vector dimension is fixed at index-build time.** Changing the embedding model requires a full
reindex, and that is exactly why the index is disposable.

**A full reindex costs hours on a cold cache, not "minutes".** The corpus as of 2026-08-24 is
1,414 documents and 14,586 chunks, embedded by `bge-m3` (1024d) on 24 Haswell threads into a
**140 MB** file. What that costs depends entirely on the embedding cache, which is keyed by
`sha256(text)` and lives outside this database:

| | measured |
|---|---|
| Cold — first build after the 2026-08-24 model switch | **~3 h** (extrapolated at 1.37 chunks/s) |
| Warm — index deleted, chunking unchanged | **3.6 min** (100% cache hit, 53 chunks re-embedded) |
| Incremental, nothing changed | seconds |

The `e5-base` equivalents were 42.8 min cold and an 85 MB file; the model switch trades roughly
3x the cold build and 65% more disk for the recall in [OQ-02](OPEN_QUESTIONS.md#oq-02).
Disposability is still real, but a cold rebuild costs an afternoon of background CPU, which makes
the incremental path load-bearing rather than a convenience — see [OQ-17](OPEN_QUESTIONS.md#oq-17).

---

## 3. Not in the databases

| Data | Where | Why |
|---|---|---|
| Security audit | `logs/audit/*.jsonl`, hash-chained | append-only and tamper-evident; a DB row is editable |
| Large tool output, screenshots | `blobs/<sha256>` on disk | keeps rows small; content-addressed and deduplicated |
| Secrets | Windows Credential Manager (DPAPI) | never in a file I might copy or back up carelessly |
| Models | `D:\ORACLE\models` | multi-GB, re-downloadable |
| Policy | `config/policy.yaml` | must be human-editable and outside the agent's reach |

## 4. Backup

- `oracle.db` — nightly `VACUUM INTO` snapshot, keep 7. **This is the irreplaceable file.**
- `logs/audit/` — included in the snapshot; never truncated.
- `knowledge.db` — **not backed up.** Rebuildable in **~43 minutes** of background CPU, measured, not
  the "minutes" this line used to claim. Still not worth backing up: an hour of idle CPU is cheaper
  than a backup that can silently restore a stale or corrupt index.
- Secrets — exported only by explicit manual action, never automatically.

`VACUUM INTO` is used rather than a file copy because it is safe against a live WAL connection.
