# 2026-08-21 — OQ-05: does `agy -p` emit stdout when piped?

**Answer: YES, with `--output-format json` or `stream-json`.** [OQ-05](../../docs/OPEN_QUESTIONS.md#oq-05)
is resolved. Antigravity moves from **Potential (blocked)** to **Supported**.

## Setup

`agy` **v1.1.14**, installed at `C:\Users\qhukz\AppData\Local\agy\bin\agy`, already authenticated.
stdout redirected to a file in every case — i.e. **not a TTY**, the exact condition of issue #76.

## Test 1 — `--output-format json`

```bash
agy -p "Reply with exactly: hello" --output-format json > out.json 2> err.txt
```

```
exit=0   stdout=257 bytes
{"conversation_id":"2c62cd80-…","status":"SUCCESS","response":"hello\n",
 "duration_seconds":12.29,"num_turns":1,
 "usage":{"input_tokens":14119,"output_tokens":23,"thinking_tokens":22,
          "cache_read_tokens":0,"total_tokens":14142}}
```

Complete, valid JSON. **No stdout loss.**

## Test 2 — `--output-format stream-json`

```
exit=0   stdout=2278 bytes   5 NDJSON lines
```

| line | `event` | payload keys |
|---|---|---|
| 1 | `init` | `cwd`, `permission_mode`, `tools` |
| 2 | `step_update` | `conversation_id`, `state`, `step_index`, `step_type` |
| 3 | `step_update` | + `duration_seconds` |
| 4 | `step_update` | + `text_delta`, `usage` |
| 5 | `result` | `conversation_id`, `duration_seconds`, `num_turns`, `response`, `status`, `usage` |

**Envelope shape — note this, the docs don't spell it out.** It is *not* `{"type": "...", ...}`.
The discriminator is an `event` field, and the payload sits under a key **named after the event**:

```json
{"event": "init",        "init":        {"cwd": "...", "tools": [...], "permission_mode": "..."}}
{"event": "step_update", "step_update": {"step_index": 0, "state": "ACTIVE", "text_delta": "..."}}
{"event": "result",      "result":      {"status": "SUCCESS", "response": "hello\n", "usage": {...}}}
```

So the adapter parses `body = obj[obj["event"]]`, not `obj["payload"]`.

## Interpretation of issue #76

The open bug reports stdout loss for `agy --print`/`-p` in **default (text) mode**. The
`--output-format json|stream-json` paths are unaffected and were likely added after the report.

**→ Always pass `--output-format`.** Never rely on default text output from a subprocess. That single
rule is the whole mitigation, and it costs nothing since ORACLE wants structured output anyway.

## Consequences

1. **`AntigravityAdapter` is unblocked** and can be built in Phase 6 alongside `ClaudeCodeAdapter`.
2. Baseline overhead is notable: **14,119 input tokens** to answer "say hello" — `agy` injects a
   large system prompt. Not a blocker (delegation is inherently expensive), but it means Antigravity
   is a poor choice for small/cheap calls; route those to Claude or the local model.
3. `usage` reports `thinking_tokens` separately — useful for the cost display in the Task Inspector.
4. `status` is `SUCCESS` and exit code 0; both must be checked, per the docs' status enum
   (`SUCCESS|ERROR|CANCELED|INTERRUPTED|WAITING|INVALID|RUNNING`).

## Still untested

- Behaviour when **unauthenticated** in a non-TTY (docs claim a clean `authentication required` error
  rather than a hang — `preflight()` depends on this).
- `--json-schema` structured output.
- Cancellation semantics (SIGINT/SIGTERM) and worktree scoping via a Project boundary.

All three are Phase 6 adapter work, not blockers.
