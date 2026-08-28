# Vision realignment — auditing ORACLE against a restated product vision

**2026-08-26** · design pass, no implementation code changed · `make check` green before and after
(7/7 steps, 1,071 Python + ~202 TS tests, 344 s)

The owner restated ORACLE's intended product in full: a persistent local AI workstation that is
already running when the PC boots, that shows what happened while they were away, and that responds
to *"continue Asterim"* by planning and dispatching work across Claude, Antigravity and local models
without the owner choosing any of them. The brief asked for a complete audit, a gap analysis, and a
redesign of architecture, documentation and roadmap.

**The headline finding is that the redesign was mostly unnecessary.**

---

## 1. What the audit found

I read every document in `docs/` (24 files, ~500 KB), the whole of `src/oracle/` (20 packages,
~24,700 lines), the desktop client (13 components, ~6,300 lines), `config/`, and the live databases.
Then I compared them against the brief clause by clause.

The brief describes a system that is **already the recorded architecture**. Not aspirationally — in
committed, tested, measured form:

| The brief asks for | Status |
|---|---|
| ORACLE as supervisor, not a chatbot | [ADR-0001](../../docs/DECISIONS.md#adr-0001--orchestrator-not-a-monolithic-agent) + [ADR-0019](../../docs/DECISIONS.md#adr-0019--the-supervisor-completes-the-orchestrator), built |
| Execution graph, replanning | [ORCHESTRATION.md](../../docs/ORCHESTRATION.md), built P7–P8, 2,575 lines |
| Planner separate from executor | [PLANNER.md](../../docs/PLANNER.md), built, with a measured fallback ladder |
| Agent capability registry | `config/agents.yaml`, 9 roles × 3 agents, versioned data a human edits |
| Provider-neutral adapters | `integrations/`, adapter protocol + Claude + Antigravity |
| Deterministic tools instead of LLM calls | 33 contracts, [ADR-0011](../../docs/DECISIONS.md#adr-0011--deterministic-pre-router-before-the-model) pre-router |
| Policy / approvals / audit | `policy/`, 5 tiers, taint, hash-chained audit, 265 security tests |
| Event bus, replayable | [ADR-0010](../../docs/DECISIONS.md#adr-0010--event-sourced-runtime), gap-free `seq`, WS resume |
| RAG + memory | `rag/` 3,439 lines, `memory/` 1,265 lines, both built |
| Desktop shell, browser + mobile peers | [ADR-0007](../../docs/DECISIONS.md#adr-0007--clients-are-peers-of-one-local-api), Tauri 2 |
| Central orbital core, task states, timeline, agent queue, inspector, design system | [UI.md](../../docs/UI.md) §3, §4, §6, §6b, §7, §8, §14, §15 — **specified in detail** |
| Asterim reuse audit | [ASTERIM_REUSE.md](../../docs/ASTERIM_REUSE.md), done 2026-08-24 |
| OSS/SDK investigation (ACP, OpenHands, LangGraph, CrewAI, MCP, Agent SDK…) | [ADR-0022](../../docs/DECISIONS.md#adr-0022--external-agent-frameworks-evaluated-not-adopted), 11 candidates, licence review included |

The brief also asked me to look for specific incompatibilities. Most were absent — but the exercise
was worth doing, because four were real.

---

## 2. The four real gaps

### 2a. There is no Project

The largest one, and the one every headline feature turns out to rest on.

A project today is **a directory name**. `core/projects.py` lists directories, classifies each by
marker file, and derives argv for test/build/lint. The list is handed to the intent classifier so a
hallucinated name resolves to nothing rather than to a filesystem path — a good safety mechanism,
and it stays. It is also the entire model.

`memory_facts`, `memory_attempts` and `TaskSpec` are all *keyed* by a project string. **There is no
entity those keys refer to.** Nothing records what Asterim is, what was last done to it, what
remains open, or what it cost. `UI.md §4` already draws `Asterim  2 tasks  branch main +3` — every
number in that line comes from a subsystem that does not exist.

Written up as [PROJECT_STATE.md](../../docs/PROJECT_STATE.md), decided in
[ADR-0024](../../docs/DECISIONS.md#adr-0024--a-project-is-a-first-class-persistent-entity),
scheduled as Phase 12.

**The design's load-bearing idea** is a split I did not expect to need before writing it out: there
are *two* kinds of project state, and conflating them is the failure mode.

- **Observed state** — branch, dirty count, last commit. Source of truth is git. **Never store it.**
  A cached branch name is wrong the moment I switch branches in my editor, silently, with no event
  to correct it; `git status` warm costs single-digit milliseconds, so the cache buys nothing and
  forfeits correctness.
- **Relational state** — what ORACLE attempted, what it left unfinished, what it cost, when I last
  looked. Source of truth is ORACLE and nothing else. **Must be stored.**

Stated as a rule: *if git knows it, do not store it; if only ORACLE knows it, store it.*

A second finding fell out of it: there is no `continue` intent. `IntentLabel` is
`run · investigate · question · status · search · modify · delegate · pipeline · chat · control`, so
the vision's headline utterance routes to `chat` or `modify` with low confidence. Adding a label is
cheap, but it is a change to a **measured** surface (93.3% over a 30-case fixture set) and therefore
requires re-running the eval, not assuming it holds.

### 2b. Nothing makes ORACLE resident

The vision opens with *"I turn on the PC. ORACLE is already running."* Today `oracled` is started by
hand and so is the UI.

Autostart appears in the repository exactly twice, both times as a *reason for choosing Tauri*
([TECH_STACK.md §5](../../docs/TECH_STACK.md#5-desktop-shell),
[ADR-0008](../../docs/DECISIONS.md#adr-0008--tauri-2-for-the-desktop-shell)) — a capability cited,
never a subsystem designed. There is no boot sequence, no state restore, no health phase, and no
"what happened while I was away".

The interesting part is *which thing* becomes resident.
[ADR-0025](../../docs/DECISIONS.md#adr-0025--oracle-is-a-resident-service-the-window-is-a-client)
chose the daemon over the shell, and the argument is short: Tauri's sidecar mechanism would make the
*window* the resident thing, which means closing the window stops the work. If ORACLE only runs
while I am looking at it, then "it keeps working while I do something else" is false and the
briefing has nothing to brief. Inverting it — the shell attaches to a running daemon — is also a
strictly larger set of working configurations, and it is what the browser client already needs.

Scheduled as Phase 13, with one new UI surface ([UI.md §7b](../../docs/UI.md#7b-the-briefing--built-p12-t3t4-2026-08-26)).

### 2c. The vision contradicts a measurement, and the measurement wins

The brief's architecture diagram routes **planning to Antigravity** and coding to Claude.

[OQ-20](../../docs/OPEN_QUESTIONS.md#oq-20) measured this on 2026-08-24 over 16 supervised calls
against the real adapter: Antigravity returned a valid `ExecutionPlan` **12/16 = 75%** against a 90%
gate, median 27–43 s, ~55k tokens per plan, and every hard failure at `--effort high` was the agent
browsing the filesystem instead of answering. The ladder promoted **Claude** to default planner;
Antigravity holds `reviewer` and `researcher` only, `read_only: true`. It cannot hold `coder`
either — headless `agy` cannot write without a flag ORACLE refuses to pass.

I did not change the architecture to match the diagram. I recorded the disagreement in
[VISION.md §8a](../../docs/VISION.md#8a-antigravity-is-not-the-planner) with the numbers attached,
and noted that it is a verdict on `agy --json-schema` as of one date, pinned to a fixture — the seam
is vendor-neutral, and re-measuring re-orders the ladder.

### 2d. New hardware invalidates the project's binding constraint

The brief names a ~14B and a ~27B local tier "once the new GPU arrives". That is **new information
about the machine**, and it reaches further than adding a tier.

The README's own words: *"4 GB of VRAM is the binding constraint of this entire project."*
[ADR-0004](../../docs/DECISIONS.md#adr-0004--two-tier-local-model-router--reasoner) is a consequence
of it — `qwen3.5:0.8b` beat `2b` by measurement because `2b` splits 36/64 CPU/GPU at *every* context
length; embeddings were pushed to CPU (ADR-0014) to keep the router resident; context length became
a hardware decision; tool pre-filtering became load-bearing because ~1,200 tokens of schemas costs
~730 ms per turn.

A bigger card re-opens all of that. So
[ADR-0026](../../docs/DECISIONS.md#adr-0026--the-local-tier-ladder-is-capability-shaped-and-gpu-conditional)
designs the **routing abstraction** now (capability tiers, same shape as the agent registry that
already selects by role rather than by vendor) and schedules the **model choices** as a measured
spike conditional on hardware — Phase 16, unscheduled, carrying an `ASSUMPTION` marker because no
GPU model, VRAM figure or date has been stated.

ADR-0004's own history is the argument against doing it any other way: its original choice was `2b`
"based on arithmetic", and **that was wrong**. Only measurement caught it.

One rule survives any GPU: *bigger is not better per task.* Model swap time dominates inference time
on this class of machine, so a resident small model beats a swapped large one for routing. Tier
selection is a function of (task shape, residency, privacy), never of "which model is smartest".

---

## 3. The thing that made the sequencing obvious

`tasks` is **0 rows**. `memory_facts` is **0 rows**. `memory_attempts` is **0 rows**. The event log
holds ~400 events over five days.

Everything the supervisor arc ships — graphs, planning, replanning, verification, memory — has been
exercised by tests and fixtures **only**. This was already known and already recorded
([current_state.md §11](../../docs/current_state.md)); what the vision adds is the reason it now
blocks the *product* and not merely a go/no-go.

The vision's payload is visualisation of activity: the orbit, the execution tree, the timeline, the
briefing. All four render supervisor activity that has never happened. Building them first means
judging them against a picture we drew ourselves, which is precisely why
[OQ-14](../../docs/OPEN_QUESTIONS.md#oq-14) is still open — and it has already bitten once, at
`TaskTree`, which is green on a fixture the running app cannot produce because `store.ts` never
populates `dependsOn`.

**The brief's own §41 asks for the smallest milestone that proves the architecture, and describes
it as: read project state → gather context → ask planner → create a task plan → dispatch one worker
→ track → collect → update state → display.** That is Phase 12. It is also exactly the run that
fills `tasks` with real evidence, timings and cost.

The product goal and the engineering unblock are the same action. That is why the residency arc goes
before mobile and voice rather than after, and it is the only sequencing change the audit justified.

---

## 4. What changed, and what deliberately did not

**Changed:**

| | |
|---|---|
| `docs/VISION.md` | **new** — the product as a day rather than an architecture; answers the brief's fifteen closing questions; records the four disagreements above |
| `docs/PROJECT_STATE.md` | **new** — the missing subsystem |
| `docs/DECISIONS.md` | ADR-0024, ADR-0025, ADR-0026; ADR-0004's status marked conditional on 4 GB VRAM |
| `docs/ROADMAP.md` | residency arc inserted as P12–P13; tier stack promoted from the idea backlog to P16; Mobile P12→P14, Voice P13→P15, Hardening P14→P17; every renumbered link fixed across 8 files |
| `docs/UI.md` | §7b, the briefing; sidebar annotated with where its numbers will come from; header records that the visual references were not attached |
| `docs/README.md` | index updated; reading order now starts at VISION and current_state |

**Not changed, on purpose:**

- **Phases 0–11.** The audit found nothing in them the vision contradicts.
- **The design language.** UI.md §1/§14/§15 already say "mission console, dark, dense, calm until
  something needs attention", with contrast ratios verified as arithmetic over tokens parsed out of
  `styles.css`. I had nothing better to offer, and — see below — no references to check it against.
- **The orbital view spec.** Still gated on OQ-14, still go/no-go, still blocked on data. The vision
  wants it badly, which is a reason to judge it honestly rather than a reason to skip the judgement.
- **The five architectural rules**, the process model, and the policy gate. The brief's §34 asked
  that working infrastructure be preserved; this is that infrastructure.
- **No implementation code.** The brief said audit and redesign first, and `docs/current_task.md`
  scoped the session to design. `make check` is green at HEAD and stayed green.

---

## 5. The dead end worth recording

I spent the first third of this session preparing to write the documents the brief listed as
missing — `ORCHESTRATION.md`, `PLANNER.md`, `ASTERIM_REUSE.md`, `AGENTS.md`, a design system, an
OSS/SDK survey. **All of them already existed**, several in more detail than I would have written.

The brief was written from the vision rather than from the repository, which is the right way to
state a vision and the wrong way to plan against one. The cost of not checking would have been
several thousand lines of confidently-written duplicate documentation, each copy immediately
starting to drift from the other — the exact failure this repository's conventions exist to prevent.

The generalisable version: **when a brief and a repository disagree about what exists, the
repository is the evidence.** Read it first, and let the brief tell you what to look for rather than
what to write.

---

## 6. One thing I could not do

The brief says the attached visual references are "extremely important" and instructs that the
design language be extracted from them.

**No images reached this session, and none are in the repository** (`find` returns only Tauri
launcher icons). So §8 of the brief — the largest single block of UI direction — could not be acted
on. UI.md's design language stands as previously written and is now marked `TO VERIFY` against the
references at its header.

This is cheap to fix and blocks nothing: re-attach them and the audit is a single pass over UI.md
§1, §14 and §15.

---

## 7. Next

Two actions, in this order, and the first is a person's.

**1. Run `oracle-selfcheck` once.** Local, no egress, six steps, one approval card, ~5 minutes.
It produces a real six-task graph with real evidence, timings and cost — which unblocks P11-T2's
go/no-go and gives P11-T3/T4 something to be judged against. It has been staged and unfired since
this morning because the approval card expires in 180 s and firing it unattended would write a
*refused* run into the very table the run exists to populate.

**2. Finish P11-T5**, then start Phase 12. T5 is unblocked, small, and already specified; Phase 12
is where the product starts existing.
