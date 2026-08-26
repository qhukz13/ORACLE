# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** **Vision realignment** — audit the whole project against a restated product vision, and
redesign architecture, documentation and roadmap to match it.
**Status:** Done. Design only; no implementation code changed. `make check` **green** (7/7).
**Date:** 2026-08-26
**Dev log:** [`2026-08-26-vision-realignment.md`](../logs/development/2026-08-26-vision-realignment.md)

---

## The finding

The owner restated ORACLE's intended product in full — a resident local AI workstation that is
already running at boot, shows what happened overnight, and answers *"continue Asterim"* by planning
and dispatching work across Claude, Antigravity and local models without the owner choosing any of
them — and asked for an audit, a gap analysis, and a redesign.

**The redesign was mostly unnecessary.** Read against `src/oracle/` (20 packages, ~24,700 lines),
the desktop client, `config/`, the live databases and all 24 documents, the brief describes what is
already the recorded architecture: supervisor not chatbot (ADR-0001/0019), task graph and replanning
(P7–P8, built), planner separate from executor with a *measured* fallback ladder, a capability
registry a human edits, provider-neutral adapters, 33 deterministic tools, a policy gate with taint
and a hash-chained audit, an event-sourced runtime, RAG, memory, and clients as peers of one local
API. UI.md already specifies the orbital core, task states, timeline, agent queue, inspector,
execution tree, knowledge graph and a full design system. `ASTERIM_REUSE.md` and ADR-0022 already
did the Asterim audit and the OSS/SDK survey the brief asked for.

**Four gaps were real.**

### 1. There is no Project — and everything rests on it

A project is a **directory name**. `core/projects.py` lists directories and classifies them by
marker file so a hallucinated name cannot become a filesystem path — good, and it stays. But
`memory_facts`, `memory_attempts` and `TaskSpec` are all *keyed by* a project string with **no
entity behind the key**. Nothing records what Asterim is, what was last done to it, what remains, or
what it cost. UI.md §4 already draws `Asterim  2 tasks  branch main +3`; every number in that line
comes from a subsystem that does not exist.

New: [PROJECT_STATE.md](PROJECT_STATE.md) ·
[ADR-0024](DECISIONS.md#adr-0024--a-project-is-a-first-class-persistent-entity) · **Phase 12**.

Its load-bearing idea is a split: **observed state** (branch, dirty count — git owns it, *never*
store it, because a cached branch name is wrong the moment I switch branches with no event to
correct it) versus **relational state** (what ORACLE attempted and left unfinished — only ORACLE
knows it, so it must be stored). *If git knows it, do not store it.*

Also found: **there is no `continue` intent.** The vision's headline utterance routes to `chat` or
`modify` with low confidence. Adding a label is cheap but touches a **measured** surface (93.3% over
30 fixtures) and so requires re-running the eval, not assuming it holds.

### 2. Nothing makes ORACLE resident

Autostart appears in the repo exactly twice, both as a *reason for choosing Tauri* — a capability
cited, never a subsystem designed. No boot sequence, no restore, no health phase, no briefing.

[ADR-0025](DECISIONS.md#adr-0025--oracle-is-a-resident-service-the-window-is-a-client) makes the
**daemon** resident rather than the shell. Tauri's sidecar arrangement would make the *window* the
resident thing — which means closing the window stops the work, and then "it keeps working while I
do something else" is false and the briefing has nothing to brief. **Phase 13**, plus one new UI
surface ([UI.md §7b](UI.md#7b-the-briefing--phase-13)).

### 3. The vision contradicts a measurement, and the measurement wins

The brief routes **planning to Antigravity**. [OQ-20](OPEN_QUESTIONS.md#oq-20) measured it:
**12/16 = 75%** valid `ExecutionPlan`s against a 90% gate, ~55k tokens per plan, every hard failure
at `--effort high` being the agent browsing the filesystem. Claude is the planner; Antigravity is
`reviewer`/`researcher`, read-only. The architecture was **not** changed to match the diagram — the
disagreement is recorded with its numbers in [VISION.md §8a](VISION.md#8a-antigravity-is-not-the-planner),
and noted as a verdict on one CLI on one date, pinned to a fixture.

### 4. New hardware invalidates the binding constraint

The brief names ~14B and ~27B local tiers "once the new GPU arrives". The README says *"4 GB of VRAM
is the binding constraint of this entire project"*, and ADR-0004 is a consequence of it — `0.8b`
beat `2b` by measurement, embeddings went to CPU, context length became a hardware decision, tool
pre-filtering became load-bearing at ~730 ms per turn.

[ADR-0026](DECISIONS.md#adr-0026--the-local-tier-ladder-is-capability-shaped-and-gpu-conditional)
designs the **capability-tier abstraction** now and schedules the **model choices** as a measured
spike (Phase 16, unscheduled, `ASSUMPTION` — no GPU, VRAM figure or date has been stated). ADR-0004
is marked conditional, not superseded. Its own history is the argument: the original choice there
was `2b` "based on arithmetic", and that was wrong.

---

## Why the sequencing changed

`tasks` is **0 rows**. The vision's payload is *visualisation of activity* — orbit, execution tree,
timeline, briefing — and all four render supervisor activity that has never happened. That already
bit once: `TaskTree` is green on a fixture the running app cannot produce.

The brief's own "smallest milestone that proves the architecture" is: read project state → gather
context → ask planner → dispatch one worker → track → collect → update state → display. **That is
Phase 12**, and it is also the run that fills `tasks` with real evidence. The product goal and the
engineering unblock are the same action — which is the whole justification for putting the residency
arc ahead of mobile and voice.

---

## What changed

| File | Change |
|---|---|
| `docs/VISION.md` | **new** — the product as a day, not an architecture; the four disagreements above |
| `docs/PROJECT_STATE.md` | **new** — the missing subsystem, with acceptance criteria |
| `docs/DECISIONS.md` | ADR-0024/0025/0026; ADR-0004 marked conditional |
| `docs/ROADMAP.md` | residency arc as P12–P13; tiers promoted to P16; Mobile→P14, Voice→P15, Hardening→P17 |
| `docs/UI.md` | §7b briefing; sidebar sourcing note; header records the missing references |
| `docs/README.md` | index + reading order |
| 8 docs | every renumbered phase link fixed |

**Deliberately unchanged:** Phases 0–11 · the design language · the orbital spec and its go/no-go ·
the five rules, the process model, the policy gate · all implementation code.

---

## Two things the next person should know

**The gate is green.** `current_state.md` said gate status was "not established"; it now is —
7/7 steps, 344 s, at HEAD on `phase6-integration`. That line in `current_state.md` is the one thing
in it that is now stale.

**The visual references were not attached.** The brief calls them "extremely important" and asks
that the design language be extracted from them. No images reached the session and none are in the
repository, so UI.md §1/§14/§15 stand as previously written and are marked `TO VERIFY` at the file
header. Cheap to close; blocks nothing.

---

## Next

**A person, ~5 minutes: run `oracle-selfcheck` once.** Local, no egress, one approval card. It
produces the first real task graph and unblocks P11-T2's go/no-go. It has been staged and unfired
since this morning because the approval expires in 180 s and firing it unattended would write a
*refused* run into the table the run exists to populate.

**An agent: [P12-T1](current_task.md)** — the `projects` table and registry. P11-T5 is carried, not
dropped; see current_task.md for why it yielded.
