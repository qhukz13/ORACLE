# ORACLE — Security Model

> ORACLE will eventually have access to source code, credentials, the terminal, and the whole PC.
> The threat model is not "a hacker on the internet". It is **my own agent, doing something stupid or
> manipulated, very fast, while I am not looking.**

## 1. Threat model

| # | Threat | Realistic? | Primary control |
|---|---|---|---|
| T1 | The local model hallucinates a destructive tool call (`delete` on the wrong path) | **Very** — small models are unreliable | Risk tiers + scopes + confirmation |
| T2 | **Prompt injection** via indexed content: a README, a note, a dependency's docs, a web page, or another agent's output says "run this" | **Very** — this is the #1 real risk | Taint tracking (§6), instruction/data separation, provenance in the UI |
| T3 | Secret exfiltration to a cloud agent inside "context" | **Very** — context assembly reads whole files | Redaction + egress preview (§7) |
| T4 | A tool escapes its filesystem scope via traversal, symlink, junction, or 8.3 name | Likely, on Windows especially | Path canonicalisation (§4) |
| T5 | Someone on the LAN reaches the agent API | Likely on shared Wi-Fi | TLS + device pairing + tokens (§8) |
| T6 | Runaway process tree (`npm install` spawning forever) | Likely | Job objects, timeouts, HALT |
| T7 | A confirmed action is not the action executed (TOCTOU / approval reuse) | Possible | Bound approvals (§5) |
| T8 | Audit log tampering, by me or by the agent | Possible | Hash-chained append-only log (§9) |
| T9 | A malicious npm/pip package executing during a tool run | Possible | Out of scope for v1 — documented, not solved |
| T10 | Physical/local attacker with the machine unlocked | — | Out of scope. OS login is the boundary. |

**Explicitly out of scope for v1**, so nobody assumes otherwise: T9 supply-chain execution, T10
physical access, kernel-level isolation, and defending against an attacker who already has code
execution as my user. ORACLE reduces blast radius; it is not a sandbox escape-proof jail.

---

## 2. Design principles

1. **Deny by default.** A tool with no matching allow rule is denied, not permitted.
2. **Fail closed.** Unparseable policy → read-only mode. Unknown capability → deny. Unresolvable path → deny.
3. **Policy is data, not code, and lives outside the model's reach.** Nothing the model emits and
   nothing retrieved from a document can modify policy. Policy is loaded once at start and on explicit
   reload — never from a tool.
4. **One chokepoint.** Every side effect crosses the Policy Gate exactly once. There is no second path.
5. **The user confirms *actions*, not *intentions*.** The confirmation UI shows the exact rendered
   argv/path/diff that will execute — never a paraphrase the model wrote.
6. **Reversibility beats permission.** Where an undo is possible, prefer auto-execute + undo over a
   confirmation prompt. Prompt fatigue is a security failure: an agent that asks 40 times a day
   trains me to click Approve without reading.

Principle 6 is the one most such designs get wrong, and it drives the risk-tier table below.

---

## 3. Capabilities, scopes, risk tiers

### Capabilities

Every tool declares the privileges it needs. Capabilities are coarse and few, so they stay auditable.

```
fs.read      fs.write     fs.delete    proc.spawn   proc.kill
net.egress   input.synth  sys.info     sys.settings git.write
secret.read  agent.delegate
```

A tool cannot request a capability at runtime — it is fixed in the tool contract, checked at registry
load, and mismatches are a startup error.

### Scopes

A capability is meaningless without a scope. Scopes are declared in `config/policy.yaml`:

```yaml
scopes:
  projects:
    roots:
      - { path: "C:/Projects", mode: rw, exclude: ["**/node_modules/**", "**/target/**", "**/.git/objects/**"] }
  notes:
    roots:
      - { path: "C:/Users/qhukz/Documents/ObsidianNotes",      mode: rw }
      - { path: "C:/Users/qhukz/Documents/AI/ML Learning",     mode: rw }
      - { path: "C:/Users/qhukz/Documents/MLAI NOTES/ML/AI",   mode: ro }
  scratch:
    roots: [ { path: "D:/ORACLE/scratch", mode: rw } ]

  # Everything not listed is invisible. There is no implicit read of C:\Users\**.
  deny_always:
    - "C:/Windows/**"
    - "C:/Program Files/**"
    - "**/.git/hooks/**"        # writing a hook is arbitrary code execution on next commit
    - "**/.ssh/**"
    - "**/*.env"                # readable only through the secrets path, never as a file
    - "**/AppData/**"
```

