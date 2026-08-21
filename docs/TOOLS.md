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
    summary="Commit staged changes in a project.",
    capabilities={"fs.write", "proc.spawn", "git.write"},
    scopes={"projects"},
    risk="T1",
    reversible=True,
    undo="git reset --soft HEAD~1",
    timeout_s=30,
    dry_run=True,
    intents={"modify", "run"},          # controls context-budget pre-filtering
    side_effects="Creates a commit in the project's git history.",
)
class GitCommit(ToolArgs):
    project: ProjectRef                 # resolved against the registry, never free text
    message: str = Field(min_length=3, max_length=2000)
    all_tracked: bool = False

class GitCommitResult(ToolResult):
    sha: str
    files_changed: int
    insertions: int
    deletions: int
```

Notes that matter:

- `ProjectRef`, `ScopedPath`, `ProgramRef` are **custom types that resolve and validate**. A plain
  `str` path in a tool signature is a review rejection — it bypasses the canonicaliser.
- `intents` drives which tools even appear in the model's context.
- `dry_run=True` means the tool can compute and return its effect without performing it. Every T3
  tool must support this, so the confirmation card can show a real preview (an actual file list, an
  actual diff) rather than a description.
- `undo` is a *recipe*, executed by the toolhost's undo journal, never by the model.

### Execution envelope

```python
class ToolInvocation(BaseModel):
    invocation_id: str
    trace_id: str
    tool: str
    args: dict            # already validated & resolved
    scope: ResolvedScope  # concrete allowed roots for THIS call
    approval: Approval | None
    timeout_s: int
    dry_run: bool
    cwd: Path             # pinned; the toolhost never inherits a working directory
    env: dict[str, str]   # constructed, not inherited
```

The toolhost receives this and nothing else. It cannot look up policy, cannot read secrets it was not
handed, and cannot widen `scope`.

---

## 3. The catalogue

Tiers are *baseline* — the effective tier is computed from resolved arguments and taint
([SECURITY.md](SECURITY.md#risk-tiers)). Phase refers to [ROADMAP.md](ROADMAP.md).

### `sys.*` — system awareness

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `sys.info` | T0 | 3 | CPU/RAM/GPU/disk snapshot |
| `sys.processes` | T0 | 3 | filtered list; no full command lines by default (they leak secrets) |
| `sys.screenshot` | T0 | 3 | **image, not OCR.** Written to a blob; never auto-fed to a cloud agent |
| `sys.notify` | T0 | 3 | native toast |

### `app.*` — applications

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `app.launch` | T1/T2 | 3 | T1 for allowlisted apps (`code`, `obsidian`, `explorer`); T2 for anything else |
| `app.close` | T2 | 3 | T2 always: unsaved work is real work |
| `app.list_running` | T0 | 3 | |

`app.launch` takes an **alias**, not a path — `{"app": "obsidian"}`, resolved through
`config/apps.yaml`. The model never supplies an executable path.

### `fs.*` — files

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `fs.read` | T0 | 2 | size-capped; binary refused; returns text + encoding |
| `fs.list` | T0 | 2 | respects excludes; never recurses into `node_modules`/`target` unasked |
| `fs.write` | T1 | 2 | **backs up the previous version to the trash journal first** |
| `fs.patch` | T1 | 2 | apply a unified diff; preferred over `write` — smaller, reviewable, safer |
| `fs.move` | T1 | 3 | |
| `fs.delete` | T3 | 3 | → trash journal, never a real delete; `recursive` requires `dry_run` preview |
| `fs.open_in_os` | T1 | 3 | hand a path to the shell's default handler |

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
| `git.push` | **T2** | 3 | visible to others, cannot be unpublished |
| `git.reset_hard` / `git.clean` | **T3** | 3 | destroys uncommitted work; `dry_run` mandatory |
| `git.push --force` | **T4** | — | not exposed. Deliberate. |

### `dev.*` — development

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `dev.run_tests` | T1 | 3 | auto-detects pytest/vitest/jest/cargo; returns structured results |
| `dev.build` | T1 | 3 | |
| `dev.install_deps` | T2 | 3 | network + arbitrary postinstall scripts — T2 is not paranoia |
| `dev.lint` | T1 | 3 | |
| `dev.run_script` | T1/T2 | 3 | only scripts declared in `package.json` / `pyproject.toml`; never arbitrary |
| `dev.docker` | T1–T3 | 7 | `ps`/`logs` T0–T1, `run`/`build` T2, `prune`/`rmi` T3 |
| `dev.execute` | **T2+** | 3 | the gated escape hatch — allowlisted program + argv, never a shell string |

### `term.*` — terminal

| Tool | Tier | Phase | Notes |
|---|---|---|---|
| `term.open` | T1 | 4 | opens a PTY bound to a project; streams to the UI |
| `term.read` | T0 | 4 | agent reads recent output |
| `term.write` | **T2, always confirmed** | 4 | typing into a live human shell = full user privilege. Never auto. |
| `term.close` | T1 | 4 | |

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

## 4. MVP tool set

Phases 1–4 ship **19 tools**, not 40. Enough to be genuinely useful, small enough to keep selection
accuracy high on a 2B model:

```
oracle.status  oracle.task_list  oracle.halt
fs.read  fs.list  fs.write  fs.patch
git.status  git.diff  git.log  git.add  git.commit  git.branch
dev.run_tests  dev.execute
app.launch  sys.info  sys.processes
term.open
```

Notably absent from MVP: delegation, knowledge, pipelines, delete, push. Those arrive with the phases
that make them meaningful — and each one arrives with its policy rules and security tests, never
before them.

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
