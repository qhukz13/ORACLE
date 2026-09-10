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

### 1. ~~P11's agent queue~~ — **done 2026-09-10**

`AgentQueue.tsx` + `queue.ts`, in the sidebar, replacing the ad-hoc `WAITING ON ME` list it
duplicated. Verified against the live daemon: six DONE rows off the real `continue ORACLE` run,
newest-first, `failed` and `skipped` kept as different words.
[UI.md §8 — as built](UI.md#8-agent-queue) records three deviations from the sketch (no `skip` verb
exists; `[review]` navigates rather than decides; two lines, not one) and the two non-obvious
mappings the tests pin: `TaskStatus.WAITING` is `NEXT`, and a delegated task must not appear twice.

**With this, P11's view list is complete.**

### 2. ADR-0027's verification — **stopped and rescheduled 2026-09-10 16:20**

The run was killed at 28% and its scheduled task disabled. **The 2h it had spent was already
worthless** and stopping is not what made it so: `corpus_fingerprint()` hashes every embedded
chunk's text, ORACLE indexes `C:/Projects`, and ORACLE *is* in `C:/Projects` — so the OQ-14 commit
an hour into the run moved the corpus under it. Measured, not inferred:

```
checkpoint     5376 / 19191 vectors   complete=False
stored fp    f740705a003bf55278ce21778a63b187af72f8014eece5f0e21dfdc64ffa50e2
today  fp    10251e03ce2ecebd3a3bf663f6efc234d19aa5ac2c9d5b6360c7c31bf18b8ff4
REUSABLE     False
```

**Fixed, so the next run is not hostage to the next commit.** `--corpus-cache` freezes the
walked-and-chunked corpus to an 8 MB `.json.gz` and reads it thereafter; `run_oq18_eval.cmd` passes
it. The eval was checkpointing the *vectors* and leaving the *corpus* free to move, which solved one
half of a two-half problem. This also makes runs weeks apart comparable, which they never were.

**Two fixture answer documents vanished** — `Asterim/apps/server/src/middleware/entitlementGuard.ts`
and `rbacGuard.ts` are staged deletions in the Asterim repo, done outside ORACLE. So
`lex-entitlement-guard` and `ru-workspace-permissions` are unreachable. The eval printed that and
then scored them as misses anyway, which its own comment says not to do ("measures the walker, not
the model"); it now drops them from the scored set and prints the denominator.

**The like-for-like baseline, computed before the run rather than argued after it.** The previous
complete pass listed misses per arm, so rescoring over the 36 still-reachable fixtures is exact:

| arm | was (n=38) | like-for-like (n=36) |
|---|---|---|
| `dense` | 60.5% | **61.1%** |
| `rrf_w2` | 68.4% | **69.4%** |
| `gated` | 60.5% | **61.1%** |

**The pre-committed decision stands: compare the new `gated` against 61.1%. If it does not move,
revert [ADR-0027](DECISIONS.md#adr-0027--rrf-is-weighted-against-the-lexical-list)** — the rollback
is one line, pass `1.0` as `lexical_weight`. The gate is recall@5 ≥ 80% and neither number reaches
it; ADR-0027 never claimed it would.

> Restoring the two Asterim files would restore the 38-case set. That is the owner's call in
> another repository, not something to do from here.

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
