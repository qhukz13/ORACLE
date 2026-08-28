-- 0002_tasks — the durable task graph (docs/ORCHESTRATION.md §2 "Storage").
--
-- The row is the record, not the memory (Asterim's rule): a reconnecting client, a
-- crashed daemon, and a person six months later all read the same state. The table is a
-- projection the event log can rebuild — the events remain the source of truth
-- (ADR-0010) — which is why nothing here is a foreign key into `events`.
--
-- `spec` and `result` are JSON because their shape belongs to the pydantic models and
-- grows with the planner (PLANNER.md §3); columns exist only for what is queried:
-- the graph is loaded by `root_id`, and recovery scans by `status`.

CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    root_id      TEXT    NOT NULL,
    parent_id    TEXT,                     -- replanning lineage, NOT execution order
    plan_id      TEXT,                     -- the ExecutionPlan that authored it (P8)
    kind         TEXT    NOT NULL,         -- tool | delegation | planning | verify | report
    status       TEXT    NOT NULL,         -- see TaskStatus; TIMEOUT != FAILED, SKIPPED != CANCELLED
    agent        TEXT,                     -- resolved executor id; NULL until assignment
    spec         TEXT    NOT NULL,         -- JSON: TaskSpec
    depends_on   TEXT    NOT NULL,         -- JSON array of task ids
    attempt      INTEGER NOT NULL DEFAULT 1,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    supersedes   TEXT,                     -- the failed task this one replaces
    created_at   TEXT    NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    result       TEXT                      -- JSON: TaskResult (evidence and claim, separate)
);

-- The two queries that exist: load a graph, and find what was in flight when the daemon
-- died. Recovery is the reason the second one is an index and not a scan (ORCHESTRATION §3).
CREATE INDEX IF NOT EXISTS ix_tasks_root   ON tasks(root_id, created_at);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status);
