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
| `graph.plan` | `{objective}` | `BUILT 2026-08-25` (P8-T1). An objective becomes a plan, a graph, and a run — with two approvals in between: the planning egress, then the graph's shape. Refusing either is a full stop |
| `graph.cancel` | `{root_id, task_id?}` | `BUILT 2026-08-25` (P7-T3). With `task_id`, stops one task and its dependents become `SKIPPED`; without, stops the whole graph. Independent branches keep running. Not HALT — HALT is above this and stops graphs this daemon never started |
| `halt` | `{reason}` | must work in every state, never touches the LLM |
| `subscribe` | `{topics[]}` | mobile subscribes narrowly to save battery |

### Inbound MCP (P6-T3)

Two loopback endpoints a *delegated agent's* bridge process calls, authorised by a delegation
capability rather than by being on the box ([INTEGRATIONS.md §4](INTEGRATIONS.md#4-oracle-as-an-mcp-server--supported)):

| Route | Body | Notes |
|---|---|---|
| `POST /api/v1/mcp/tools` | `{token}` | the tools this capability lends, as MCP descriptors. An unverifiable token gets an empty list — the client renders that as a server error, and a bridge that cannot list must not look like a working one |
| `POST /api/v1/mcp/call` | `{token, tool, arguments}` | executes through the ordinary `ToolExecutor`. Refusals are `{ok: false}` results, not HTTP errors: the delegate should read them and adapt, not conclude the server is broken and shell out |

Deliberately **not** on the WS protocol: the bridge is a short-lived child of the delegate's CLI, and
handing it the event socket would hand a delegated agent the whole command surface.

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

GET    /api/v1/projects                 BUILT 2026-08-26: tracked projects + candidates.
                                        Deliberately NO git state — see below
POST   /api/v1/projects?name=           BUILT 2026-08-26: register a discovered directory
GET    /api/v1/projects/{id}            BUILT 2026-08-26: the row + a fresh observation
POST   /api/v1/projects/{id}/scan       PLANNED — and probably never: there is nothing to
                                        scan into, because observed state is not stored

GET    /api/v1/sessions                 list
GET    /api/v1/sessions/{id}/events     paged history (?since_seq=&limit=)
DELETE /api/v1/sessions/{id}

GET    /api/v1/tasks?root_id=          BUILT 2026-08-25: one graph as a tree
GET    /api/v1/tasks                    PLANNED: ?status=active|waiting|done|failed
GET    /api/v1/tasks/{id}               PLANNED: full record incl. costs
POST   /api/v1/tasks/{id}/cancel        PLANNED as REST; the built path is the `graph.cancel`
                                        command, because cancelling is a live action on a live
                                        graph and the WS stream is where the answer arrives

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

### `continue.derived` — where a continue objective came from  `BUILT 2026-08-26`

Emitted once per `continue`, before planning. **Critical**, not coalescable: it is the
provenance record for a planning decision, and the approval card that follows says the
objective is partly untrusted without saying how it got that way.

```json
{"project": "Asterim", "open_tasks": 3, "dropped": 0,
 "notes": ["docs/current_task.md"], "tainted": true}
```

`notes` names the project's own files that were quoted into the objective
([PROJECT_STATE.md §5](PROJECT_STATE.md#5-unfinished-work--where-continue-gets-its-list)).
`dropped` is how many open tasks did not fit the cap — present so a client can say
"3 of 40" rather than implying it saw everything.

---

### `GET /api/v1/projects` — the registry  `BUILT 2026-08-26`

Two lists, and the split is the point ([PROJECT_STATE.md §3](PROJECT_STATE.md#3-the-model)):

- **`projects`** — rows ORACLE tracks. Registration is an explicit human act, so this stays
  short enough to brief.
- **`candidates`** — directories `discover_projects()` found that nobody has registered.
  The real projects root on this machine holds `New folder` and `docs.zip` next to the real
  ones; auto-registering everything would fill the briefing with them.

**This endpoint runs no `git`.** A sidebar with twenty projects would otherwise be twenty
subprocesses on a page-load. `status` is the stored value corrected by a fresh `is_dir()` —
a directory deleted since boot reports `missing` immediately rather than at the next
restart, because existence is observed state too.

`POST /api/v1/projects?name=` registers. `name` **must be one `discover_projects()` actually
found**; anything else is a 404. That is a safety rule, not a convenience — a name outside
the candidate list would be a filesystem path assembled from a request. Registering is
idempotent by name, and it **grants nothing**: scopes live in `config/policy.yaml` where a
human edits them and git records the edit, asserted in `tests/security/`.

### `GET /api/v1/projects/{id}` — one project  `BUILT 2026-08-26`

The stored row, plus an `observation` object read **fresh on every call** through
`git.status` and `git.log` (both T0, both across the policy gate). Nothing in it is cached
or persisted: a cached branch name is wrong the moment someone switches branches, silently,
with no event that could correct it.

`observation.error` is a **field, not a status code**. A directory that is not a repository,
a root that has been deleted, and a path the policy engine refuses all return `200` with the
reason in that field — because every caller of this is a surface that has to render
something, and a crashed sidebar is worse than a row that says why it is empty.

---

### `GET /api/v1/tasks?root_id=` — the execution tree  `BUILT 2026-08-25`

A **projection over the `tasks` table** (ORCHESTRATION.md §6), not a second source of truth: no
cache, no second writer, and the same shape whether the graph is running or finished.

```json
{
  "root_id": "tk_root",
  "live": true,
  "status": "running",
  "tasks": [
    {
      "id": "check", "kind": "verify", "status": "failed",
      "depends_on": ["fix"], "objective": "…", "role": "tester",
      "agent": null, "attempt": 1, "supersedes": null,
      "started_at": "…", "finished_at": "…",
      "summary": "1 test that passed before this work now fails",
      "evidence": {"observed": {"passed": 583, "failed": 29}, "new_failures": ["…"]},
      "claim": "everything passes",
      "error": {"kind": "execution_failed", "message": "…", "retryable": false}
    }
  ]
}
```

Three properties worth stating, because each is a decision:

* **`evidence` and `claim` arrive as separate fields** and must stay separate on screen. Evidence
  is what ORACLE measured; the claim is what the worker said about its own work. A client that
  renders them together has undone the verification design at the last possible moment.
* **`live` is the only thing not in the table** — it means "this process is still running it".
* **An unknown `root_id` returns an empty tree, not 404.** A client asking has already seen a
  `task.*` event; a 404 would tell it to retry something that will never appear.

Live updates ride the existing WS `task.*` events, which carry `"source": "graph"`. That stamp
matters: a `DELEGATION` task emits `task.*` twice over — once as graph state, once as its own
lifecycle — under the same `task_id`, and both are wanted.

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
