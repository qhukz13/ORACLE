# ORACLE — Data Model

Two SQLite files, deliberately separate. Rationale in
[ADR-0006](DECISIONS.md#adr-0006--sqlite-only-storage-two-files-sqlite-vec--fts5).

```
D:\ORACLE\data\
├── oracle.db       operational: sessions, events, tasks, approvals, memory, devices
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

```sql
CREATE TABLE projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
  kind TEXT,                        -- node|python|rust|roblox|mixed
  has_git INTEGER NOT NULL DEFAULT 0,
  test_command TEXT, build_command TEXT,
  card_json TEXT,                   -- README/AGENTS.md/CLAUDE.md summary
  last_seen_at TEXT
);

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
  chunk_id TEXT PRIMARY KEY, embedding FLOAT[768]
);

-- lexical: BM25 over the same chunks, same file, same transaction
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text, anchor, rel_path UNINDEXED, chunk_id UNINDEXED, tokenize='unicode61 remove_diacritics 2'
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

`tokenize='unicode61 remove_diacritics 2'` matters for Russian: the default tokenizer handles Cyrillic
poorly for the mixed RU/EN corpus this indexes. `TO VERIFY` against real Russian queries in the
Phase 5 retrieval fixture suite — a lexical tokenizer that silently mangles Cyrillic would degrade
half the hybrid search without any visible error.

**Vector dimension is fixed at index-build time.** Changing the embedding model requires a full
reindex — which is cheap here (minutes) and is exactly why the index is disposable.

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
- `knowledge.db` — **not backed up.** Rebuildable in minutes.
- Secrets — exported only by explicit manual action, never automatically.

`VACUUM INTO` is used rather than a file copy because it is safe against a live WAL connection.
