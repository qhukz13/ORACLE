# Working on ORACLE (instructions for coding agents)

You are working on a **local-first personal AI agent**. This file is the contract for any agent —
Claude Code, Antigravity, or a future one — that modifies this repository.

## Before you write any code

1. Read `docs/current_task.md`. That is your assignment. If it says "design only", do not implement.
2. Read `docs/ARCHITECTURE.md` for the layer boundaries you must not violate.
3. Check `docs/DECISIONS.md` before choosing any technology. Most choices are already made and have
   recorded reasons. If you disagree, add a new ADR that supersedes the old one — do not silently
   deviate.
4. Check `docs/OPEN_QUESTIONS.md`. If your task depends on an item marked `EXPERIMENT NEEDED`, run
   the experiment and record the result before building on the assumption.

## When you finish

Update **both**:
- `docs/current_report.md` — overwrite with what you did (it is a snapshot, not a changelog).
- `docs/current_task.md` — set it to the next task, or mark the current one `DONE` and state what's next.

Write a development log to `logs/development/YYYY-MM-DD-<slug>.md` for any non-obvious investigation,
benchmark, or dead end. Dead ends are the most valuable thing you can record.

## Hard rules

| Rule | Why |
|---|---|
| Never call `subprocess` with `shell=True` | Command injection via model-controlled arguments. Use argv lists. |
| Never let the LLM layer import the tool-execution layer | The privilege boundary is a process boundary. See ARCHITECTURE.md. |
| Never widen a filesystem scope to make a test pass | Scopes are the security model. Fix the test. |
| Never log a secret, token, or API key | Redaction is applied at the sink; do not bypass the sink. |
| Never add a dependency without justifying it in TECH_STACK.md | Dependency count is a maintenance budget. |
| Never mark something done that you did not verify | Report the failing output instead. |

## Conventions

- **Python 3.12**, managed by `uv`. Do not use the system Python (3.14 / 3.10 are both installed and
  both are wrong — see ADR-0002).
- Formatting/lint: `ruff format` + `ruff check`. Types: `mypy --strict` on `packages/core`.
- TypeScript: `strict: true`, no `any` without a comment explaining why.
- Every tool, event, and API payload is a **pydantic model**. Schemas are generated from those models —
  never hand-write a JSON schema or a TS interface that duplicates one.
- Tests live next to what they test. Security tests live in `tests/security/` and are not optional.
- Commit messages: `area: imperative summary`. Areas: `core`, `tools`, `policy`, `rag`, `api`, `ui`,
  `integrations`, `docs`, `build`.

## Uncertainty markers

Use these literally in docs and code comments. They are grepped.

- `UNKNOWN` — nobody has established this yet.
- `ASSUMPTION` — we are proceeding as if this is true; it is not verified.
- `TO VERIFY` — verifiable cheaply; someone should just check.
- `EXPERIMENT NEEDED` — requires building a spike to answer.

Do not silently resolve one of these. Resolve it, then delete the marker and record the finding.
