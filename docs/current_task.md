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

### 2. ~~ADR-0027's verification~~ — **done 2026-09-11, and it stands**

The run completed at 04:00 against the frozen corpus. **`gated_w2` 69.4% vs `gated` 66.7%: the
weighting is worth +2.8 points on the path that ships**, so the pre-committed revert does not fire.
[ADR-0027 verified](DECISIONS.md#adr-0027--rrf-is-weighted-against-the-lexical-list) ·
[dev log](../logs/development/2026-09-11-adr0027-verified.md) ·
`uv run python scripts/oq18_verdict.py` re-reads the verdict from the artifact.

**The comparison was taken within one run, and that mattered more than it sounded.** Of the three
arms with a rescored cross-run baseline, two were unchanged and `gated` had drifted **+5.6 points**
between runs — twice ADR-0027's whole effect. A cross-run reading would not have been wrong so much
as meaningless.

**One follow-up, cheap and concrete:** the eval writes a single `misses` list rather than one per
arm, so whether `gated_w2` and `rrf_w2` miss the *same* queries — they score identically — cannot
be answered from the artifact. Per-arm miss lists would make the next run of this question cheaper.

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
