-- 0003_memory — what ORACLE learned, as opposed to what it can look up (docs/MEMORY.md).
--
-- Two tables, because MEMORY.md §2 names four kinds of memory and only two of them need
-- machinery: working memory is the turn object, and episodic memory is the event log,
-- which is already durable, ordered and queryable. Building a third store for either
-- would be a second source of truth for something that already has one.
--
-- Facts and preferences share `memory_facts`. §2 lists them as separate rows in a table
-- of *kinds*, not as separate stores: they have the same shape, the same write policy,
-- the same conflict rule and the same "why does ORACLE think that?" requirement, so one
-- table with a `kind` column is one thing to reason about instead of two that must be
-- kept in step.
--
-- Nothing here is deleted by the system. A fact that loses a conflict is marked
-- `superseded_by` and stays readable, because "why does it think that?" has to be
-- answerable about beliefs it no longer holds (§3) — and because a memory system whose
-- corrections are invisible is one nobody can audit.

CREATE TABLE IF NOT EXISTS memory_facts (
    id                TEXT PRIMARY KEY,
    kind              TEXT    NOT NULL,          -- fact | preference
    scope             TEXT    NOT NULL,          -- global | project | collection
    scope_ref         TEXT,                      -- e.g. "Asterim"; NULL for global
    key               TEXT    NOT NULL,          -- "test_command"
    value             TEXT    NOT NULL,          -- "pnpm test"
    confidence        REAL    NOT NULL DEFAULT 1.0,
    source            TEXT    NOT NULL,          -- user_stated | user_corrected | observed | inferred
    evidence          TEXT    NOT NULL,          -- JSON array: event ids / file paths
    origin            TEXT    NOT NULL DEFAULT '',  -- the turn/task that caused this write
    created_at        TEXT    NOT NULL,
    last_confirmed_at TEXT    NOT NULL,
    hit_count         INTEGER NOT NULL DEFAULT 0,
    superseded_by     TEXT                       -- the fact that replaced it; never deleted
);

-- The one read that happens on every assembled turn: live facts for a scope, by key.
-- Partial on `superseded_by IS NULL` because superseded rows are only ever read by the
-- Memory view answering "why did it used to think that?".
CREATE INDEX IF NOT EXISTS ix_memory_live
    ON memory_facts(scope, scope_ref, key) WHERE superseded_by IS NULL;

-- Prior attempts (MEMORY.md §4): the highest-value memory for a delegation-oriented
-- agent, and the one most systems omit. Separate from `tasks` on purpose — a task row is
-- what the supervisor is doing now, and an attempt is what was tried, in a vocabulary
-- that outlives the graph it happened in. The task id is kept so the two can be joined,
-- not so one can be derived from the other.
--
-- `claim` is absent, and that is the enforcement rather than a convention: what a worker
-- said about its own work is not evidence, and an attempt is read back into a planning
-- prompt and a handoff packet, which are the two places prose becomes instructions.

CREATE TABLE IF NOT EXISTS memory_attempts (
    id             TEXT PRIMARY KEY,
    task_signature TEXT    NOT NULL,          -- normalised goal + project, for matching
    goal           TEXT    NOT NULL,
    project        TEXT    NOT NULL DEFAULT '',
    approach       TEXT    NOT NULL DEFAULT '',
    agent          TEXT    NOT NULL DEFAULT '',
    outcome        TEXT    NOT NULL,          -- success | failure | abandoned
    what_failed    TEXT,                      -- the actual error, ORACLE's own record
    files_touched  TEXT    NOT NULL DEFAULT '[]',  -- JSON array
    task_id        TEXT,                      -- the row this was recorded from
    at             TEXT    NOT NULL
);

-- Matching is signature-first with a token-overlap fallback (memory/attempts.py), so the
-- signature is the index and the project narrows it.
CREATE INDEX IF NOT EXISTS ix_memory_attempts_sig
    ON memory_attempts(task_signature, at);
CREATE INDEX IF NOT EXISTS ix_memory_attempts_project
    ON memory_attempts(project, at);
