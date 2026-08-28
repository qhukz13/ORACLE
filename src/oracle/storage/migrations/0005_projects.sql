-- 0005_projects — a project becomes a thing rather than a directory name.
--
-- docs/PROJECT_STATE.md · ADR-0024. Until now a "project" was whatever
-- `discover_projects()` found on disk this boot, and `memory_facts`, `memory_attempts`
-- and `TaskSpec` were all keyed by a project *string* with no entity behind the key.
--
-- The one rule this table encodes (PROJECT_STATE.md §2): **if git knows it, do not store
-- it.** There is deliberately no branch, no dirty count, no last-commit, no test command
-- and no file inventory here. Those are read fresh through the tool layer every time
-- they are shown, because a cached branch name is wrong the moment someone switches
-- branches in their editor — silently, with no event that could correct it.
--
-- What IS here is the half nothing else holds: ORACLE's own record of its relationship
-- with the project.

CREATE TABLE IF NOT EXISTS projects (
    id                  TEXT PRIMARY KEY,          -- "pj_..."; stable across renames
    name                TEXT NOT NULL UNIQUE,      -- what the intent classifier matches
    root                TEXT NOT NULL,             -- absolute, canonicalised at register time
    status              TEXT NOT NULL,             -- active | idle | archived | missing
    description         TEXT NOT NULL DEFAULT '',
    -- Provenance of `description`: 'user' if a person wrote it, 'derived' if ORACLE
    -- produced it from repository content. A derived description carries the taint of
    -- whatever it was derived from (PROJECT_STATE.md §7), so the reader must be able to
    -- tell the two apart without guessing.
    description_source  TEXT NOT NULL DEFAULT 'user',
    first_seen          TEXT NOT NULL,
    -- The last time ORACLE itself did something here. NOT the last commit: that is
    -- observed state and is read from git.
    last_touched        TEXT,
    -- Where the briefing resumes from (PROJECT_STATE.md §6). Advances on acknowledgement
    -- only, never on render — a briefing that clears itself on sight is a notification,
    -- and notifications are how people miss things.
    briefed_through_seq INTEGER NOT NULL DEFAULT 0,

    -- Denormalised counters. A PROJECTION, never a source: they are rebuildable from
    -- `tasks` and a counter that disagrees with the task table is a bug in the
    -- projection, repaired by recomputing. They exist because the briefing has a 3-5
    -- second budget and must not aggregate the task table per project per render.
    open_tasks          INTEGER NOT NULL DEFAULT 0,
    failed_tasks        INTEGER NOT NULL DEFAULT 0,
    tokens_spent        INTEGER NOT NULL DEFAULT 0,
    usd_spent           REAL    NOT NULL DEFAULT 0.0
);

-- `name` is UNIQUE and `id` is the identity. Renaming a directory re-points the label;
-- it must not orphan the facts and attempts recorded against the row.
CREATE INDEX IF NOT EXISTS ix_projects_status ON projects(status, name);

-- The project a task belongs to lives inside `spec` (TaskSpec.project), which is JSON.
-- A generated column rather than a real one, for a reason worth stating: a real column
-- would be a second copy of a fact `spec` already holds, and two copies of a fact drift.
-- This one cannot — it *is* `spec`, read through an expression — and SQLite will still
-- index it.
--
-- VIRTUAL rather than STORED because ALTER TABLE permits only VIRTUAL, and because there
-- is nothing to store: the value is already on the row.
ALTER TABLE tasks ADD COLUMN project TEXT
    GENERATED ALWAYS AS (json_extract(spec, '$.project')) VIRTUAL;

-- The counter rebuild reads exactly this: every task for one project, by status.
CREATE INDEX IF NOT EXISTS ix_tasks_project ON tasks(project, status);
