-- 0007_briefing — somewhere to keep a daemon-level scalar, and a way to find a task's
-- events without scanning the log.
--
-- docs/PROJECT_STATE.md §6.
--
-- **`meta`.** `projects.briefed_through_seq` gives every project its own watermark, but
-- the briefing also has a system section — a daemon that died overnight, a provider that
-- was unreachable — and those events belong to no project. Without a watermark of their
-- own they would reappear in every briefing forever, which is exactly the "notification
-- you learn to skip" failure §6 is shaped to avoid.
--
-- `oracle.db` has had no home for a daemon-level scalar. `schema_version` is a table
-- because it is a list, not a scalar; everything else has lived on a row that owns it.
-- One key-value table is a smaller thing to reason about than a one-column table per
-- scalar, and `knowledge.db` already has exactly this (`rag/store.py`), so it is a shape
-- the project already reads without thinking.
--
-- Deliberately NOT a place for configuration. Config lives in `config/*.yaml` where a
-- human edits it and git records the edit (SECURITY.md). This is for values the daemon
-- computes about itself.
--
-- **`ix_events_task`.** The briefing asks "which tasks in this project saw activity after
-- seq N", which joins `events.task_id` to `tasks.id`. `events` already has indexes on
-- (session_id, seq), (trace_id) and (type, ts) — none of which helps that join, so it
-- would be a full scan of the log on every render. The log is ~400 rows today and will
-- not stay that way.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_events_task ON events(task_id, seq) WHERE task_id IS NOT NULL;
