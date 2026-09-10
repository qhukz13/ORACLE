# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**[OQ-14](OPEN_QUESTIONS.md#oq-14) is resolved `CUT`. The orbital view was built against real data,
failed its own test, and was deleted
([ADR-0029](DECISIONS.md#adr-0029--the-orbital-view-is-cut)). Next: the P11 agent queue, and
[ADR-0027](DECISIONS.md#adr-0027--rrf-is-weighted-against-the-lexical-list)'s verification when the
OQ-18 corpus run lands.**

**Phase:** [11 — execution visualisation & advanced UI](ROADMAP.md#phase-11--execution-visualisation--advanced-ui--capability-arc) · **Scope:** Capability arc
**Status:** `READY` · **Set:** 2026-09-10 · **Blocked on:** nothing

### Done 2026-09-10

| | |
|---|---|
| [OQ-27](OPEN_QUESTIONS.md#oq-27) | resolved — dispatched approvals do not expire ([ADR-0028](DECISIONS.md#adr-0028--a-dispatched-approval-does-not-expire)) |
| §11b knowledge graph | complete: canvas map, traces, hulls, select-as-context |
| §12 notifications | shipped |
| `TaskTree` fixture | re-recorded off the wire; the assertion with teeth (`kind`) was measured, not assumed |
| **[OQ-14](OPEN_QUESTIONS.md#oq-14)** | **`CUT`** — [dev log](../logs/development/2026-09-10-oq14-the-centrepiece-loses-to-a-sentence.md) |

**Why OQ-14 came back "no", in one line:** with every label covered the orbit still said three true
things — a state colour, no pulse, two red dots — and all three were written in words, at that same
moment, in the chrome around it (`IDLE` · `WAITING ON ME nothing` · `ORACLE ACTIVE ✗2`). It never
answered the fourth question at all. `Orbit.tsx`, `graph/orbit.ts`, their tests, the styles and the
stage are deleted; the state vocabulary and ADR-0013's stable angle survive, in the command bar and
the knowledge map respectively.

---

## What remains

### 1. P11's agent queue — the last unbuilt item in this phase

The only §-spec'd view still missing now that the orbit is not coming. It has real data to render
for the first time (`tasks` is non-zero, delegations run, attempts and outcomes exist).

### 2. ADR-0027's verification — **waiting on a running job, do not restart it**

The OQ-18 corpus run re-started 2026-09-10 14:29 and re-embeds from scratch (~0.9 chunks/s,
19,212 chunks, ETA ~20:30). It writes `logs/measurements/oq18-translated.json`; the completion marker
*is* that file, and `scripts/run_oq18_eval.cmd` exits early if it exists.

**What to compare, and the pre-committed rollback:** the previous complete pass measured
`dense 61% · rrf 61% · rrf_w2 68% · gated 61%`, so the weighting is worth **+7pp on `rrf`**. The
open question is whether it carries through the *fusion gate* — compare the new `gated` against that
`61%`. **If the composed number does not move, revert ADR-0027** (the rollback is one line: pass
`1.0` as `lexical_weight`). The gate is recall@5 ≥ 80%; neither number reaches it, and ADR-0027 was
never claimed to.

> Two operational facts, both learned the hard way and both still true: heavy CPU work during the run
> corrupts its `chunks/s` numbers *and* slows it, and **any repo edit invalidates the checkpoint** for
> the *next* attempt, because `corpus_fingerprint()` hashes every embedded chunk's text and ORACLE
> indexes itself. The run in flight is unaffected — it read the corpus at 14:29.

### 3. [OQ-26](OPEN_QUESTIONS.md#oq-26) — still open

The eval self-contamination ratchet, and whether config files should be embeddable at all.

### 4. Still `TO VERIFY`, still wanting the owner's eye

UI.md §1/§14/§15 — the visual references were never attached. This mattered less than it looked:
OQ-14 turned out to be answerable by measurement without them.

---

## Definition of done for the phase

P11 closes when the agent queue renders. The orbit's slot in that list is closed by deletion, which
[ROADMAP](ROADMAP.md#phase-11--execution-visualisation--advanced-ui--capability-arc) item 2 now
records as done-and-undone rather than pending.