`deny_always` wins over any allow. It is not overridable from the UI — only by editing this file by
hand, which is a deliberate speed bump.

### Risk tiers

The tier decides the path an action takes. Tier is a function of `(tool, resolved arguments, scope,
taint)` — **not** of the tool alone. `write_file` into `scratch` is not the same act as `write_file`
into a project.

| Tier | Meaning | Path | Examples |
|---|---|---|---|
| **T0** | No side effect, in scope | auto, logged | `git_status`, `read_file`, `search_*`, `get_processes`, `screenshot` |
| **T1** | Reversible local write, in scope | auto, logged, **undo journalled** | `write_file` (backed up), `git_add`, `git_commit`, `run_tests`, `npm_install` in a project |
| **T2** | Externally visible or expensive to reverse | **confirm** | `git_push`, `delegate` to a cloud agent, `docker run`, any `net.egress`, `open_application` for an unknown binary |
| **T3** | Destructive or wide-blast | **confirm_strong** (typed phrase, desktop only, 10 s cool-down) | recursive delete, `git push --force`, branch delete, `docker system prune`, mass rename, software install |
| **T4** | Never | **deny**, not offerable | anything under `deny_always`, `rm -rf` at a scope root, disabling ORACLE's own policy/audit, writing git hooks, `sys.settings` writes |

Rationale for the T1 band: `git_commit` is technically a mutation but is trivially reversible and is
the single most common useful action. Gating it behind a prompt would produce prompt fatigue and buy
nothing. Instead it is automatic, logged, and undoable. Conversely `git_push` is T2 not because it is
destructive but because **it is visible to other people and cannot be un-published**.

### Taint escalation

If the turn is tainted (§6), every tier above T0 is bumped one level: T1→T2, T2→T3. A plan built from
content ORACLE just read out of an untrusted file does not get to auto-write files.

---

## 4. Path safety (Windows-specific)

Path handling is where filesystem sandboxes actually break, and Windows has more ways to break than
POSIX. The resolution algorithm, applied to **every** path argument before policy evaluation:

```
1  reject if it contains a NUL byte or a wildcard the caller did not declare
2  reject UNC (\\server\share) and device paths (\\.\, \\?\) outright
3  reject alternate data streams — any ':' after the drive letter  (file.txt:evil)
4  expand environment strings? NO — refuse paths containing '%' or '$'
5  expand 8.3 short names to long form   (PROGRA~1 → "Program Files")
6  make absolute against the step's pinned working directory
7  normalise separators, collapse '.' and '..' LEXICALLY first
8  resolve reparse points (symlinks, junctions, mount points) to a final real path
9  re-check 7 on the resolved result   ← a symlink can point back out
10 case-insensitive prefix match against allowed roots; longest match wins
11 apply deny_always; deny wins
12 re-resolve immediately before execution and compare — mismatch aborts (TOCTOU)
```

Step 9 is the one people forget: `C:/Projects/x/link` may lexically look contained while resolving to
`C:/Windows/System32`. Step 12 closes the gap between "approved" and "executed".

`EXPERIMENT NEEDED`: verify that Python's `os.path.realpath` on Windows 10 fully resolves junctions
and mount points, not just symlinks. If it does not, use `GetFinalPathNameByHandleW` via `ctypes`.

## 4b. Command safety

- **No shell, ever.** `subprocess` is called with an argv list; `shell=True` is banned repo-wide and
  enforced by a lint rule and a security test.
- `execute_command` does not accept a command string. It accepts `{program, args[], cwd, timeout}`
  where `program` must resolve to an entry in the program allowlist:

  ```yaml
  programs:
    git:    { path: "C:/Program Files/Git/cmd/git.exe", subcommands: { allow: [status, diff, log, add, commit, branch, checkout, worktree, stash], confirm: [push, reset, clean], deny: [filter-branch, "push --force"] } }
    npm:    { subcommands: { allow: [test, run, ci, install], deny: [publish] } }
    python: { allow_args_matching: ["-m pytest*", "-m venv*"] }
    docker: { subcommands: { allow: [ps, logs, compose], confirm: [run, build, stop], deny: [system prune, rmi] } }
  ```

