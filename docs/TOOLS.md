# ORACLE — Tool System

The tool system is the agent's only route to the world. Its design goal is not "expose everything" —
it is **to make the set of possible actions small, precisely described, and individually judgeable.**

## 1. Design rules

### Rule 1 — Prefer intent-shaped tools over primitives

The tool sketch in the original brief listed `execute_command` alongside `git_status`, `npm`,
`python`, `terminal`. That set collapses under its own weight: if the model can reach `execute_command`,
every narrower tool is decorative and the policy engine is reduced to guessing about shell strings.

So: **specific tools are the interface; general execution is a gated exception.**

| Instead of | Provide | Because |
|---|---|---|
| `execute_command("git commit -m …")` | `git.commit(project, message)` | precise schema, precise tier (T1), precise undo (`git reset --soft`) |
| `execute_command("npm test")` | `dev.run_tests(project, filter?)` | structured results, not scraped stdout; a known timeout |
| `execute_command("rm -rf x")` | `fs.delete(path, recursive)` | goes through the canonicaliser and the trash journal |

Every tool the model can call is a promise about *what can happen*. Shell strings are not a promise.

### Rule 2 — Fewer tools than you think

Tool schemas consume the router model's context every turn (band 2 in
[AGENT_RUNTIME.md](AGENT_RUNTIME.md#5-context-budget)) and every additional near-duplicate tool
measurably degrades selection accuracy in small models. Target: **≤ 40 tools total**, with 5–8
presented per turn after intent filtering. A new tool must justify itself against merging into an
existing one with an extra parameter.

### Rule 3 — Declare reversibility, and prefer undo over prompting

A tool that can be undone should run automatically and journal its undo, rather than prompting.
Prompt fatigue is a security failure ([SECURITY.md](SECURITY.md#2-design-principles)).

### Rule 4 — Structured results, not scraped text

Tools return typed results. `dev.run_tests` returns counts, failures and durations — not a blob of
stdout for the model to misread. Raw output is still captured to a blob and linked, for the human.

### Rule 5 — No hidden state

A tool never depends on a previous tool's side effect on shared mutable state (a `cd`, an env var, a
"currently selected project"). Every call carries its own scope. This is what makes replay testing
possible.

---

## 2. Tool contract

Every tool is declared once, as a pydantic model plus metadata. The registry validates all of this at
startup; a contract error is a boot failure, not a runtime surprise.

```python
@tool(
    id="git.commit",
    summary=(
        "Record the staged changes as a commit. Requires a message. Undo puts the "
        "changes back in the staging area."
    ),
    args=GitCommitArgs,
    result=GitCommitResult,
    capabilities={Capability.FS_READ, Capability.GIT_WRITE, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T1,
    reversible=True,
    undo="git reset --soft HEAD~1",
    intents={"modify", "run"},          # controls context-budget pre-filtering
    side_effects="Creates a commit. Nothing leaves the machine — that is `git.push`.",
    path_fields={"path"},               # canonicalised before the gate
    programs={"git"},                   # pinned by the PARENT from the allowlist
)
async def git_commit(*, ctx: ToolContext, args: GitCommitArgs) -> GitCommitResult:
    ...
```

Notes that matter:

- **`ScopedPath` is a marker, not magic.** The resolution happens in the executor, driven by
  `path_fields`; the handler receives an already-canonicalised `Path` in its context. A path argument
  that is not listed in `path_fields` bypasses the canonicaliser, which is a review rejection.
- **`programs` names allowlist keys, never paths.** The parent pins each to an absolute path and
  hands it over. A handler that resolved its own program would defeat the pin, and a security test
  greps for exactly that.
- `intents` drives which tools even appear in the model's context.
- `dry_run=True` means the tool can compute and return its effect without performing it. Every T3
  tool must support this, so the confirmation card can show a real preview.
- `undo` is a *recipe*, executed by the undo journal, never by the model. Where reversing it needs a
  process (`git reset --soft`), the journal dispatches it back through the gate as a **hidden** tool
  rather than running it in the parent.

### What the summary is for  `MEASURED 2026-08-21`

The summary is not documentation — it is **the entire basis on which the model chooses**. Rewriting
four summaries to *distinguish* neighbouring tools rather than describe them, plus few-shot examples,
took selection accuracy from **83.3% to 100%** on the eval set. Before that, "commit my changes"
selected `git.add`, staged, and reported success.

So: write the summary against its nearest neighbour. `git.add` says "Does NOT create a commit";
`git.status` says "Does not show the changes themselves". Measured by
[`scripts/eval_selection.py`](../scripts/eval_selection.py).

### Execution envelope

```python
class Invocation(BaseModel):          # src/oracle/toolhost/protocol.py
    id: str
    tool: str
    args: dict[str, Any]              # already validated against the contract
    resolved: dict[str, str]          # absolute, canonicalised paths
    programs: dict[str, str]          # absolute, pinned from the allowlist
    cwd: str | None
    timeout_s: int
    dry_run: bool
```

The toolhost receives this and nothing else. It cannot look up policy, cannot read secrets it was not
handed, and cannot widen a scope.

Two differences from the original sketch, both from building it:

- **No `scope` and no `approval` cross the boundary.** They were in the sketch, and neither is
  needed: by the time an invocation exists, the scope check has already happened and the approval has
  already been spent. Sending them would give the child something to reason about, and the whole
  point is that it has nothing to reason about.
- **`resolved` and `programs` are the interesting fields.** Everything the call may touch is an
  absolute path decided on the parent side. The child never canonicalises a path and never looks up a
  program — doing either would move the sandbox decision to the wrong side of the pipe.

---

## 3. The catalogue

Tiers are *baseline* — the effective tier is computed from resolved arguments and taint
([SECURITY.md](SECURITY.md#risk-tiers)). Phase refers to [ROADMAP.md](ROADMAP.md).

### `sys.*` — system awareness

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `sys.info` | T0 | 3 | **built.** CPU/RAM/disk snapshot; CPU load is sampled over 120 ms, not faked |
| `sys.processes` | T0 | 3 | **built.** Filtered list; no full command lines (they leak secrets). Declares `proc.spawn` — it runs `tasklist` — so it is absent from a read-only build |
| `sys.screenshot` | T0 | 3 | **image, not OCR.** Written to a blob; never auto-fed to a cloud agent |
| `sys.notify` | T0 | 3 | native toast |

### `app.*` — applications

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `app.launch` | T1/T2 | 3 | **built.** Tier comes from the catalogue entry: `explorer` is T1, `browser` is T2 because it opens the network. **Runs in the parent and launches detached** — [ADR-0018](DECISIONS.md#adr-0018--a-launched-application-is-not-a-tool-call) |
| `app.close` | T2 | later | T2 always: unsaved work is real work. Not built — closing a window is the user's job, and `app.launch` deliberately keeps no control over what it started |
| `app.list_running` | T0 | later | `sys.processes` covers this today |

`app.launch` takes an **alias**, not a path — `{"app": "obsidian"}`, resolved through
`config/apps.yaml`. The model never supplies an executable path.

### `fs.*` — files

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `fs.read` | T0 | 2 | size-capped; binary refused; returns text + encoding |
| `fs.list` | T0 | 2 | respects excludes; never recurses into `node_modules`/`target` unasked |
| `fs.write` | T1 | 2 | **backs up the previous version to the trash journal first** |
| `fs.patch` | T1 | 2 | apply a unified diff; preferred over `write` — smaller, reviewable, safer |
| `fs.move` | T1 | 3 | **built.** Both paths are canonicalised; refuses to overwrite |
| `fs.delete` | T3 | 3 | **built.** → trash, never a real delete |
| `fs.open_in_os` | — | — | **dropped.** "Hand a path to the shell's default handler" is `ShellExecute`: what it starts is the set of file associations on the machine, which no contract can promise. `app.launch` with an alias is the bounded version |

`fs.trash` is not a tool — it is the *implementation* of delete. A genuine unrecoverable delete is T4
and simply absent from the catalogue. If I want that, I use Explorer.

### `git.*` — version control

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `git.status` | T0 | 3 | structured: branch, ahead/behind, staged/unstaged/untracked |
| `git.diff` | T0 | 3 | capped; large diffs go to a blob and return a summary + stat |
| `git.log` | T0 | 3 | |
| `git.add` | T1 | 3 | |
| `git.commit` | T1 | 3 | undo: `reset --soft HEAD~1` |
| `git.branch` | T1 | 3 | create/list/switch |
| `git.stash` | T1 | 3 | |
| `git.worktree` | T1 | 6 | the isolation primitive for delegated agents |
| `git.push` | **T2** | 3 | **built.** Visible to others, cannot be unpublished — and therefore declared **not reversible**, so it can never slide to T1 and stop asking |
| `git.undo` | T1 | 3 | **built, hidden.** The recipe the undo journal executes. Registered so the child can dispatch it, never offered to the model: an agent that could undo at will could erase work it was asked to do |
| `git.reset_hard` / `git.clean` | **T3** | later | tiered in policy, not built. Nothing needs them yet, and the trash covers the recoverable cases |
| `git.push --force` | **T4** | — | not exposed. Deliberate, and also denied at the argv level: `push --force` is on the program allowlist's deny list, matched however it is spelled |

### `dev.*` — development

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `dev.run_tests` | T1 | 3 | **built.** Detects pytest/vitest/jest/cargo from marker files; asks each for a machine-readable report and **labels the one case where it scrapes** |
| `dev.build` | T1 | 3 | **built.** Declares `proc.spawn`, not `fs.write`: what a build writes is not a path this contract can name |
| `dev.install_deps` | T2 | later | network + arbitrary postinstall scripts. Not built; `dev.execute` covers it under confirmation |
| `dev.lint` | T1 | 3 | **built** |
| `dev.run_script` | — | later | subsumed by detection: `dev.build`/`dev.lint` already run only what `package.json` declares |
| `dev.docker` | T1–T3 | 7 | `ps`/`logs` T0–T1, `run`/`build` T2, `prune`/`rmi` T3 |
| `dev.execute` | **T2** | 3 | **built.** The gated escape hatch — allowlisted program + argv, never a shell string. The only tool whose argv the model chooses, and therefore the only one the subcommand rules inspect |

### `term.*` — terminal

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `term.open` | T1 | 3 | **built.** ConPTY via `pywinpty`. Lives in the toolhost, so a runaway shell dies with HALT. Waits for a *measured* readiness condition — input sent before the shell is reading is swallowed silently ([OQ-09](OPEN_QUESTIONS.md#oq-09)) |
| `term.read` | T0 | 3 | **built.** A reader thread drains the PTY continuously; ANSI is stripped for the model and kept for the UI |
| `term.write` | **T2, always confirmed** | 3 | **built.** Typing into a live shell = full user privilege. Declares its own `term.write` capability, **not** `proc.spawn`: a spawn is an argv the allowlist can inspect, and a line of shell input is not. One line per call, so an approval cannot cover a script |
| `term.close` | T1 | 3 | **built** |

### `know.*` — knowledge

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `know.search` | T0 | 5 | hybrid search; `collection` + `project` filters |
| `know.search_code` | T0 | 5 | symbol-aware |
| `know.read_context` | T0 | 5 | assemble context for a topic, with citations |
| `know.summarize` | T0 | 5 | uses the local model |
| `know.reindex` | T1 | 5 | scoped; a full reindex is explicit |

### `ai.*` — agent delegation

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `ai.build_packet` | T0 | 6 | assemble a Handoff Packet — **no egress**, purely local |
| `ai.delegate` | **T2** | 6 | send to Claude/Antigravity. Egress preview mandatory |
| `ai.monitor` | T0 | 6 | poll a running delegation |
| `ai.collect` | T1 | 6 | diff the worktree, run tests, summarise |
| `ai.cancel` | T1 | 6 | |

Splitting `build_packet` from `delegate` is deliberate: it makes "show me what you *would* send"
a free, safe, always-available action, and it is what makes the egress preview meaningful rather
than a rubber stamp.

### `pipe.*` — pipelines

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `pipe.list` / `pipe.describe` | T0 | 7 | |
| `pipe.run` | inherited | 7 | **tier = max tier of its steps**, computed at validation |
| `pipe.cancel` | T1 | 7 | |

### `oracle.*` — self

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `oracle.status` | T0 | 1 | agent state, loaded model, queue depth |
| `oracle.memory_write` | T1 | 5 | record a durable fact |
| `oracle.task_list` | T0 | 1 | |
| `oracle.halt` | T0 | 2 | anyone can always stop it |
| `oracle.set_policy` | **T4** | — | **not exposed.** Policy changes are a human editing a file. |

### Deliberately absent

| Not a tool | Why |
|---|---|
| `keyboard` / `mouse` synthesis | Full user impersonation with no meaningful scope: a synthetic keystroke can do anything I can do, and no policy can inspect it. Deferred to Post-MVP behind an explicit capability, and only for a named-window allowlist. |
| `registry.write`, `sys.settings` | T4. The blast radius is the OS. |
| `net.fetch` (arbitrary HTTP) | Post-MVP, and only with an egress allowlist. An agent that can GET a URL and read the result into context is a prompt-injection funnel ([SECURITY.md](SECURITY.md#6-prompt-injection-and-taint-tracking)). |
| `install_software` | Confirmation cannot make this safe enough to be worth it. I install software myself. |
| `sql.query` against project DBs | No scoping story yet. Revisit when there is one. |

---

## 4. MVP tool set  `SHIPPED 2026-08-21`

**27 registered, 26 offerable** (`git.undo` is hidden). Still well under the cap of 40, which is the
point — see rule 2.

```
fs.read  fs.list  fs.stat  fs.write  fs.patch  fs.move  fs.delete
git.status  git.diff  git.log  git.add  git.commit  git.branch  git.stash  git.push  [git.undo]
dev.run_tests  dev.build  dev.lint  dev.execute
term.open  term.read  term.write  term.close
app.launch  sys.info  sys.processes
```

Two things the original sketch got wrong, both found by building it:

- **`push` and `delete` shipped after all.** They were deferred to "the phase that makes them
  meaningful" — but the phase that makes a commit meaningful is the same one that makes pushing it
  meaningful. They arrived *with* their tiers and their security tests, which was the actual
  requirement all along.
- **A hidden tool is a category the contract needed.** `git.undo` must exist in the registry (the
  toolhost dispatches by id) and must never be selectable. `hidden=True` is enforced: a hidden
  contract may declare no intents and never appears in `for_intent`.

**Only 11 of these are reachable from a routed turn.** Selection offers a tool only when its
arguments can be built from *(resolved project, one model-supplied string)*. `dev.execute` needs an
argv, `term.write` needs a command, `fs.write` needs file content, `git.push` needs a remote —
half-filling any of them would mean inventing something. They stay callable by a human through the
API, and by a plan once plans exist.

Notably still absent: delegation, knowledge, pipelines.

---

## 5. Adding a tool — the checklist

1. Does an existing tool cover this with one more parameter? If yes, stop.
2. Write the contract: capabilities, scopes, tier, `reversible`, `undo`, `timeout_s`, `dry_run`.
3. Use resolved types (`ProjectRef`, `ScopedPath`), never bare `str` for paths or programs.
4. Return a **typed result**; send raw output to a blob.
5. Add policy rules; add a `tests/security/` case if it touches paths, processes, or the network.
6. Add a golden test for how the tool is described to the model — description drift silently degrades
   selection accuracy, and this is the only way to catch it.
7. Register it under the right `intents` so the context filter can find it.
