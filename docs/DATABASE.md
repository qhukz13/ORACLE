# ORACLE — Data Model

Two SQLite files, deliberately separate. Rationale in
[ADR-0006](DECISIONS.md#adr-0006--sqlite-only-storage-two-files-sqlite-vec--fts5).

```
D:\ORACLE\data\
├── oracle.db       operational: sessions, events, tasks, projects, approvals, memory
└── knowledge.db    index: documents, chunks, vectors, symbols   ← DISPOSABLE
```

Both in WAL mode with `foreign_keys=ON`, `synchronous=NORMAL`, `busy_timeout=5000`.
Migrations are numbered `.sql` files applied by a small in-house runner recording `schema_version`.

`knowledge.db` being disposable is a design feature: a bad chunking change or a corrupt index is fixed
by deleting one file, and it can never damage session history.

---

## 1. `oracle.db`

### Events — the spine

```sql
CREATE TABLE events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,  -- global, gap-free, drives WS resume
  ts          TEXT    NOT NULL,                   -- ISO8601 UTC
  type        TEXT    NOT NULL,                   -- 'tool.finished', …
  session_id  TEXT REFERENCES sessions(id) ON DELETE CASCADE,
  turn_id     TEXT,
  task_id     TEXT REFERENCES tasks(id) ON DELETE SET NULL,
  trace_id    TEXT    NOT NULL,
  actor       TEXT    NOT NULL,                   -- user | agent | tool | system | external
  payload     TEXT    NOT NULL,                   -- JSON
  critical    INTEGER NOT NULL DEFAULT 0          -- never dropped under backpressure (API.md §2)
);
CREATE INDEX ix_events_session_seq ON events(session_id, seq);
CREATE INDEX ix_events_trace       ON events(trace_id);
CREATE INDEX ix_events_task        ON events(task_id, seq);
CREATE INDEX ix_events_type_ts     ON events(type, ts);
```

This one table underpins the WS resume protocol, the activity timeline, replay testing, and after-the-
fact debugging. `AUTOINCREMENT` is required, not incidental: it guarantees monotonicity even after
deletes, which the resume protocol depends on.

Retention: full events for 90 days; older turns collapse to a summary event, with the originals moved
to a compressed archive rather than dropped.

### Sessions, turns, tasks

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, last_active_at TEXT NOT NULL,
  title TEXT, project_id TEXT REFERENCES projects(id), origin TEXT NOT NULL  -- desktop|mobile|voice|api
);

CREATE TABLE turns (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  started_at TEXT NOT NULL, finished_at TEXT,
  user_text TEXT, intent TEXT, intent_confidence REAL,
  tainted INTEGER NOT NULL DEFAULT 0,
  context_json TEXT,          -- per-band token counts: how the budget was spent
  tokens_in INTEGER, tokens_out INTEGER,
  outcome TEXT                -- completed | cancelled | error
);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
  turn_id TEXT, project_id TEXT REFERENCES projects(id),
  kind TEXT NOT NULL,         -- plan | pipeline | delegation | index
  title TEXT NOT NULL,
  status TEXT NOT NULL,       -- queued|running|waiting|completed|failed|cancelled
  created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  cost_usd REAL DEFAULT 0, tokens_local INTEGER DEFAULT 0,
  result_json TEXT, error_json TEXT
);

