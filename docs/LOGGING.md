# ORACLE — Logging Strategy

Logs exist to answer specific questions. The structure below is derived from the questions, not from
habit.

> **What happened? When? Which agent? Which task? Which tool? Which command? What was the result?
> What failed?**

Every one of those is answerable by a `trace_id` lookup, because `trace_id` is attached at ingress and
propagated through every layer, process boundary and child invocation.

## 1. Structure

```
logs/
├── app/            backend service: startup, config, HTTP, WS, lifecycle
├── agent/          one file per session: reasoning traces, intents, plans, context composition
├── tools/          every tool invocation: args digest, decision, duration, outcome
├── pipelines/      one directory per run: per-step logs and artifacts
├── integrations/   external agent transcripts (Claude, Antigravity), normalised + raw
├── audit/          SECURITY-relevant, hash-chained, append-only, never rotated away
├── errors/         crashes and unhandled exceptions with full context
└── development/    human/agent notes: investigations, benchmarks, dead ends
```

Two directories are unlike the others and that is deliberate:

- **`audit/`** is a security artifact, not a log. Hash-chained, never auto-deleted, verified by
  `oracle audit verify` ([SECURITY.md §9](SECURITY.md#9-audit-log)). It answers "what was permitted and
  by whom", and it must remain trustworthy even if everything else is wrong.
- **`development/`** is written by humans and coding agents, not by the application. Markdown, one
  file per investigation. **Dead ends are the most valuable content here** — the point is to stop the
  next agent (or me, in three months) from re-running a failed experiment.

## 2. Format

JSONL, one event per line. Machine-readable first; a `logs` view in the UI renders it for humans.

```json
{"ts":"2026-08-21T03:43:07.412Z","level":"info","event":"tool.finished",
 "trace_id":"tr_9f2c","session_id":"s_01J8","task_id":"t_4471","step_id":"st_3",
 "actor":"agent","tool":"git.status","tier":"T0","decision":"allow",
 "duration_ms":84,"outcome":"ok","project":"Asterim"}
```

Required on every record: `ts`, `level`, `event`, `trace_id`. Everything else is contextual.
`event` is a dotted name from a **closed vocabulary** shared with the WS event types
([API.md](API.md#server--client-events)) — one vocabulary across logs, events and the UI, so filters
learned in one place work everywhere.

| Level | Use |
|---|---|
| `debug` | development only; off by default (verbose, and prone to capturing content) |
| `info` | normal operation: tool ran, task finished, index updated |
| `warn` | degraded but handled: retry, fallback engaged, model unavailable |
| `error` | an operation failed and the user should know |
| `critical` | data loss, security control failure, HALT triggered |

## 3. Redaction — one sink, no bypass

**Every** log record passes through the redaction filter before it is written. There is no
`logger.raw()`. The filter is the same one used for events, prompts and outbound payloads
([SECURITY.md §7](SECURITY.md#7-secrets-and-egress)).

Rules:
- Tool arguments are logged as a **digest plus a redacted preview**, never raw.
- File *contents* are never logged; paths and hashes are.
- Environment variables are never logged, not even names in bulk.
- A redaction that fires is itself logged (`event: "redaction.applied"`, with the rule but not the
  value), so a suppressed secret is visible as an occurrence without exposing it.
- Prompts are logged only at `debug`, and only with content hashes at `info` — a prompt contains
  retrieved file content by construction.

## 4. Rotation and retention

| Directory | Rotation | Retention | Backed up |
|---|---|---|---|
| `app/` | 50 MB or daily | 14 days | no |
| `agent/` | per session | 30 days | no |
| `tools/` | daily | 30 days | no |
| `pipelines/` | per run | 30 runs per pipeline | no |
| `integrations/` | per delegation | 30 days | no |
| `audit/` | monthly | **forever** | **yes** |
| `errors/` | per crash | 90 days | yes |
| `development/` | never | forever (in git) | yes (git) |

Total budget: **2 GB**, with a warning at 80%. On breach, rotate the oldest non-audit files first;
`audit/` is never sacrificed to a size limit. Volume lives on `D:` — C: has under 40 GB free.

## 5. Correlation

```
trace_id   one user request, end to end, across every process and child agent
session_id conversation
task_id    a durable unit of work
step_id    one tool invocation
```

`trace_id` is generated at ingress and passed into the toolhost with each `ToolInvocation`, and into
external agents via their environment where the CLI allows it. Given a `trace_id` I can reconstruct:
the message, the intent, the retrieved context, the plan, every policy decision, every command, every
byte of output, and the final answer — across all three processes.

That is the single property that makes an autonomous system debuggable, and it is why `trace_id` is a
required field rather than a nice-to-have.

## 6. What is not logged

- File contents, prompt bodies at `info`+, model outputs verbatim (hash + length instead).
- Secrets, tokens, keys, `.env` contents — enforced at the sink, not by convention.
- Full process command lines from `sys.processes` (they routinely contain credentials).
- Screenshot pixels — the blob hash only.
- Personal content from indexed notes. The *fact* that a note was retrieved is logged; its text is not.

## 7. Development logs

`logs/development/YYYY-MM-DD-<slug>.md`, written by whoever did the work.

```markdown
# 2026-08-21 — Router model benchmark on GTX 1050 Ti

## Question
Does qwen3.5:2b fit in 3.5 GB usable VRAM with a workable context?

## Method
...

## Result
...

## Decision
...

## Dead ends
- Tried X. Failed because Y. Do not retry without Z.
```

These are committed to git (the only logs that are) and are the primary hand-off mechanism between
sessions and between agents. `AGENTS.md` requires one for any non-obvious investigation.

## 8. Implementation notes

- Backend: `structlog` over stdlib `logging`, JSON renderer, `trace_id` bound via `contextvars` so it
  propagates through async calls without being threaded manually through every signature.
- The toolhost logs to its own stream, which `oracled` merges — the toolhost never writes to shared
  files, keeping it write-isolated ([ARCHITECTURE §3](ARCHITECTURE.md#3-process-model)).
- Logging is **async and non-blocking**: a queue handler with a background writer. A slow disk must
  never stall the agent loop.
- The audit sink is the exception: it is **synchronous and fsynced**. A security record that might not
  have been written is worthless, and audit volume is low enough that the cost is irrelevant.
