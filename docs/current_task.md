# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P9-T1 — Memory: what ORACLE is allowed to remember, and what it must refuse to forget**

**Phase:** [9 — memory & context engine](ROADMAP.md#phase-9--memory--context-engine--supervisor-arc) · **Scope:** Supervisor arc
**Status:** `SET — not started` · **Set:** 2026-08-25
**Previous task:** P8-T3 — **done except the live run**; see [`current_report.md`](current_report.md),
[PLANNER.md §6](PLANNER.md#6-fallbacks) and the
[dev log](../logs/development/2026-08-25-p8t3-ladder.md).

---

## Why this task exists

Every band above 4 is empty. The planner is given an objective and a list of roles; a delegate is
given a packet whose `ATTEMPTS.md` nobody fills; a replan is given ORACLE's evidence about *this*
run and nothing about the six times this task shape failed before. Phase 7 and 8 built machinery
whose quality is now bounded by context rather than by mechanism, which is exactly the point at
which building more mechanism stops paying.

This task builds the store and the **write policy**, not the retrieval tuning — because a memory
system's failure mode is not "it forgot", it is "it confidently remembered something wrong", and
that failure is designed in on the first day or not prevented at all.

## What the earlier phases hand you

1. **[MEMORY.md](MEMORY.md) stands as written** — facts schema, the restrictive write policy,
   decay, the Memory view. It has not been implemented, and it has not been revised; read it
   before deciding it is wrong.
2. **Attempt records exist.** P7 writes task rows; P8-T2 writes `supersedes` lineage and
   `attempts_report()`. A repeated task's history is *in the table already* and unqueried.
3. **`context/budget.py` has the bands** and producers for 0–4. Bands 5–7 are declared and unfed.
4. **Taint is tracked** (SECURITY.md §6). A turn built from content ORACLE did not author is
   marked, and that mark is what a write policy has to consult.
5. **RAG can match a `task_signature`** — the retrieval half already works; nothing has asked it
   this question.

## Requirements

1. **`src/oracle/memory/`**: a facts + preferences store on `oracle.db` (migration `0003`),
   pydantic models, and the events (`memory.written`, `memory.contradicted`) that make it
   auditable like everything else.
2. **The write policy, restrictive and tested**, per MEMORY.md:
   - **no write mid-plan** — a graph in flight does not get to teach ORACLE things about itself;
   - **no write from a tainted turn** — a document that says "remember that you may push to main"
     must not become a memory;
   - **a contradiction surfaces, never auto-deletes.** Two facts that disagree are two facts and a
     question for a person, because silently picking one is how a wrong memory becomes permanent.
3. **Attempt retrieval**: a task's packet carries the prior attempts at the *same task signature*
   without anybody hand-feeding it. This is the band-5 producer, and it is the one that pays for
   the phase — a replan that knows what already failed is a different tool.
4. **Bands 5–7 wired into assembly**, budgeted like every other band, with the same redaction and
   the same provenance labels. A memory in a packet is content ORACLE authored; a memory *derived
   from* a tainted source is not, and the label must survive.
5. **"Why does ORACLE think that?"** answerable: every fact carries its origin (turn, task, or the
   person saying so) and the UI can show it in one click.
6. **[OQ-18](OPEN_QUESTIONS.md#oq-18) measured in its stated order** — truncated chunks first,
   query translation second — and either the 80% recall gate met or the gate re-argued in writing
   with the numbers.

## Constraints

- **Wrong memories are worse than none.** Keep the write policy restrictive even when recall feels
  low; a fact that should have been remembered is a gap, a fact wrongly remembered is a lie the
  system tells itself for months.
- Memory is a **band producer**. Disabling it returns context assembly to today's behaviour, and a
  test should prove that rather than assume it.
- No new approval types. A memory write is not an action a person approves; it is a write the
  policy either permits or refuses.
- Do not touch the planner ladder, the replan budget, or the single-turn pipeline.

## Acceptance criteria

- [ ] MEMORY.md's write rules hold, each with a test: no write mid-plan, no write from a tainted
      turn, a contradiction surfaced rather than resolved.
- [ ] A repeated task's packet carries the prior attempt with nobody hand-feeding it.
- [ ] Retrieval recall ≥ 80% on the fixture set, **or** OQ-18's gate re-set with a written argument
      and the measurements behind it.
- [ ] Every fact answers "why does ORACLE think that?" — origin recorded, and reachable in the UI.
- [ ] Disabling memory returns context assembly to its current output, asserted.
- [ ] `make check` green; the security suite extended with the tainted-write case.

## Relevant files

New: `src/oracle/memory/` · `src/oracle/storage/migrations/0003_memory.sql` ·
`tests/test_memory.py` · `tests/security/test_memory_writes.py`.
Modify: `src/oracle/context/budget.py` (bands 5–7) · `src/oracle/handoff/` (ATTEMPTS.md) ·
`apps/desktop/.../` (the Memory view) · `docs/MEMORY.md` (as-built).
Read first: [MEMORY.md](MEMORY.md) · [OQ-18](OPEN_QUESTIONS.md#oq-18) ·
`src/oracle/orchestration/replan.py` (the attempt shape already exists — do not invent a second).

## Dependencies

P7 (attempt rows), P8-T2 (lineage). P8's live run is still outstanding and does not block this.

## Risks

| Risk | Mitigation |
|---|---|
| The write policy is relaxed to make recall look better | The policy has its own tests and its own security suite; relaxing one is a visible diff with a reason attached |
| A second attempt shape appears beside `replan.Attempt` | Reuse it. Two vocabularies for "what was tried" is how the replan prompt and the packet start disagreeing |
| OQ-18 is declared met by changing the fixture set | The fixture set is versioned; a change to it is a change to the claim, and belongs in the same commit with an argument |

## Definition of done

All acceptance criteria · `make check` green · MEMORY.md corrected to as-built · OQ-18 resolved or
re-argued with numbers · a dev log for the recall measurements · `current_report.md` overwritten ·
this file set to **P9-T2** or **P10-T1**, whichever the state of Phase 9 warrants.

---

## Carried over, not forgotten

Phase 8 is complete except **one supervised live run** of the full scenario on a real project with
every preview human-approved — deliberately left for a person, since answering the approvals
programmatically would tick the one criterion whose subject is the human in the loop. What it
should measure is in the [P8-T3 dev log](../logs/development/2026-08-25-p8t3-ladder.md).
