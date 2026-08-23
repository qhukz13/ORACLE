# ORACLE — Local API

One API. Every client — desktop shell, browser tab, phone, voice daemon — speaks it, with no
privileged back channel. See [ADR-0007](DECISIONS.md#adr-0007--clients-are-peers-of-one-local-api).

## 1. Shape

| Transport | Used for |
|---|---|
| **REST** | things you can name and fetch: projects, tasks, documents, devices, settings, history |
| **WebSocket** | everything that streams: tokens, tool output, terminal bytes, events, approvals |

Base: `https://127.0.0.1:8787/api/v1` (loopback by default; LAN is opt-in per launch).
Versioned in the path; the WS envelope carries its own `v`.

**Source of truth:** pydantic models. OpenAPI, JSON Schema and the frontend's TypeScript types are all
generated from them. Hand-writing a TS interface that mirrors a Python model is a review rejection.

---

## 2. WebSocket protocol

### Envelope

```json
{ "v": 1, "seq": 1043, "ts": "2026-08-21T03:43:07.412Z",
  "trace_id": "tr_9f2c", "session_id": "s_01J8",
  "type": "tool.finished",
  "payload": { "tool": "git.status", "ok": true, "duration_ms": 84 } }
```

- `seq` — **global, monotonic, gap-free** across the whole server. Clients treat a gap as data loss
  and re-sync. Client→server frames use `id` for correlation instead.
- `type` — namespaced `domain.event`; unknown types **must be ignored**, not error, so a newer server
  can talk to an older client.

### Connect and resume

```
GET /api/v1/stream?since_seq=1042
Authorization: Bearer <device-token>
```

The server replays from `since_seq + 1` (bounded by retention) then streams live. This single
mechanism is what makes a phone on flaky Wi-Fi workable — reconnection is exact, not approximate, and
never produces duplicates or holes. If `since_seq` is older than retention, the server sends
`session.resync` with a state snapshot and a new baseline `seq`.

### Server → client events

| Type | Payload |
|---|---|
| `session.created` / `session.resync` | session, baseline seq |
| `turn.started` / `turn.finished` | turn id, duration, outcome |
| `agent.state` | one of the [state-machine states](AGENT_RUNTIME.md#3-state-machine) |
| `message.delta` / `message.completed` | streaming assistant text |
| `plan.proposed` / `plan.step.started` / `plan.step.finished` | the plan object, step results |
| `tool.started` / `tool.output` / `tool.finished` | tool id, args digest, streamed chunks, typed result |
| `approval.requested` / `approval.resolved` | the full [approval object](SECURITY.md#5-confirmation-and-approvals) |
| `task.created` / `task.updated` / `task.finished` | task record |
| `external.progress` | normalised external-agent events |
| `term.output` | `{pty_id, stream: "stdout"\|"stderr", data}` |
| `index.progress` | collection, files done/total |
| `system.metrics` | cpu, ram, gpu, vram — throttled to 1 Hz |
| `system.degraded` | `{component, reason}` — drives the UI banner |
| `log.entry` | structured log line (level-filtered per client) |
| `error` | typed error |

### Client → server commands

| Type | Payload | Notes |
|---|---|---|
| `session.message` | `{text, attachments?}` | the main entry point |
| `session.cancel` | `{turn_id}` | |
| `task.cancel` | `{task_id}` | |
| `approval.respond` | `{approval_id, decision: "approve"\|"reject"}` | **T3 rejected from non-desktop devices**. Implemented 2026-08-21; `nonce` and `scope` are deferred — see below |
| `undo` | `{undo_id?}` | reverses one journalled mutation; omit the id for the most recent |
| `term.input` | `{pty_id, data}` | human input only; agent PTY writes go through the tool path |
| `term.resize` | `{pty_id, cols, rows}` | |
| `delegate` | `{task, project, allowed_tools?}` | starts a delegation (P6-T2). The service asks its own question — the egress preview rides `approval.requested` — before anything leaves the machine |
| `delegate.discard` | `{task_id}` | throw away a finished delegation's worktree; the packet stays on disk as the record of what was sent |
| `halt` | `{reason}` | must work in every state, never touches the LLM |
| `subscribe` | `{topics[]}` | mobile subscribes narrowly to save battery |

**Delegation events.** A delegation streams over the reserved `task.*` types (`created` →
`updated` with `rendering`/`awaiting_egress`/`running`/`verifying` → `finished` with diff stat,
gate-run test verdict and cost) plus a coalescable `delegate.event` feed (the delegate's normalised
started/thinking/tool_use/text stream). All carry `task_id` on the wire — added to the envelope with
this feature; clients ignore unknown fields by contract.

**On `nonce` and `scope`.** Both were in the original sketch and neither is implemented, on purpose.
A `nonce` guards against a replayed approval; approvals are already single-use and keyed by an
unguessable id, and resolving one twice cannot overturn the recorded answer, so a nonce would add a
field without adding a property. A `scope` ("approve for this session") is deliberately absent: the
answer to prompt fatigue is *fewer prompts*, via reversibility and the T1 tier, not cheaper ones
([SECURITY.md](SECURITY.md#2-design-principles)). Revisit only with data from
[OQ-13](OPEN_QUESTIONS.md#oq-13).

### Backpressure

A slow client (phone on 3G) must never stall the runtime. Per-connection bounded queue; on overflow,
**coalescable** events (`system.metrics`, `term.output`, `log.entry`) are dropped oldest-first while
**critical** events (`approval.requested`, `task.*`, `error`, `agent.state`) are never dropped. If the
critical queue overflows, the connection is closed and the client reconnects with `since_seq`.

Classifying every event as coalescable or critical is a required field on the event definition, not a
runtime judgement call.

---

## 3. REST endpoints

```
GET    /health                          liveness; no auth
GET    /api/v1/status                   agent state, model, versions, degradations

GET    /api/v1/projects                 registry with detected type + git state
GET    /api/v1/projects/{id}
POST   /api/v1/projects/{id}/scan

GET    /api/v1/sessions                 list
GET    /api/v1/sessions/{id}/events     paged history (?since_seq=&limit=)
DELETE /api/v1/sessions/{id}

GET    /api/v1/tasks                    ?status=active|waiting|done|failed
GET    /api/v1/tasks/{id}               full record incl. steps and costs
POST   /api/v1/tasks/{id}/cancel

GET    /api/v1/approvals                pending
POST   /api/v1/approvals/{id}           {decision, nonce}

POST   /api/v1/search                   {query, sources[], filters} → grouped results
GET    /api/v1/collections              index health per collection
POST   /api/v1/collections/{id}/reindex

GET    /api/v1/memory/facts             ?scope=&project=
PATCH  /api/v1/memory/facts/{id}
DELETE /api/v1/memory/facts/{id}

GET    /api/v1/pipelines                discovered definitions
POST   /api/v1/pipelines/{id}/run

GET    /api/v1/logs                     ?level=&source=&trace_id=&since=
GET    /api/v1/blobs/{hash}             large tool output / screenshots

GET    /api/v1/devices                  paired devices
POST   /api/v1/devices/pair             {pairing_code} → device token
DELETE /api/v1/devices/{id}             revoke

POST   /api/v1/halt
POST   /api/v1/resume                   clears HALT; desktop only
```

Conventions: cursor pagination (`?cursor=&limit=`, `limit` capped at 200) · `ETag`/`If-None-Match` on
project and collection reads · `Idempotency-Key` required on every POST that starts work, so a mobile
retry cannot launch a pipeline twice.

---

## 4. Errors

```json
{ "error": { "code": "policy_denied", "message": "Writing outside an allowed scope.",
             "detail": "C:/Windows/System32 is in deny_always",
             "trace_id": "tr_9f2c", "retryable": false } }
```

| Code | HTTP | |
|---|---|---|
| `unauthenticated` / `forbidden_device` | 401 / 403 | bad or under-privileged token |
| `policy_denied` | 403 | includes the rule that fired |
| `approval_required` / `approval_expired` | 409 | |
| `not_found` / `invalid_request` | 404 / 422 | |
| `halted` | 423 | ORACLE is stopped; only `resume` is accepted |
| `degraded` | 503 | a component is unavailable; `detail` names it |
| `rate_limited` | 429 | `Retry-After` |

`message` is human-facing and redacted; `detail` is developer-facing and may be verbose. Both pass
through the redaction sink.

---

## 5. Authentication

- **Loopback default.** Bound to `127.0.0.1`; no token required for local clients on first run, but a
  token is still issued and used so the code path is identical everywhere.
- **LAN mode is explicit** and forces TLS + tokens. See
  [SECURITY.md §8](SECURITY.md#8-network-and-device-authentication) and [MOBILE.md](MOBILE.md).
- Bearer token per device, Argon2id-hashed at rest, revocable individually.
- Per-device **capability profile** enforced server-side — a phone token is rejected for `approval.respond`
  on a T3 action regardless of what the client sends. Client-side restriction alone is not a control.

## 6. Versioning

`/api/v1` changes only in backward-compatible ways: new fields, new event types, new endpoints.
Breaking changes bump to `/api/v2`, and the server may serve both during a transition. Clients must
ignore unknown event types and unknown fields — this rule is what allows the mobile PWA (cached, and
possibly stale) to keep working across a backend update.
