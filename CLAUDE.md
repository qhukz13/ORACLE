# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) — it is the canonical instruction file for this repo.** This file only
adds Claude-specific notes.

## Claude-specific notes

- ORACLE *invokes you programmatically* (`claude -p --bare --output-format stream-json`). When you are
  editing the integration in `packages/integrations/claude/`, remember you are writing the code that
  drives your own CLI. The contract you rely on is pinned in
  [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md#3-claude-code-cli--supported); update that doc if the CLI
  surface changes.
- ORACLE also exposes an **MCP server** so that you, running inside a delegated task, can call back
  into ORACLE's guarded tools instead of running raw shell commands. Prefer that path.
- This repo is design-first. If `docs/current_task.md` scopes you to documentation, produce
  documentation — do not "helpfully" scaffold the application.
