# P12-T2 — "continue Asterim" becomes answerable

**2026-08-26** · `core/unfinished.py`, an eleventh `IntentLabel`, migration `0006`, 51 tests

T1 built the entity. This makes it readable: the vision's headline utterance now resolves a
project, reads what is actually left, and hands a planner an objective built from evidence —
or asks, when there is nothing to build one from.

Design: [PROJECT_STATE.md §5](../../docs/PROJECT_STATE.md#5-unfinished-work--where-continue-gets-its-list).

---

## The ordering is the whole design

Three sources, strictly ranked:

1. **ORACLE's own task graph — authoritative.** Non-terminal tasks, plus `FAILED`/`TIMEOUT`
   ones with nothing superseding them.
2. **The repository's own task documents — evidence.** `TODO.md`, `docs/current_task.md`,
   `ROADMAP.md`. `local_foreign`, quoted, attributed, never obeyed.
3. **Never the planner's imagination.** Both empty → a **question**, not a plan.

The third is the one worth defending. `objective_of()` returns `None` for an empty derivation
and the caller asks, because a planner handed a project name and nothing else will produce
plausible work — and plausible work is worse than none: it is unfalsifiable, and it costs a
worktree and a delegation to discover it was invented.

`test_an_empty_derivation_yields_no_objective` exists because "just ask the planner, it'll
figure something out" is the tempting shortcut and it looks like helpfulness right up until
somebody approves it.

---

## Migration 0006 — a generated column that could detonate

**The most useful thing found today, and it was found by a test trying to assert something
else.**

T1 indexed `tasks` by project with a generated column:

```sql
ALTER TABLE tasks ADD COLUMN project TEXT
    GENERATED ALWAYS AS (json_extract(spec, '$.project')) VIRTUAL;
```

`json_extract` **raises on malformed JSON** rather than returning NULL. Because the column is
indexed, the blast radius is the whole table:

```
sqlite> INSERT INTO t VALUES ('b', 'not json');   -- a pre-existing row
sqlite> CREATE INDEX ix ON t(p);
OperationalError: malformed JSON
sqlite> SELECT id, p FROM t;
OperationalError: malformed JSON
```

So on any database already holding one task row with an unparseable `spec`, **migration 0005
would have failed at `CREATE INDEX`** — and had it got past that, every read of the column
would raise: the counter rebuild, the unfinished-work query, the whole projects surface.

This is the same shape as the dead collection root that disabled live re-indexing for *every*
collection with one absent path. A per-row fault escalating to a subsystem-wide outage,
because nothing in between was tolerant.

It did not bite: `tasks` was 0 rows when 0005 applied, and `TaskStore.save()` only ever writes
`spec.model_dump_json()`, which is valid by construction. That is exactly why it was worth
fixing now — the conditions for it to bite are "somebody hand-edits a row" and "a write is
torn", and both arrive without warning.

`json_valid()` answers the question without raising:

```sql
GENERATED ALWAYS AS (CASE WHEN json_valid(spec) THEN json_extract(spec, '$.project') END)
```

A malformed row is now simply unattributed. Verified: the index still builds over a malformed
row, and `EXPLAIN QUERY PLAN` still reports `SEARCH ... USING INDEX ix_tasks_project`.

**0005 was not edited.** An applied migration is a historical fact; editing one leaves every
database that ran it disagreeing with the file that claims to describe it. 0006 drops the
index, drops the column, and rebuilds both.

---

## Taint buys attribution, not escalation

The plan was to have repo notes escalate the confirmation tier. Reading `approve_graph` showed
that would be theatre: the graph card **already** evaluates as `Provenance.EXTERNAL` at T2,
because ADR-0021 treats every plan as untrusted input. There is no further escalation to give.

What was missing was not severity but **provenance**. `approve_graph` now takes
`untrusted_sources`, and the card names the files whose text is inside the objective. That is
the fact a person actually needs — not "this is risky" (everything here is) but "part of this
objective was written by `docs/current_task.md`, so read the plan sceptically".

The acceptance criterion I had written for myself said "escalates the confirmation tier by
exactly one". It was wrong, and the honest fix was to change the criterion rather than to add
an escalation that does nothing so the sentence could stay true.

---

## The injection surface, fenced three times

Reading someone's `TODO.md` into a planner prompt is a prompt-injection channel **by
construction** — carrying their prose into ORACLE's reasoning is the entire feature. So:

1. **Scope** — the read goes through `fs.read`, so the policy engine resolves the path. A
   project registered outside every scope cannot have its files read by asking ORACLE to
   continue it. (`read_agent_docs` predates the gate and reads directly; new code does not.)
2. **Framing** — quoted inside a fence named after the file, under a heading that says it is
   untrusted, with ORACLE's own record rendered *first*. Order is a defence: a note above
   ORACLE's findings reads as the brief; below them it reads as a source.
3. **Authority** — the plan that comes back is still validated, still cannot name its own
   executor, and still reaches an approval card that names the file.

`tests/security/test_continue_evidence.py` runs five injection payloads through the renderer,
including one that forges the closing fence, and checks the real `PolicyEngine` refuses an
out-of-scope note and a traversal.

---

## Two smaller things

**A test one reformat away from asserting nothing.** `test_only_one_tool_is_ever_reached`
grepped the source for `execute("`. `ruff format` then wrapped that call across two lines, and
the grep matched zero calls — caught only because the assertion was `== {"fs.read"}` rather
than a subset check, so an empty set failed loudly instead of passing vacuously.

That is luck, not design. Had it been written as `assert "dev.execute" not in called`, it would
have gone green forever while checking nothing. Rewritten over the AST. The general lesson is
both halves: a source-inspection test that matches on **text** is one reformat from being
inert, and an assertion phrased as an **absence** cannot tell "nothing bad" from "nothing at
all".

**A fake that invented a file.** The `_FakeExecutor` matched paths by suffix, so `docs/TODO.md`
was served the body registered for `TODO.md` and the test reported a document it had never set
up. Now resolved against the root. A fixture that invents data is worse than no fixture,
because it looks like coverage.

---

## The eval was not re-run

Deliberately, at the owner's direction. `IntentLabel` gained an eleventh member and intent
accuracy (**93.3%**, 30 fixtures, ten labels) has not been re-measured.

Recorded as [OQ-25](../../docs/OPEN_QUESTIONS.md#oq-25) with what shipped *instead* of a
measurement, so the deferral is not a blank cheque: the system prompt states the
`continue` / `run` boundary explicitly rather than leaving it inferable, four few-shots cover
it including a Russian one, and a test asserts both — so a future edit cannot quietly delete
the mitigation.

The named risk is that *"run the Asterim tests"* and *"continue Asterim"* are one word apart to
a 0.8B classifier. A wrong route here is recoverable — the user sees the intent on the turn and
rephrases — which is why it blocks nothing.

Also noted while writing it up: **`make eval` is documented in TESTING.md §8 and defined
nowhere**, so the documented way to resolve OQ-25 does not currently exist.

---

## Two things about the gate itself

**`ruff check src/oracle` is not what the gate runs.** It runs `ruff check src tests`, and two
lint errors sat in a new test file through three green-looking local checks because I kept
narrowing the path to the thing I had just edited. The habit that costs nothing and would have
caught it: run the gate's own command, not a subset of it.

**A gate run hung in the `pytest` step under concurrent load** — thirteen minutes at near-zero
CPU, where the same set passes in ~125 s. Re-run on a quiet machine it passed. I was running
other `uv run` commands against the same tree while it went, so the most likely cause is
contention rather than a defect, and nothing in it is reproducible on demand.

Worth recording because it rhymes with a carried item: `test_a_long_burst_arrives_complete`
already *fails* under CPU starvation, losing lines of a ConPTY burst. That one is in
`tests/security/`; this hang was in the non-security half, so they are not the same symptom.
If a hang recurs, the first useful step is `pytest -p no:cacheprovider --durations=20` on a
quiet machine — there is no `pytest-timeout` installed, which is itself worth fixing before
somebody has to bisect a hang by hand a third time.

---

## Not built in T2

The briefing (T3), the sidebar and inspector (T4), and the first real end-to-end run (T5).
`tasks` is still 0 rows — every path added today is exercised by fixtures only, which is the
condition T5 exists to end.
