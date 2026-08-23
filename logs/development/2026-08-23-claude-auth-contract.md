# 2026-08-23 — The delegation auth contract, corrected before code was written against it

Requirement 1 of [P6-T1](../../docs/current_task.md). The pinned invocation in
[INTEGRATIONS.md §3](../../docs/INTEGRATIONS.md#3-claude-code-cli--supported) was
`claude -p --bare` + `ANTHROPIC_API_KEY` from Credential Manager. The owner's actual setup is a
**Claude Max subscription with zero API credit** — a fact the contract never accounted for, surfaced
when the smoke-run script asked for a key the owner does not have.

## Measured, on the installed CLI (v2.1.238, up from the pinned v2.1.234)

| Invocation | Result |
|---|---|
| `-p --bare`, no key | `apiKeySource: "none"` → **"Not logged in · Please run /login"**, `duration_api_ms: 0`, cost $0 — fails closed, nothing egresses |
| `-p` without `--bare`, no key | Authenticates via the existing subscription login. `is_error: false`, answer collected, `total_cost_usd` reported (subscription quota, not billing) |

Documentation check (via the claude-code-guide agent, against the official docs): `--bare`
authenticates **only** via `ANTHROPIC_API_KEY` or an `apiKeyHelper` — it reads neither the OAuth
keychain nor `CLAUDE_CODE_OAUTH_TOKEN`, so `claude setup-token` does not rescue it. No documented
flag combination reproduces `--bare`'s hook isolation. Subscription use for headless `-p` is
permitted.

## The decision

Run **without `--bare`** and rebuild its isolation materially:

1. **Worktree scrub** — delegates only ever run in ORACLE's disposable worktree, so ORACLE deletes
   `.claude/` and `.mcp.json` from the worktree before invocation. Hooks cannot load from files
   that do not exist. This carries the load `--bare` carried; asserted in P6-T1 by a planted-hook
   fixture that must never fire.
2. `--setting-sources user` and `--strict-mcp-config` as belt-and-braces for settings-borne hooks
   and MCP configs.
3. Residual, accepted and logged: user-level (`~/.claude`) hooks and plugins still load — the
   owner's own machine configuration, visible in `system/init`, which the adapter checks.

No `ANTHROPIC_API_KEY` handling anywhere in ORACLE: the subscription login is the only credential,
and ORACLE never reads or stores it. The Credential Manager path stays documented in
INTEGRATIONS.md as the alternative for an API-billing machine, where `--bare` becomes preferable
again.

The `--bare` contract row had said the flag "will become the `-p` default" — when that lands, a
non-bare invocation may need an explicit opt-out. Left as a watch item on the quarterly vendor
re-verification (Phase 11).

Updated: `docs/INTEGRATIONS.md` §3 · `docs/current_task.md` (req 1, req 4, constraints, acceptance)
· `scripts/record_claude_stream.py`.
