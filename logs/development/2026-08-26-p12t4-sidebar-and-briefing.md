# P12-T4 — three endpoints stop being invisible

**2026-08-26** · `ProjectList.tsx`, `Briefing.tsx`, 34 UI tests · **verified against the live
daemon**, not only against fixtures

T1 built the project entity, T2 taught `continue` to read it, T3 built the briefing — and none
of it had ever been on a screen. This is the first Phase 12 task to touch `apps/desktop/`.

Surfaces: [UI.md §4](../../docs/UI.md#4-sidebar) · [UI.md §7b](../../docs/UI.md).

---

## It was verified by running it

The standing hazard in this repository is a component that is green on a fixture the running app
cannot produce — `TaskTree.test.tsx` is exactly that, and it was written before there was any
way to check. There is now, so this was checked end to end against a real `oracled`:

```
GET  /api/v1/briefing            → the unclean-restart notice, rendered
POST /api/v1/projects?name=ORACLE → 200; candidates 10 → 9, ORACLE appears under PROJECTS
POST /api/v1/briefing/ack?through_seq=415 → 200; stage returns to chat
GET  /api/v1/briefing            → "Nothing ran since 8/26/2026, 11:29:38 PM."
```

Two things that only a live run could have shown:

**The briefing's first real content was a genuine crash report.** I had killed a hung gate run
earlier in the day with `Stop-Process`, so the daemon's next boot found the last event was not a
`system.shutdown` and said so: *"ORACLE stopped unexpectedly and restarted at 23:29:38"*. The
feature's first output was true and about something that actually happened, which is a better
test than any fixture.

**`through_seq=415` on the acknowledgement is the number that was displayed.** That is the rule
T3's design leans on, and it is visible in the network log rather than only in a unit test.

---

## Fixtures recorded from the wire, deliberately

Both test files use the **snake_case, every-field-present** shape the API actually serialises,
not a convenient camelCase object. `toProjects` / `toBriefing` are the seam, and they are tested
against garbage too — `null`, a string where a list belongs, a status the server has never sent.
A parser that throws takes the whole sidebar down for one bad field.

One small rule fell out of that: **an unknown status clamps to `idle`.** A class name built from
server text is how a typo becomes an unstyled row nobody notices.

---

## What is deliberately not on the screen

**No branch, no dirty count, no ahead/behind** — and a test asserts their absence rather than
trusting the omission to survive. Producing them per row costs a `git` subprocess per project
per render, and that fan-out is unmeasured ([OQ-24](../../docs/OPEN_QUESTIONS.md#oq-24)).
Caching them instead would make the sidebar lie the moment somebody switches branches in their
editor, which is the failure the whole subsystem is shaped around.

**Candidates are collapsed.** The real projects root holds `New folder`, `Kaggle` and
`docs.zip` alongside the real ones — the live run showed **10 candidates** — and putting them in
the briefing would destroy the one property that makes it worth reading.

---

## Two accessibility rules, enforced rather than intended

**Status is a word on every row, not only a colour**, and the word is present for the calm rows
too. A label that appears only on failure is one nobody learns to read.

**Each `inspect` button names what it inspects.** Eight buttons all called "inspect" is unusable
in a screen reader's element list — the same defect the six identical "cancel" buttons had
before P11. `a11y.test.tsx` asserts the labels are *distinct*, not merely present.

Three new axe cases (project list, briefing, briefing-empty), so the standing gate holds rather
than acquiring a phase-shaped hole.

**Counted rather than assumed, and the count was worse than the docs said.** The audit now
renders **12 of 15** components. `current_state.md` claimed *"`Inspector` is the one still
uncovered"*; measuring it found **three** — `Inspector`, `Citations` and `EgressPreview`. Two of
those predate today. Carried into `current_task.md` rather than quietly corrected, because "the
audit covers everything except X" is the kind of sentence that stays written long after it stops
being true.

---

## Two things the live run taught that the tests could not

**`user-event` is not a dependency.** The first draft of both test files imported
`@testing-library/user-event`, which is not installed — and adding a dependency needs a
justification in TECH_STACK.md (AGENTS.md). The repo already clicks with `fireEvent`; switching
cost nothing and avoided a dependency argument for a convenience.

**A collapsed `<details>` still shows its contents in the accessibility tree.** The first
attempt to click a candidate did nothing and reported success: `read_page` listed all ten
buttons, but they were inside a closed `<details>`, so the click landed on hidden content and no
`POST` was made. The network log is what caught it — three GETs and no POST. Worth remembering:
**the accessibility tree is not a visibility oracle**, and a click that silently does nothing
looks exactly like a click that worked.

---

## The gate caught what my own check could not, again

```
src/components/Briefing.test.tsx(79,12): error TS2532: Object is possibly undefined.
… 8 errors across the two new test files
```

`noUncheckedIndexedAccess` is on, so `data.projects[0]` is `T | undefined`. I had run
`npx tsc --noEmit` and seen it clean — **before writing the test files**. Vitest was green
throughout, because vitest does not typecheck.

That is the third time today the same shape of mistake landed: `ruff check src/oracle` instead
of the gate's `src tests`, then a hung run I diagnosed from a stale output file, now a
typecheck that predated the code it was supposed to cover. The generalisable version is not
"run tsc again" — it is that **a check is only evidence about the tree that existed when it
ran**, and the cheapest way to keep that straight is to run the gate's own command rather than
a subset chosen for speed.

The fix is also better testing: the element is bound and asserted before it is read, so an empty
array fails with "expected a project" instead of an undefined-property error three lines later.

---

## Not built in T4

The inspector still opens *turns*, not tasks — `onInspect` currently routes a task id into
`setSelectedTurn`, which is the P11-T5 shape and is honest about being a stopgap. And every
number this renders is still computed over fixtures in CI, because `tasks` is 0 rows.

**T5 ends that**, and it is a person's to run.