CREATE TABLE steps (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL, tool TEXT NOT NULL,
  args_json TEXT NOT NULL, args_digest TEXT NOT NULL,   -- digest binds approvals
  tier TEXT NOT NULL, decision TEXT NOT NULL,           -- allow|confirm|deny
  approval_id TEXT REFERENCES approvals(id),
  status TEXT NOT NULL, started_at TEXT, finished_at TEXT, duration_ms INTEGER,
  result_json TEXT, output_blob TEXT,                   -- large output → blob hash
  undo_json TEXT                                        -- recipe, if reversible
);
```

`context_json` on `turns` is what makes "why did it answer that?" answerable months later — it records
how the scarce context budget was actually spent.

### Approvals

```sql
CREATE TABLE approvals (
  id TEXT PRIMARY KEY, trace_id TEXT NOT NULL,
  tool TEXT NOT NULL, args_digest TEXT NOT NULL,   -- recomputed at execution; mismatch aborts
  tier TEXT NOT NULL, reason TEXT, preview_json TEXT NOT NULL,
  requested_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  decided_at TEXT, decision TEXT,                  -- approve|deny|expired
  device_id TEXT REFERENCES devices(id), nonce TEXT NOT NULL,
  scope_rule_json TEXT                             -- if "always for X" was chosen
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

-- Named `memory_facts` in the built schema, not `facts`.
CREATE TABLE facts (                -- MEMORY.md §3
  id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_ref TEXT,
  key TEXT NOT NULL, value TEXT NOT NULL,
  confidence REAL NOT NULL, source TEXT NOT NULL, evidence_json TEXT,
  created_at TEXT NOT NULL, last_confirmed_at TEXT NOT NULL,
  hit_count INTEGER NOT NULL DEFAULT 0, superseded_by TEXT REFERENCES facts(id)
);
CREATE UNIQUE INDEX ux_facts_live ON facts(scope, scope_ref, key) WHERE superseded_by IS NULL;

CREATE TABLE attempts (             -- MEMORY.md §4 — prior attempts
  id TEXT PRIMARY KEY, task_signature TEXT NOT NULL, goal TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id), approach TEXT, agent TEXT,
  outcome TEXT NOT NULL, what_failed TEXT, files_json TEXT, at TEXT NOT NULL
);
CREATE INDEX ix_attempts_sig ON attempts(task_signature);

CREATE TABLE devices (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,   -- desktop|mobile|voice
  token_hash TEXT NOT NULL,        -- Argon2id; the raw token is never stored
  capabilities_json TEXT NOT NULL, -- e.g. max approvable tier
  paired_at TEXT NOT NULL, last_seen_at TEXT, revoked_at TEXT
);
```

The partial unique index on `facts` enforces one live fact per `(scope, scope_ref, key)` while keeping
superseded history — so "why does it believe that?" stays answerable.

`TO VERIFY` — the `facts`, `attempts` and `devices` blocks above are still the pre-build
sketch. The shipped tables are **`memory_facts`** and **`memory_attempts`** (migration
0003) and carry extra columns; `devices` is not built at all. Only the `projects` block has
been reconciled against source. Correct the rest when something next touches them, rather
than trusting the shape here.

---

## 2. `knowledge.db`

```sql
CREATE TABLE collections (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL,       -- code|markdown|pdf|mixed
  roots_json TEXT NOT NULL, config_json TEXT NOT NULL,
  last_indexed_at TEXT, doc_count INTEGER DEFAULT 0, chunk_count INTEGER DEFAULT 0
);

CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  project_id TEXT, path TEXT NOT NULL, rel_path TEXT NOT NULL,
  lang TEXT, mtime REAL NOT NULL, size INTEGER NOT NULL,
  content_hash TEXT NOT NULL,                    -- gates re-embedding
  provenance TEXT NOT NULL,                      -- local_owned | local_foreign  (SECURITY.md §6)
  indexed_at TEXT NOT NULL, parse_error TEXT
);
CREATE UNIQUE INDEX ux_documents_path ON documents(path);

CREATE TABLE chunks (
  id TEXT PRIMARY KEY,                           -- sha256(path + ordinal + text)
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL, text TEXT NOT NULL,
  anchor TEXT,                                   -- 'TokenService.refresh' | heading path
  line_start INTEGER, line_end INTEGER, token_count INTEGER NOT NULL
);

-- dense: brute-force KNN is correct at this corpus size (RAG.md §1)
CREATE VIRTUAL TABLE chunk_vectors USING vec0(
  chunk_id TEXT PRIMARY KEY, embedding FLOAT[1024]   -- the model's dim; 768 before 2026-08-24
);

-- lexical: BM25 over the same chunks, same file, same transaction
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text, anchor, ident, rel_path UNINDEXED, chunk_id UNINDEXED,
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE symbols (            -- code navigation and symbol-aware search
  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  name TEXT NOT NULL, kind TEXT NOT NULL,        -- function|class|method|type
  signature TEXT, line_start INTEGER, line_end INTEGER, parent TEXT
);
CREATE INDEX ix_symbols_name ON symbols(name);

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
