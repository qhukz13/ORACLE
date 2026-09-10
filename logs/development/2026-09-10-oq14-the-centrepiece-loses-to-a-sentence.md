# OQ-14 — the centrepiece loses to a sentence

`2026-09-10` · resolved `CUT` · [ADR-0029](../../docs/DECISIONS.md#adr-0029--the-orbital-view-is-cut)

The orbital view was the picture on the cover. It was specified in [UI.md §3](../../docs/UI.md)
before anything else in the UI, it is what "a mission console" meant, and ADR-0013 chose its layout
maths in Phase 0. It was also, uniquely in this repo, pre-committed to a test rather than to a
shipping date:

> Cover every label and you must still be able to say what ORACLE is doing. If it fails, delete it
> and record an ADR saying so.

It was built this morning and deleted this afternoon.

## What was built

Deliberately little, because the test is easier to run on a small thing and cheaper to delete:

- `graph/orbit.ts` — `angleOf()` (FNV-1a over the node id), `place()`, `coreStateOf()`. Pure
  arithmetic, no React, so the layout could be asserted separately from the picture.
- `components/Orbit.tsx` — SVG, four rings, a core, one pulse. **No rotation**, which §3 asks for;
  it reads as effort and adds nothing to the test.
- `Orbit.test.tsx` — 12 tests, split into "what the arithmetic guarantees" and "what survives
  `labelsHidden`", so the verdict would be reading measurements rather than taste.
- A `labelsHidden` prop and a **Cover every label (OQ-14)** button in the stage. The instrument, not
  a feature.

## The test, run against live data

One tracked project, two failed tasks from the real `continue ORACLE` graph, two collections. Five
nodes — that is the honest population, not a reduced one.

With labels on, the view read: a grey core labelled `IDLE`, two red dots labelled `a failed` /
`b failed`, a green `ORACLE 0 tasks`, and `notes 167 docs` / `projects 1412 docs`.

With **Cover every label** engaged, `container.querySelectorAll("text")` → 0, confirmed live. What
remained: a grey blob, two small red dots, a green dot, two cyan dots, four rings.

So: can you still say what ORACLE is doing? **Yes — three facts.** Core colour says idle. No pulse
says nothing is waiting. Two red dots say two things failed.

Then I read the rest of the screen the orbit was sitting on, verbatim, at that same moment:

```
command bar:  ORACLE  ⌘ search / ask…  IDLE  seq 1229  Chat Tasks … HALT
sidebar:      PROJECTS  ● ORACLE  ACTIVE  ✗2   17 not tracked   WAITING ON ME  nothing
```

All three facts, in words, permanently, in the frame drawn *around* the orbit — and the words carry
the project name and the count, which the dots had to give up to be dots. The fourth question, *what
is it working on*, the orbit never answered: a project with zero open tasks and a 1412-document
collection are things that **exist**, not things being worked on.

That is the whole verdict. Everything below is me trying to argue the other side and failing.

## "It would be better with more data"

Measured rather than assumed, at 14 nodes (an 11-task graph, a project, two collections):

```
nodes             14
label overlaps    14 of 91 pairs
labels off-canvas 1 of 14 (svg is 340px wide)
node radii        3.2px, 3.7px, 5.8px, 11px
closest 2 tasks   9.1px apart (each is 6px across)
task pairs <8px   0 of 55
```

Two things fall out, and one of them is against my expectation:

**The hash angle works.** No node collides with another at realistic density — closest pair 9.1px for
6px nodes. ADR-0013's central claim is upheld by the measurement, which is why the ADR that cuts the
view leaves ADR-0013 accepted and scoped to the knowledge map.

**The size channel is dead.** Magnitude is shared across rings, so the 1412-document collection took
the entire scale and every task and the project collapsed onto the 3.2px floor. Per-ring scales would
fix it and would also destroy the only reason size existed, which was to compare across rings.

I had predicted the label layer would *collapse* at this density. It does not — 15% of pairs overlap.
That is degradation, not collapse, and the number is in the ADR instead of the adjective.

## "Keep it as an ambient/idle screen"

The tempting option, and the one §2 originally described ("the orbit is the ambient/idle view, chat
is the working view"). Rejected for two reasons. An ambient view that must be *decoded* is worse than
a sentence you can read from the same distance. And keeping it means the state vocabulary is rendered
in two places — the exact shape of the `dependsOn` defect, where a field was asserted by a test in
one place while the wire never sent it in another.

## The accessibility finding, which is the one that actually convinced me

The test file asserts that with labels hidden the SVG's `aria-label` still says `NEEDS YOU` and
`N waiting on you`. That assertion was written as a caveat — "cover the labels" tests the *visual*
channels, and a screen-reader user has neither colour nor motion, so the accessible name has to carry
what the labels carried.

Writing it made the argument obvious. The accessible version of the orbit **is a sentence**. If the
sentence is sufficient for one user it is sufficient for all of them, and the picture is what is
optional. I did not expect the a11y requirement to be the thing that decided a visual-design question.

## What survives

- The **state vocabulary** — `NEEDS YOU` outranking everything, `HALTED` distinct from `ERROR`, only
  one state permitted to be loud. This was the genuinely load-bearing work and it lives in the
  command bar, which is where it was already being read from.
- **ADR-0013's stable angle**, in the knowledge map, where nodes are 1420 documents and position is
  the only channel that could possibly carry them.
- **The honesty gate itself**, which now has a body count of one. §11b's knowledge graph carries the
  same clause, and the fact that the orbit went first makes that clause credible rather than
  decorative.

## Cost

About four hours to build and one to delete, against a decision that had been open since Phase 0 and
was quoted in eight documents. Cheap. The alternative was shipping the cover picture because it was
the cover picture, and then maintaining a second renderer of the state vocabulary forever.

The design principle table in UI.md §1 says *"If an element doesn't answer a question I actually have,
it's deleted."* It cost nothing to write. It cost the centrepiece to mean it.
