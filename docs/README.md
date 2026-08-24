# ORACLE — Documentation Index

Written so a coding agent with **no prior conversation context** can read its way to competence.

## If you are picking this up cold

Read in this order. It takes about 30 minutes and is worth it.

1. **[../README.md](../README.md)** — what ORACLE is, the five rules, the target hardware.
2. **[ARCHITECTURE.md](ARCHITECTURE.md)** — layers, process model, control flow, degradation. The map.
3. **[current_task.md](current_task.md)** — **your actual assignment.**
4. **[DECISIONS.md](DECISIONS.md)** — 23 ADRs. Check before choosing any technology; most choices are made.
5. **[OPEN_QUESTIONS.md](OPEN_QUESTIONS.md)** — what is *not* known. Check before assuming.
6. **[ROADMAP.md](ROADMAP.md)** — the phase your task belongs to, and its Definition of Done.

Then read whichever subsystem doc your task touches.

## Full index

### Start here
| Doc | Answers |
|---|---|
| [current_task.md](current_task.md) | What am I supposed to be doing right now? |
| [current_report.md](current_report.md) | What did the last agent do, and what did it leave behind? |
| [ROADMAP.md](ROADMAP.md) | What gets built, in what order, and what "done" means |
| [../AGENTS.md](../AGENTS.md) | The rules for modifying this repository |

### Design
| Doc | Answers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the system is shaped; what talks to what |
| [DECISIONS.md](DECISIONS.md) | Why each major choice was made, and what it cost |
| [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | What is unknown, assumed, or needs an experiment |
| [TECH_STACK.md](TECH_STACK.md) | Which technologies, why them, what was rejected |

### Subsystems
| Doc | Answers |
|---|---|
| [AGENT_RUNTIME.md](AGENT_RUNTIME.md) | The turn pipeline, context budget, cancellation |
| [ORCHESTRATION.md](ORCHESTRATION.md) | The supervisor, the task graph, scheduling, failure, replanning |
| [PLANNER.md](PLANNER.md) | Structured plans, TaskSpecs, roles, agent selection, fallbacks |
| [ASTERIM_REUSE.md](ASTERIM_REUSE.md) | What Asterim already solved, and what ORACLE ports from it |
| [TOOLS.md](TOOLS.md) | The tool catalogue, contracts, and how to add one |
| [SECURITY.md](SECURITY.md) | Threat model, capabilities, scopes, risk tiers, taint, audit |
| [RAG.md](RAG.md) | What gets indexed, how it's chunked, how retrieval works |
| [MEMORY.md](MEMORY.md) | What ORACLE remembers, and how it can be corrected |
| [INTEGRATIONS.md](INTEGRATIONS.md) | Claude Code, Antigravity, MCP, Handoff Packets |
| [PIPELINES.md](PIPELINES.md) | Declarative workflows and their limits |
| [API.md](API.md) | REST + WebSocket protocol, events, errors, auth |
| [MOBILE.md](MOBILE.md) | Phone client, pairing, TLS, what mobile may not do |
| [UI.md](UI.md) | Layout, the core view, components, a11y, keyboard, states |
| [DATABASE.md](DATABASE.md) | Schema for both SQLite files |
| [LOGGING.md](LOGGING.md) | Log structure, format, redaction, retention |
| [TESTING.md](TESTING.md) | How a non-deterministic system gets a regression suite |

## Where to look for a given question

| Question | Doc |
|---|---|
| Can the agent run `rm -rf`? | [SECURITY.md §3](SECURITY.md#risk-tiers) · [TOOLS.md](TOOLS.md#deliberately-absent) |
| Why not Postgres/Qdrant? | [ADR-0006](DECISIONS.md#adr-0006--sqlite-only-storage-two-files-sqlite-vec--fts5) |
| Which model, and will it fit? | [TECH_STACK.md §3](TECH_STACK.md#3-local-llm) · [OQ-01](OPEN_QUESTIONS.md#oq-01) |
| How does delegation to Claude work? | [INTEGRATIONS.md §3](INTEGRATIONS.md#3-claude-code-cli--supported) |
| What is a task / task graph / plan? | [ORCHESTRATION.md](ORCHESTRATION.md) · [PLANNER.md](PLANNER.md) |
| Who plans, who supervises, who executes? | [ARCHITECTURE.md §1](ARCHITECTURE.md#1-what-oracle-is) · [ADR-0019](DECISIONS.md#adr-0019--the-supervisor-completes-the-orchestrator) |
| What happens when Ollama is down? | [ARCHITECTURE.md §8](ARCHITECTURE.md#8-degradation--what-happens-when-a-piece-is-missing) |
| How do I add a tool? | [TOOLS.md §5](TOOLS.md#5-adding-a-tool--the-checklist) |
| What is NOT being built yet? | [ROADMAP.md](ROADMAP.md#the-mvp-stated-once) · [ADR-0016](DECISIONS.md#adr-0016--mvp-excludes-the-interesting-parts) |
| Why is my folder not indexed? | [RAG.md §2](RAG.md#2-what-gets-indexed) |
| What can the phone do? | [MOBILE.md §1](MOBILE.md#1-what-mobile-is-for) |

## Conventions

- `VERIFIED <date>` — checked against a primary source on that date.
- `UNKNOWN` · `ASSUMPTION` · `TO VERIFY` · `EXPERIMENT NEEDED` — see [../AGENTS.md](../AGENTS.md#uncertainty-markers).
  These are grepped; do not resolve one silently.
- `current_task.md` and `current_report.md` are **snapshots, not history.** Overwrite them.
  History lives in git and in `logs/development/`.
- ADRs are append-only. Supersede; never rewrite.
