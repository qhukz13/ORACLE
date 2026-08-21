# ORACLE

**A local-first personal AI agent that runs on my PC, orchestrates other agents, and never gets unsupervised root.**

ORACLE is not "a chatbot with shell access". It is a small **orchestrator** that understands intent,
selects a capability, routes work to the cheapest competent executor, and reports back — while every
side effect passes through a policy layer that can refuse.

```
You ──▶ ORACLE ──▶ intent ──▶ route ──┬─▶ local tool        (fast, free, guarded)
 ▲                                    ├─▶ project knowledge (hybrid retrieval)
 │                                    ├─▶ Claude Code       (deep code reasoning)
 │                                    ├─▶ Antigravity       (alternate coding agent)
 │                                    └─▶ pipeline          (declarative workflow)
 └──────────────── result ◀───────────┘
```

The local model is deliberately small. It is a **router and a narrator**, not the thing that writes
your code. Heavy reasoning is delegated to agents that are good at it; ORACLE's value is knowing
*what* to delegate, *with what context*, and *what to allow*.

---

## Status

★ **The MVP is complete.** Phases 0–4 done. Next: [Phase 5 — Project knowledge](docs/ROADMAP.md#phase-5--project-knowledge-rag--post-mvp).

ORACLE runs, routes, acts, and has an interface. Ask it to check a repository and it runs `git.status`
and shows you the card. Ask it to push and it stops and shows you the commits that would leave the
machine. Type in the terminal dock and your keystrokes reach a real ConPTY inside a Job Object that
the HALT key can kill.

| | |
|---|---|
| Tools | **29** behind the policy gate (26 offerable, 11 reachable from a routed turn) |
| Tests | **370 Python** + **77 TypeScript**; the security suite is a merge gate |
| Router | `qwen3.5:0.8b`, 93.3% intent accuracy, 100% tool selection on the eval set |
| Isolation | every tool runs in a separate low-privilege process inside a Job Object |

What is not there yet: knowledge retrieval, delegation to Claude/Antigravity, pipelines, mobile, and
the orbital view.

Read `docs/` first — this repository is design-first and the documents lead the code.

| Question | File |
|---|---|
| What is this and how is it shaped? | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| What am I supposed to build right now? | [docs/current_task.md](docs/current_task.md) |
| What happened last? | [docs/current_report.md](docs/current_report.md) |
| What order does it get built in? | [docs/ROADMAP.md](docs/ROADMAP.md) |
| What technology, and why that one? | [docs/TECH_STACK.md](docs/TECH_STACK.md) |
| Why was X decided? | [docs/DECISIONS.md](docs/DECISIONS.md) |
| What don't we know yet? | [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) |

Full index: **[docs/README.md](docs/README.md)**

---

## The five rules

These are load-bearing. Violating one is an architecture bug, not a style preference.

1. **The model never touches the OS.** It emits a *tool request*. The Policy Engine decides. The Tool
   Host executes. Three separate processes, three separate trust levels.
2. **Local-first by default, cloud by exception.** Anything leaving this machine is an explicit,
   previewable, auditable event — not a side effect.
3. **The desktop shell holds zero business logic.** Every client (desktop, browser, phone, voice)
   is an equal peer speaking the same local API. This is why voice and mobile don't require touching
   the agent core.
4. **Untrusted content cannot escalate privilege.** File contents, repo READMEs, web pages and other
   agents' output are *data*. Ingesting them taints the turn and raises the confirmation bar.
5. **Everything is an event.** The runtime is event-sourced, so any session can be replayed,
   audited, and tested deterministically.

---

## Target hardware

ORACLE is designed against a *specific, modest* machine — not a hypothetical workstation. This
constrains the model choice more than anything else in the design.

| | |
|---|---|
| CPU | Xeon E5-2670 v3 — 12c/24t, Haswell, AVX2, no AVX-512 |
| RAM | 32 GB |
| GPU | GTX 1050 Ti — **4 GB VRAM**, compute 6.1 (Pascal), driver 582.28 |
| Disk | C: 39.8 GB free (tight), D: 187 GB free, E: 190 GB free |
| OS | Windows 10 Pro 19045 |

**4 GB of VRAM is the binding constraint of this entire project.** See
[docs/TECH_STACK.md](docs/TECH_STACK.md#3-local-llm) for what actually fits and
[ADR-0004](docs/DECISIONS.md#adr-0004--two-tier-local-model-router--reasoner).

---

## License

Personal project. No license granted yet.
