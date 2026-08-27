# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** **P12-T4 — the sidebar and the briefing, rendered.** Fourth of four today, after the
vision realignment, T1 (the project entity), T2 (the `continue` intent) and T3 (the briefing).
**Status:** Done, and **verified against a live daemon** rather than only against fixtures.
**Date:** 2026-08-26
**Dev logs:** [T4](../logs/development/2026-08-26-p12t4-sidebar-and-briefing.md) ·
[T3](../logs/development/2026-08-26-p12t3-briefing.md) ·
[T2](../logs/development/2026-08-26-p12t2-continue-intent.md) ·
[T1](../logs/development/2026-08-26-p12t1-project-entity.md) ·
[vision realignment](../logs/development/2026-08-26-vision-realignment.md)

---

## What shipped

`components/ProjectList.tsx`, `components/Briefing.tsx`, 34 UI tests and three new axe cases.
Three endpoints built earlier today stopped being invisible.

---

## It was verified by running it

The standing hazard here is a component that is green on a fixture the running app cannot
produce — `TaskTree.test.tsx` is exactly that. So this was driven end to end against a real
`oracled`:

```
GET  /api/v1/briefing                      → the unclean-restart notice, rendered
POST /api/v1/projects?name=ORACLE          → 200; candidates 10 → 9, ORACLE under PROJECTS
POST /api/v1/briefing/ack?through_seq=415  → 200; stage returns to chat
GET  /api/v1/briefing                      → "Nothing ran since 8/26/2026, 11:29:38 PM."
```

**The briefing's first real content was a genuine crash report.** I had killed a hung gate run
earlier with `Stop-Process`; the next boot found the last event was not a `system.shutdown` and
said *"ORACLE stopped unexpectedly and restarted at 23:29:38"*. The feature's first output was
true and about something that actually happened.

**Note:** that live run registered `ORACLE` as a project in your real database and acknowledged
the briefing through seq 415. Both are reversible and neither grants anything — but they are
real rows, so they are stated here rather than left to be discovered.

---

## What is deliberately not on the screen

**No branch, no dirty count, no ahead/behind — and a test asserts their absence.** Producing them
per row costs a `git` subprocess per project per render, and that fan-out is unmeasured
([OQ-24](OPEN_QUESTIONS.md#oq-24)). Caching them instead would make the sidebar lie the moment
somebody switches branches. An omission that is merely *intended* gets added back by the next
person who wants a branch name.

**Candidates are collapsed.** The live run found **10** — `New folder`, `Kaggle`, `docs.zip` and
the rest. Putting them in the briefing would destroy the property that makes it worth reading.

---

## Two things the live run taught that the tests could not

**A collapsed `<details>` still lists its contents in the accessibility tree.** My first click on
a candidate did nothing and *looked* successful — `read_page` showed all ten buttons, but they
were inside a closed `<details>`, so the click hit hidden content. The network log caught it:
three GETs, no POST. The accessibility tree is not a visibility oracle.

**`user-event` is not a dependency.** Both test files first imported it; adding a dependency
needs a TECH_STACK justification (AGENTS.md), and the repo already clicks with `fireEvent`.

---

## A doc correction, measured

`current_state.md` said *"`Inspector` is the one still uncovered"* by the axe audit. Counting it
found **three**: `Inspector`, `Citations` and `EgressPreview`. The audit now covers **12 of 15**
components. Carried into `current_task.md` — "the audit covers everything except X" is the kind
of sentence that stays written long after it stops being true.

---

## Next: P12-T5, and it is yours

**Phase 12 is complete except for the run itself.** T5 is *"continue ORACLE"* typed into the
command bar, and it is deliberately a person's: approvals expire in 180 s, so firing it
unattended writes a *refused* run into the very table the run exists to populate.

`tasks` has been **0 rows** for the entire life of the supervisor arc. Everything the sidebar
counts, everything the briefing computes, the execution tree, the orbit's go/no-go
([OQ-14](OPEN_QUESTIONS.md#oq-14)) and `TaskTree`'s fixture are all waiting on it.

It is also [OQ-25](OPEN_QUESTIONS.md#oq-25)'s first real evidence: whether a 0.8B router actually
classifies `continue` rather than confusing it with `run`.

[current_task.md](current_task.md) has the exact steps and what to write down. `oracle-selfcheck`
remains the cheaper first run — local, **no egress**, six steps, ~5 minutes.
