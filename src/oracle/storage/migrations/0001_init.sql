-- 0001_init — sessions and the event log.
-- docs/DATABASE.md. The `events` table is the spine: WS resume, replay tests,
-- the activity timeline and the audit trail all read from it.

CREATE TABLE IF NOT EXISTS sessions (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    title          TEXT,
    project_id     TEXT,
    origin         TEXT NOT NULL          -- desktop | browser | mobile | voice | api
);

-- AUTOINCREMENT is required, not incidental: it guarantees monotonic seq even after
-- deletes, which the `since_seq` resume protocol depends on.
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    type       TEXT    NOT NULL,
    session_id TEXT    REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id    TEXT,
    task_id    TEXT,
    trace_id   TEXT    NOT NULL,
    actor      TEXT    NOT NULL,          -- user | agent | tool | system | external
    payload    TEXT    NOT NULL,          -- JSON
    critical   INTEGER NOT NULL DEFAULT 0 -- never dropped under backpressure
);

CREATE INDEX IF NOT EXISTS ix_events_session_seq ON events(session_id, seq);
CREATE INDEX IF NOT EXISTS ix_events_trace       ON events(trace_id);
CREATE INDEX IF NOT EXISTS ix_events_type_ts     ON events(type, ts);