- The program is resolved to an **absolute path once at startup** and pinned. Never rely on `PATH`
  at call time — `PATH` is attacker-influenceable and `git.exe` in a project directory is a real
  Windows attack (current-directory search order).
- The interactive PTY (`terminal`) is a **separate capability from `proc.spawn`**. The agent may
  *read* a PTY's output; writing into a human's PTY session is T2 and confirmed every time. An agent
  that can type into your shell has all your permissions regardless of any allowlist.
- Environment is **scrubbed by default**: the child gets a minimal constructed environment, not
  `os.environ`. Secrets are injected only for tools that declare `secret.read` and only the specific
  named secrets.

---

## 5. Confirmation and approvals

An approval is a **cryptographically bound, single-use, expiring grant for one specific invocation**:

```json
{ "approval_id": "ap_01J…", "trace_id": "tr_9f2…",
  "tool": "execute_command",
  "arg_hash": "sha256:4b91…",        // hash of the fully-resolved arguments
  "tier": "T2", "requested_at": "…", "expires_at": "…+300s",
  "reason": "push the fix branch so CI can build it",
  "preview": { "argv": ["git","push","origin","fix/auth"], "cwd": "C:/Projects/Asterim" },
  "nonce": "…", "device": "desktop-01" }
```

Rules:
- The executor recomputes `arg_hash` and refuses if it differs. Approving a plan does **not** approve
  a mutated version of it.
- Approvals expire (default 5 min) and are single-use.
- **T3 requires the desktop** and a typed confirmation phrase; it cannot be approved from the phone.
  A 4-inch screen at a bus stop is not where irreversible decisions get made.
- Scoped remembering is allowed and bounded: "allow `npm test` in Asterim for this session" creates a
  rule keyed on `(tool, arg pattern, project, session)` with an expiry. There is **no** "always allow
  everything" switch.
- Every approval and denial is audited with the rule that fired.

### Emergency stop (HALT)

Reachable from a global hotkey, the tray, the API, and the phone. **Must not require the LLM or the
router** — it is a direct path from the API layer to the runtime.

```
HALT →  1. cancel all agent loops (cancellation tokens)
        2. terminate every tool-host job object → whole process trees die
        3. flip policy to deny-all
        4. persist the reason + a snapshot of what was in flight
        5. require an explicit manual "resume" — never auto-recover
```

---

## 6. Prompt injection and taint tracking

This is the control most agent designs lack, so it is specified concretely.

**Every piece of content entering the context carries provenance:**

| Provenance | Trust | Examples |
|---|---|---|
| `user` | trusted | what I typed |
| `system` | trusted | ORACLE's own prompts, policy summaries |
| `local_owned` | semi | my own notes and code, in a declared scope |
| `local_foreign` | untrusted | `node_modules`, vendored code, downloaded PDFs, dependency READMEs |
| `external` | untrusted | web pages, external agent output, HTTP responses |

Rules:

1. Untrusted content is rendered into the prompt inside an explicit, delimited data block, labelled
   as data, never interpolated into the instruction region.
2. Ingesting any `local_foreign` or `external` content sets `turn.tainted = true`. Taint is sticky
   for the turn and is inherited by any plan produced from it.
3. Tainted turns get **tier escalation** (§3) and lose access to `net.egress` and `agent.delegate`
   without confirmation.
4. The confirmation card **shows provenance**: "this action was proposed after reading
   `node_modules/foo/README.md`" is exactly the signal a human needs to say no.
5. Retrieved content can never name a tool that gets auto-executed. The planner only selects tools
   from the registry; a string in a document that looks like a tool call is inert text.

`ASSUMPTION`: taint escalation will be annoying often enough to matter but not so often that it gets
disabled. This needs real-usage tuning — track the escalation rate as a metric from Phase 5 onward.

---

## 7. Secrets and egress

**Storage.** Secrets live in Windows Credential Manager via `keyring` (DPAPI-backed, tied to the user
account). Never in `.env` committed anywhere, never in the DB, never in a prompt. The agent can
reference a secret by *name* (`anthropic_api_key`) and never sees the value; the toolhost receives it
only for tools declaring `secret.read`.

