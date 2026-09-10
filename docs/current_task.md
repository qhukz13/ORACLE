# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P11's view list is complete and P13's boot health phase is in. Next: P13's remaining new work —
`oracled` as an autostart service, and the shell attaching to a running daemon instead of
supervising a sidecar.**

**Phase:** [13 — residency, boot & the briefing](ROADMAP.md#phase-13--residency-boot--the-briefing--residency-arc) · **Scope:** Residency arc
**Status:** `READY` · **Set:** 2026-09-10 · **Blocked on:** nothing

### Done 2026-09-10

| | |
|---|---|
| [OQ-27](OPEN_QUESTIONS.md#oq-27) | resolved — dispatched approvals do not expire ([ADR-0028](DECISIONS.md#adr-0028--a-dispatched-approval-does-not-expire)) |
| [OQ-14](OPEN_QUESTIONS.md#oq-14) | **`CUT`** — the orbit failed its own test ([ADR-0029](DECISIONS.md#adr-0029--the-orbital-view-is-cut), [dev log](../logs/development/2026-09-10-oq14-the-centrepiece-loses-to-a-sentence.md)) |
| §11b knowledge graph · §12 notifications | shipped |
| §8 agent queue | shipped — **P11's view list is now complete** |
| `TaskTree` fixture | re-recorded off the wire |
| **P13 boot health phase** | `core/health.py`; P13's acceptance criterion run, not asserted |
| **The degraded banner** | one row per missing subsystem, each carrying its own `lost` |

**Two defects the banner work uncovered, both live before today:** every degradation ended with
*"Slash commands and the command palette still work"* — the *reasoning* fallback, and false when the
index was what was down — and only one degradation could be shown at a time. Both are gone;
`src/degradation.ts` merges the boot snapshot with any live `system.degraded`, and the live event
wins.

---

## What remains

### 1. P13 — the rest of residency

- **`oracled` as an autostart service or scheduled task**, starting *degraded-capable*. The boot
  health phase is what makes "degraded-capable" mean something: it measured 139 ms healthy and
  376 ms with the router down, and never gates the boot.
  > **This changes the machine's boot behaviour, so the installer gets written but not run.**
  > Installing it is the owner's call, not an agent's.
- **The shell attaches to a running daemon** instead of supervising a sidecar.
- Acceptance still open: reboot → online with nobody starting it · closing the window does not stop
  work · boot animation ≤ ~400 ms.

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

P13 closes on its acceptance list: reboot the machine and ORACLE is online without anyone starting
it · the window shows what ran, finished, failed and is waiting within 3–5 s · the briefing does not
clear itself on render · boot animation ≤ ~400 ms · closing the window does not stop work.

Three of those are already true and tested (the briefing's persistence and the unclean-restart line
came with P12-T3; the health phase is what lets a degraded boot still be a boot). The two that are
not are both about **the daemon outliving the window**, which is the one structural change left in
this phase.
