# ORACLE — Vision

> **What the product is, stated as an experience rather than as an architecture.**
> Written 2026-08-26 from the owner's restatement of intent, audited against source the same day
> ([dev log](../logs/development/2026-08-26-vision-realignment.md)).
>
> Every other document in `docs/` describes *the system*. This one describes *the day*. When a
> design decision has no obvious answer, the tie-breaker is §2: does it make that morning shorter?

---

## 1. What ORACLE is

**A personal AI workstation.** One resident local environment that holds the state of everything I
am building, decides what to do next, and drives whichever agent is right for the job — so that I
talk to one intelligence instead of operating five tools.

It is *not* a chatbot, a dashboard, an agent manager, or a launcher. Those are all interfaces to
work. ORACLE is meant to be the thing that **holds the work**, and the interface is how I see into it.

The architectural expression of this is already recorded and unchanged:
[ADR-0001](DECISIONS.md#adr-0001--orchestrator-not-a-monolithic-agent) (orchestrator, not a
monolithic agent) and [ADR-0019](DECISIONS.md#adr-0019--the-supervisor-completes-the-orchestrator)
(the supervisor completes it). This document does not revise them. It states what they are *for*.

---

## 2. The day — the acceptance test

This is the product. Everything in [ROADMAP.md](ROADMAP.md) is ordered by how much of it it makes true.

```
I turn on the PC.                     ORACLE is already running. I did not launch it.
I look at the screen.                 Within 3-5 seconds I know:
                                        - what ran while I was away, and how it ended
                                        - what is running now
                                        - what is waiting on me
                                        - what failed
                                        - what ORACLE intends to do next
I say "continue Asterim."             ORACLE resolves the project, reads its state, assembles
                                      context, plans, dispatches a worker, watches it, verifies
                                      the result with evidence, and reports.
I go and do something else.           It keeps working. When it needs me, it says so - once,
                                      unmissably, and it does not proceed without me.
```

**What I never do in that sequence:** open Claude, open Antigravity, open a terminal, write a
prompt, pick a model, or copy a file path between two windows.

**The two claims in it that ORACLE cannot make today**, and which therefore define the next arc of
work, are *"ORACLE is already running"* (§6) and *"what ran while I was away"* (§6). Everything
else in the sequence exists in some form and is inventoried in
[current_state.md](current_state.md).

---

## 3. Who does what

The division of labour is measured, not assumed. Where a measurement contradicts intuition, the
measurement is recorded here rather than in a footnote — see §8.

| | Does | Never does |
|---|---|---|
| **ORACLE** (deterministic Python) | Holds state · assembles context · decides who works · enforces what they may touch · verifies results · reports with evidence | Write your code. Reason about your architecture. Be the smartest model in its own system. |
| **Local tiny model** (`qwen3.5:0.8b`) | Intent, project resolution, routing, short narration | Plan. Write code. Decide anything with a side effect. |
| **Local mid model** *(tier not yet built — §8b)* | RAG answers, summarisation, documentation, private work that must not leave the machine | Author execution plans until measured against the same fixtures as Claude. |
| **Claude** | Plan authorship · implementation · debugging · testing · review · research | Choose its own executor. Approve its own side effects. See a secret. |
| **Antigravity** | Review, research — read-only, `--effort low` | **Plan.** Measured at 75% valid-on-first-attempt against a 90% gate ([OQ-20](OPEN_QUESTIONS.md#oq-20)). |
| **Deterministic tools** | git, tests, filesystem, search, launch — 33 contracts | Anything a model was going to do anyway, more slowly and less reliably. |

**The governing economy:** *the most common correct action is not to call a model at all.* Slash
commands, palette actions, pipelines and exact-match tool syntax bypass the model entirely
([ADR-0011](DECISIONS.md#adr-0011--deterministic-pre-router-before-the-model)). This is what makes
a sub-1B model viable as the primary interface, and it is why ORACLE stays usable when Ollama is
down.

---

## 4. How ORACLE decides

Four questions, in order, each answered by the cheapest mechanism that can answer it.

| Question | Answered by | Falls back to |
|---|---|---|
| *What did they mean?* | Pre-router exact match → local classifier | Ask. Never guess a project name. |
| *What is the state of the thing they meant?* | Project state (§5) + git + memory + retrieval | Read the repository directly |
| *What is the work?* | Planner ladder: templates → Claude → Antigravity → ask | A single task, not a graph |
| *Who should do it, and may they?* | Capability registry (`config/agents.yaml`) + policy gate | Refuse, and say which rule refused |

Two rules make this trustworthy rather than merely automatic:

1. **A plan cannot give itself an executor.** Agent capabilities are versioned data a human edits
   ([PLANNER.md §5](PLANNER.md#5-agent-selection)); planner output is untrusted input
   ([ADR-0021](DECISIONS.md#adr-0021--planner-output-is-untrusted-input)).
2. **Evidence outranks claims.** An agent saying "tests pass" is a claim. ORACLE running the tests
   is evidence, and only evidence gates a dependent task
   ([ORCHESTRATION.md §2](ORCHESTRATION.md#2-task-model)).

---

## 5. What is persistent

Persistence is what separates "an app I open" from "an environment that is there". Five layers, and
the last two do not exist yet.

| Layer | Holds | Where | State |
|---|---|---|---|
| **Events** | Everything that happened, gap-free, replayable | `events` in `oracle.db` | built ([ADR-0010](DECISIONS.md#adr-0010--event-sourced-runtime)) |
| **Task graphs** | What was attempted, by whom, with what evidence and cost | `tasks` | built; **0 rows — never run for real** |
| **Memory** | Preferences, project facts, prior attempts | `memory_facts`, `memory_attempts` | built; **0 rows** |
| **Project state** | What each project *is*, and where it stands | — | **not built** — [PROJECT_STATE.md](PROJECT_STATE.md) |
| **Residency** | That ORACLE is running at all, across reboots | — | **not built** — §6 |

**Project state is the missing subsystem.** Today a project is a directory name discovered at boot
by `core/projects.py` and validated against the classifier so a hallucinated name cannot become a
filesystem path. That is a safety mechanism, and it works. It is not a model of a project: there is
no record of what Asterim is, what was last done to it, what is open against it, or what it costs.
"Continue Asterim" is unanswerable without one, which is why it is the next architectural piece
rather than a UI feature.

---

## 6. What happens when the PC boots

Today: nothing. `oracled` is started by hand, the UI is started by hand, and the window is where
state appears rather than where state lives.

The intended shape, in the order the pieces must be built:

```
Windows starts
   |
oracled starts as a service            <- ORACLE is the resident thing, not the window
   |
recovery                               <- already built: interrupted graphs are gated, never auto-resumed
   |
health: Ollama - databases - index - agent CLIs - policy
   |                                     (any one may be down; ARCHITECTURE.md #8 degradation applies)
ORACLE ONLINE ------------------------- work may already be happening here
   |
the window opens (or does not - it is a client)
   |
THE BRIEFING                           <- what changed since I last looked
```

Two constraints on the boot experience, both from the product philosophy rather than from taste:

- **It must be fast and quiet.** A cinematic sequence is a tax paid every single morning. The
  correct length of an animation I will see 3,000 times is about 400 ms.
- **It must never auto-resume an interrupted worker.** Already decided and already implemented —
  Asterim's empirically-derived rule, ported ([ASTERIM_REUSE.md](ASTERIM_REUSE.md)): on restart, a
  prior agent still alive gates, an agent gone mid-run gates. "Resume safe background work" means
  *ORACLE resumes*, not *the agent resumes*.

---

## 7. What the interface represents

The interface is a **read-out of state, not a container for it**. Every client — desktop shell,
browser, phone, and eventually voice — is an equal peer of one local API
([ADR-0007](DECISIONS.md#adr-0007--clients-are-peers-of-one-local-api)). This is why mobile and
voice do not require touching the agent core, and it is why the shell is replaceable.

The design language is specified in [UI.md](UI.md) and is not restated here. The one rule worth
repeating, because it is the rule most easily lost while chasing an aesthetic:

> **Every element answers a question I actually have.** If it does not, it is deleted — however
> good it looks. Fake telemetry, decorative nodes, permanent edges and animation that does not
> track a real event are all failures of the same kind: they cost attention and return nothing.

The futurism in the references is a *consequence* of presenting a system honestly and densely. It
is not a skin to apply.

---

## 8. Where this vision and the measurements disagree

Recorded here rather than resolved silently, per the repo's standing rule.

### 8a. Antigravity is not the planner

The vision's diagram routes planning to Antigravity and coding to Claude. **Measurement says the
opposite.** [OQ-20](OPEN_QUESTIONS.md#oq-20), 16 supervised calls against the real adapter:
Antigravity returned a valid `ExecutionPlan` **12/16 = 75%** against a 90% gate, at a median 27–43 s
and ~55k tokens per plan, and every hard failure at `--effort high` was the agent browsing the
filesystem instead of answering. The ladder promoted **Claude** to default planner and Antigravity
holds `reviewer` and `researcher` only, `read_only: true`.

It cannot hold `coder` either: headless `agy` cannot write without a flag ORACLE refuses to pass.

**This is not a permanent verdict on Antigravity** — it is a verdict on `agy --json-schema` as of
2026-08-24, pinned to a fixture. The seam is vendor-neutral; re-measure and the ladder re-orders.
But the vision should not be written as though the measurement had gone the other way.

### 8b. The tier model assumes a GPU that is not in this machine

The vision names a ~14B and a ~27B local tier "once the new GPU arrives". **That is new
information, and it invalidates the most load-bearing constraint in the project.**

Today: **4 GB of VRAM on a GTX 1050 Ti**, and
[ADR-0004](DECISIONS.md#adr-0004--two-tier-local-model-router--reasoner) is entirely a consequence
of it — `qwen3.5:0.8b` was chosen over `2b` by measurement, embeddings were pushed to CPU
([ADR-0014](DECISIONS.md#adr-0014--embeddings-on-cpu-gpu-reserved-for-the-router)), context length
became a hardware decision, and tool pre-filtering became load-bearing because ~1,200 tokens of
schemas costs ~730 ms per turn.

A larger card does not merely add a tier. It reopens: which model routes, whether embeddings return
to GPU, whether context budgets are still split by call type, and whether a local model becomes a
planner-ladder candidate above deterministic templates. Those are **measurements to re-run, not
assumptions to update** — which is why the tier work is scheduled as a phase with a spike rather
than as a configuration change. See
[ADR-0026](DECISIONS.md#adr-0026--the-local-tier-ladder-is-capability-shaped-and-gpu-conditional).

`ASSUMPTION` — no GPU model, VRAM figure or arrival date has been stated. Until one is, the tier
phase stays unscheduled and ADR-0004 stands.

### 8c. The system has never run for real

`tasks` is **0 rows**. `memory_facts` is **0 rows**. The event log holds ~400 events over five days.
Everything the supervisor arc ships — task graphs, planning, replanning, verification, memory — is
exercised **by tests and fixtures only**.

This bears directly on the vision, because the vision's payload is *visualisation of activity*: the
orbit, the execution tree, the timeline and the briefing all render supervisor activity that has
never happened. Building them first would mean judging them against a picture we drew ourselves —
which is the explicit reason [OQ-14](OPEN_QUESTIONS.md#oq-14) is still open.

**The vision's own first milestone is the fix.** "Continue Asterim" — resolve, read state, plan,
dispatch one worker, verify, report — is exactly the run that populates `tasks` with real evidence,
real timings and real cost. The product goal and the engineering unblock are the same action.

### 8d. The visual references were not attached

The brief calls them "extremely important" and instructs that the design language be extracted from
them. **No images reached this session, and none are in the repository.** The design language in
[UI.md](UI.md) was therefore neither confirmed nor revised against them — it stands as previously
written, which is: mission console, dark, dense, calm until something needs attention, cyan/amber
status semantics with verified contrast ratios.

`TO VERIFY` — re-attach the references and audit UI.md §1, §14 and §15 against them. This is cheap
and should not block anything.

---

## 9. Anti-goals

Stated so that a future change can be refused by pointing at a line rather than by argument.

| Not | Because |
|---|---|
| A chatbot with tools | The conversation is one input channel among four; the state is the product |
| A dashboard | A dashboard reports on a system. ORACLE *is* the system |
| An agent manager | If I am choosing agents, the orchestration failed |
| A launcher | Opening things is what I am trying to stop doing |
| A model with root | The privilege boundary is a process boundary, and that is not negotiable ([ADR-0003](DECISIONS.md#adr-0003--tool-execution-in-a-separate-process)) |
| An OS replacement | It is a resident environment *on top of* Windows. Deeper integration is a later question with its own evidence |
| A demo | Optimise for the 3,000th morning, not the first |

---

## 10. The one-sentence test

> **I turn on my computer, ORACLE is already there, and I can simply tell it what I want done.**

Any phase that does not move this sentence closer to true needs a reason that is written down.
