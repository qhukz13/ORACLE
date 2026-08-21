# logs/

Runtime and development logs. Strategy, format, redaction and retention are specified in
**[docs/LOGGING.md](../docs/LOGGING.md)** — this file only covers what lives where.

```
logs/
├── app/            backend service: startup, config, HTTP, WS, lifecycle
├── agent/          per-session reasoning traces: intents, plans, context composition
├── tools/          every tool invocation: args digest, policy decision, duration, outcome
├── pipelines/      one directory per run: per-step logs and artifacts
├── integrations/   external agent transcripts (Claude, Antigravity), normalised + raw
├── audit/          SECURITY-relevant, hash-chained, append-only, never rotated away
├── errors/         crashes and unhandled exceptions with full context
└── development/    human/agent notes: investigations, benchmarks, dead ends
```

## Two directories are special

**`audit/`** is a security artifact, not a log. Hash-chained, verified by `oracle audit verify`, never
auto-deleted, and never sacrificed to a size limit. Written synchronously and fsynced — a security
record that might not have been written is worthless.

**`development/`** is written by people and coding agents, not by the application. Markdown, one file
per investigation, **committed to git** (the only logs that are). Dead ends are the most valuable
content here: the point is to stop the next agent from re-running a failed experiment.

```
logs/development/YYYY-MM-DD-<slug>.md
```

`AGENTS.md` requires one for any non-obvious investigation, benchmark, or dead end.

## Volume

Application logs are written to **`D:\ORACLE\logs`** at runtime — C: has under 40 GB free. This
directory holds the committed structure and the development notes; during development it may be
symlinked to the D: location.

Total runtime budget is 2 GB with a warning at 80%. Rotation drops the oldest non-audit files first.

## Rules

- **Never commit runtime logs.** `.gitignore` tracks the directory structure and `development/*.md`,
  nothing else.
- **Never write a secret to a log.** Redaction is applied at the sink; there is no `logger.raw()`.
- Every record carries `ts`, `level`, `event`, `trace_id`. Given a `trace_id` you can reconstruct an
  entire request across all three processes.
- File contents, prompt bodies, and model outputs are logged as hashes and lengths, never verbatim.