**Redaction.** A single redaction sink is applied to: all logs, all events, all prompt renders, and
every outbound payload. Detection combines high-precision regexes (`sk-ant-`, `ghp_`, `AKIA`,
PEM blocks, JWTs, connection strings) with a Shannon-entropy heuristic for long opaque tokens near
key-like identifiers. Redaction failures are logged loudly, not silently swallowed.

**Egress control.** Anything leaving the machine is enumerated:

```yaml
egress:
  allow:
    - { host: "api.anthropic.com", why: "Claude delegation" }
    - { host: "127.0.0.1",         why: "local Ollama" }
  default: deny
```

**Egress preview** is the local-first feature that makes cloud delegation acceptable: before any
Handoff Packet leaves, the UI shows the exact rendered payload — file list, byte count, token
estimate, the full text — with the redactions visibly marked. Approve, edit, or cancel. See
[INTEGRATIONS.md](INTEGRATIONS.md#egress-preview).

---

## 8. Network and device authentication

Default posture: **bind to `127.0.0.1` only.** LAN exposure is opt-in per launch, never the default.

When LAN is enabled:

- **TLS always**, with a self-signed cert generated on first run and stored with a stable key.
- **Pairing** — the desktop shows a QR containing `{host, port, spki_sha256, pairing_code, expires}`.
  The pairing code is 8 digits, valid 120 s, single use, rate-limited to 5 attempts.
- The phone pins the SPKI fingerprint on first pair (TOFU) and refuses a changed cert afterwards.
- Pairing yields a **device token**: 32 random bytes, stored server-side as an Argon2id hash, sent as
  a bearer token, revocable individually from the Devices screen.
- Devices carry a **capability profile**: a phone may read, chat, cancel, and approve T2 — but never
  T3, and never `input.synth`.
- Discovery via mDNS (`_oracle._tcp.local`) is a convenience; it advertises presence, never secrets.
- Rate limits on auth endpoints; failed pairings are audited.

**Never port-forward.** Access from outside the LAN, if it ever happens, goes over Tailscale/WireGuard
(Post-MVP, [MOBILE.md](MOBILE.md#remote-access--post-mvp)). Exposing this API to the internet is a T4-class idea.

---

## 9. Audit log

Separate from application logs, in `logs/audit/YYYY-MM.jsonl`, append-only, **hash-chained**:

```json
{ "seq": 8814, "ts": "…", "prev": "sha256:aa31…", "hash": "sha256:71f0…",
  "actor": "agent", "trace_id": "tr_9f2…", "device": "desktop-01",
  "tool": "execute_command", "args_digest": "sha256:4b91…",
  "tier": "T2", "decision": "allow", "rule": "programs.git.subcommands.confirm",
  "approval_id": "ap_01J…", "outcome": "ok", "duration_ms": 412 }
```

Each record's `hash` covers `prev` + the record body, so removing or editing an entry breaks the
chain and `oracle audit verify` detects it. Arguments are stored as digests plus a redacted preview,
never raw — the audit log must not become the place secrets end up.

Audited: every policy decision (including denials), every approval, every HALT, every pairing, every
egress, every policy reload, every secret access by name.

---

## 10. Security checklist for implementers

Every PR touching `packages/policy`, `packages/toolhost`, or `packages/api` must satisfy:

- [ ] No `shell=True`, no `os.system`, no string-built commands.
- [ ] Every new path argument goes through the canonicaliser; a test proves traversal is blocked.
- [ ] Every new tool declares capabilities, a risk tier, and `reversible: true|false`.
- [ ] New tiers ≥ T2 have a confirmation preview that renders the *actual* arguments.
- [ ] Redaction covers any new output sink.
- [ ] Denials are logged with the rule that fired.
- [ ] `tests/security/` gains a case for the new surface.

The red-team suite in `tests/security/` (Phase 2 onward) includes: traversal strings, symlink escapes,
ADS paths, 8.3 names, injected instructions in Markdown/README/PDF fixtures, approval replay,
mutated-argument execution, and a fixture repo whose README tries to talk the agent into `git push`.
