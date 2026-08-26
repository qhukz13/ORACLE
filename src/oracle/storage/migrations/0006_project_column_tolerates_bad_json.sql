-- 0006 — the `tasks.project` generated column must not detonate on one bad row.
--
-- A defect in 0005, found the same day by a test that was trying to assert something
-- else. `json_extract(spec, '$.project')` **raises** on malformed JSON rather than
-- returning NULL, and because the column is indexed the blast radius is the whole table:
--
--     sqlite> INSERT INTO t VALUES ('b', 'not json');   -- pre-existing row
--     sqlite> CREATE INDEX ix ON t(p);
--     OperationalError: malformed JSON
--     sqlite> SELECT id, p FROM t;
--     OperationalError: malformed JSON
--
-- So on any database that already held a task row with an unparseable `spec`, migration
-- 0005 would have **failed at CREATE INDEX**, and had it somehow got past that, every
-- subsequent read of the column — the counter rebuild, the unfinished-work query, the
-- whole projects surface — would raise. One corrupt row would take out the feature.
--
-- This is the same shape as the dead collection root that disabled live re-indexing for
-- every collection with a single absent path: a per-row fault escalating to a
-- subsystem-wide outage because nothing between them was tolerant.
--
-- It did not bite: `tasks` was 0 rows when 0005 applied, and `TaskStore.save()` only
-- ever writes `spec.model_dump_json()`, which is valid by construction. That is exactly
-- why it is worth fixing now — the conditions for it to bite are "somebody hand-edits a
-- row" or "a write is torn", and both arrive without warning.
--
-- `json_valid()` answers the question without raising, so a malformed row now yields
-- NULL: the task is simply not attributed to any project, which is the honest answer,
-- and every other row keeps working. Verified: the index still builds over a malformed
-- row, and `EXPLAIN QUERY PLAN` still reports `SEARCH ... USING INDEX ix_tasks_project`.
--
-- The column is rebuilt rather than 0005 being edited. An applied migration is a
-- historical fact: editing one leaves every database that ran it disagreeing with the
-- file that claims to describe it.

DROP INDEX IF EXISTS ix_tasks_project;

ALTER TABLE tasks DROP COLUMN project;

ALTER TABLE tasks ADD COLUMN project TEXT
    GENERATED ALWAYS AS (
        CASE WHEN json_valid(spec) THEN json_extract(spec, '$.project') END
    ) VIRTUAL;

CREATE INDEX IF NOT EXISTS ix_tasks_project ON tasks(project, status);
