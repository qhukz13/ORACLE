# ORACLE — Project State

> **The subsystem that makes "continue Asterim" answerable.**
> Design, 2026-08-26. Nothing here is built. Scheduled as
> [Phase 12](ROADMAP.md#phase-12--project-state--the-continue-loop--residency-arc); the decision is
> [ADR-0024](DECISIONS.md#adr-0024--a-project-is-a-first-class-persistent-entity).

---

## 1. The problem, stated precisely

A project today is **a directory name**. `core/projects.py` lists the top-level directories under
the projects root, classifies each by marker file (`pyproject.toml`, `package.json`, `Cargo.toml`,
`default.project.json`), and derives the argv for test/build/lint. The list is passed to the intent
classifier so that a hallucinated project name resolves to nothing rather than to a filesystem path.

That is a good safety mechanism and it stays. It is also the entire model.

**What it cannot answer**, and what every sentence in [VISION.md §2](VISION.md#2-the-day--the-acceptance-test)
depends on:

| Question | Today |
|---|---|
| What *is* Asterim? | A directory that contains a `package.json` |
| What did we do to it last? | Nothing recorded — tasks carry a project string, but nothing indexes by it |
| What is unfinished? | Unanswerable |
| What did it cost? | Per-task, never per-project |
| Is it healthy? | `git status` on demand, remembered by nobody |
| What changed since I last looked? | Unanswerable — there is no "last looked" |

`memory_facts` and `memory_attempts` are already scoped by project
([`memory/models.py`](../src/oracle/memory/models.py) `FactScope.PROJECT`), and `TaskSpec` carries a
project. So the *keys* exist. **There is no entity they are keys to.**

---

## 2. The distinction that makes this design work

There are two kinds of project state, and conflating them is the failure mode this document exists
to avoid.

| | **Observed state** | **Relational state** |
|---|---|---|
| Examples | branch, ahead/behind, dirty files, test command, last commit, file count | what ORACLE attempted, what it left unfinished, what it learned, what it cost, when I last looked |
| Source of truth | **git and the filesystem** | **ORACLE, and nothing else** |
| Storage | **never stored** — read on demand | **must be stored** — nothing else holds it |
| If it is wrong | it cannot be; it is read fresh | it is lost |

**Observed state must not be persisted.** A cached branch name is a lie waiting to be read: I switch
branches in my editor and ORACLE's sidebar is wrong, silently, with no event to correct it. `git
status` on a warm repository is single-digit milliseconds; caching it buys nothing and costs
correctness. This is the same reasoning that put the event log — not a projection — at the centre of
the runtime ([ADR-0010](DECISIONS.md#adr-0010--event-sourced-runtime)).

**Relational state must be persisted**, because it is ORACLE's own memory of the relationship and
there is no second copy anywhere. This is the table.

The rule, stated once:

> **If git knows it, do not store it. If only ORACLE knows it, store it — the row is the record.**

---

## 3. The model

```python
class ProjectStatus(StrEnum):
    ACTIVE    = "active"      # touched by ORACLE or by a commit inside the attention window
    IDLE      = "idle"        # known, nothing recent
    ARCHIVED  = "archived"    # explicitly set aside by a human; hidden by default
    MISSING   = "missing"     # registered, root no longer on disk


class Project(BaseModel):
    """The durable half. Everything git can answer is deliberately absent."""

    id: str                          # "pj_..." - stable across renames of the directory
    name: str                        # the resolved name the classifier matches against
    root: Path
    status: ProjectStatus

    #: One or two lines, human- or ORACLE-authored, shown in the briefing and given to
    #: the planner. Authored once and corrected, never regenerated per turn.
    description: str = ""

    first_seen: datetime
    #: The last time ORACLE itself did something here. NOT the last commit - that is
    #: observed state and is read from git.
    last_touched: datetime | None = None
    #: Where the briefing resumes from. See section 6.
    briefed_through_seq: int = 0

    #: Denormalised counters, rebuildable from `tasks`. Present because the briefing has
    #: a 3-5 second budget and must not aggregate the task table per project per render.
    open_tasks: int = 0
    failed_tasks: int = 0
    tokens_spent: int = 0
    usd_spent: float = 0.0
```

`id` is stable and `name` is not: renaming `Asterim/` on disk must not orphan every fact and attempt
recorded against it. Identity is the row; the name is a label that can be re-pointed by a human.

### Storage

A `projects` table in `oracle.db` (migration `0005`), plus an index on `tasks(project, status)`
which does not exist today and is what makes the counters rebuildable cheaply.

**The counters are a projection, not a source.** They are rebuilt from `tasks` on demand and on
boot, exactly as the `tasks` table is itself a projection the event log can rebuild. A counter that
disagrees with the task table is a bug in the projection, and the repair is to recompute — never to
trust the counter.

### Registration is explicit, discovery is a suggestion

`discover_projects()` keeps doing what it does: it lists directories. What it produces is now a
*candidate list*, and a candidate becomes a `Project` row when **a human registers it** or when
ORACLE first does work in it and records that fact.

This matters for a reason that is not obvious: the projects root contains `Kaggle`, `docs.zip`,
`New folder` and `model-testing`. Auto-registering every directory would fill the briefing with
things I do not consider projects, and the briefing's whole value is that it is short.

---

## 4. Observed state: the reader

One function, no storage, called when a project is displayed or when its state enters a prompt.

```python
@dataclass(frozen=True)
class ProjectObservation:
    """Read fresh, every time. Never persisted, never cached across a turn boundary."""
    branch: str | None
    ahead: int
    behind: int
    dirty: int                       # modified + staged + untracked, counted not listed
    last_commit: tuple[str, str, datetime] | None   # sha, subject, when
    detected: ProjectInfo            # existing core/projects.py output
    agent_docs: tuple[str, ...]      # AGENTS.md / CLAUDE.md present - read separately, tainted
    error: str | None = None         # not a repo, unreadable, root missing
```

Three constraints on it:

1. **It goes through the tool layer**, not around it. `git.status`, `git.log`, `fs.stat` are already
   contracts behind the policy gate; a reader that shells out directly would be a second execution
   path and would violate the one rule the architecture defends hardest
   ([ADR-0003](DECISIONS.md#adr-0003--tool-execution-in-a-separate-process)). Every one of these is
   T0 — no side effect, in scope — so the cost is a process hop, not an approval.
2. **`error` is a field, not an exception.** A project whose root was deleted, or which was never a
   git repository, must render as a row that says so. `MISSING` in the sidebar is information; a
   crashed sidebar is not. This is the same lesson as the dead collection root that took the whole
   RAG watcher down with one absent path.
3. **It has a budget.** Observing N projects for the briefing is N git calls. `EXPERIMENT NEEDED` —
   measure the fan-out at the real project count (13 directories today) against the 3–5 second
   glance budget, and if it misses, observe lazily per row rather than caching the result.

---

## 5. Unfinished work — where "continue" gets its list

This is the part most likely to be got wrong, so it is stated as a hierarchy with reasons.

**Primary source: ORACLE's own task graph.** Tasks in this project whose status is not terminal, or
which ended `FAILED` / `TIMEOUT` without a superseding attempt. ORACLE recorded them, ORACLE owns
them, and they carry evidence, cost and lineage. This is authoritative and it is the only source
that is.

**Secondary source: what the repository says about itself.** `docs/current_task.md`, `TODO.md`,
`ROADMAP.md`, an open issue list — whatever the project's own convention is. This is
**`local_foreign` content**: it is authored by whoever wrote the repository, it is given to the
planner as *evidence to consider*, it taints the turn, and it never becomes an instruction
([SECURITY.md §6](SECURITY.md#6-prompt-injection-and-taint-tracking)). ORACLE already treats `AGENTS.md` and
`CLAUDE.md` this way in `read_agent_docs()`; this extends the same handling to task documents.

> A project that says "next: delete the production database" in its TODO is describing itself, not
> commanding ORACLE. The taint escalation and the approval gate are what make reading it safe, and
> they are why this content can be read at all.

**Never a source: the planner's imagination.** A planner handed a project name and no state will
produce plausible work, and plausible work is worse than none — it is unfalsifiable and it burns a
worktree to find out. If both sources above are empty, the correct answer to "continue Asterim" is a
question, not a plan.

### The `continue` intent

`IntentLabel` today is `run · investigate · question · status · search · modify · delegate ·
pipeline · chat · control`. There is no `continue`, and the vision's headline utterance therefore
routes to `chat` or `modify` with low confidence.

Adding it costs one label and a handful of fixtures, but note what it changes: `continue` is the
first intent whose *object is a project rather than a request*. It resolves to "read this project's
state and decide", which is a planning call, not a tool call. The router stays a router — it does
not decide the work, it decides that the work is unknown and must be planned.

`EXPERIMENT NEEDED` — a new label is a change to a measured surface. Intent accuracy is 93.3% on a
30-case fixture set; adding a label requires re-running that eval, not assuming it holds. The risk
is specifically confusion with `run` and `modify`.

---

## 6. The briefing — "what happened while I was away"

`briefed_through_seq` is the whole mechanism. The event log's `seq` is global and gap-free, and
clients already resume from `since_seq`. The briefing is the same primitive pointed at a person
instead of a socket:

```
briefing = summarise(events where seq > briefed_through_seq, grouped by project)
```

- **It is per-project**, because "what happened" is only meaningful scoped to a thing.
- **It advances only when acknowledged**, never on render. If I glance at the screen and walk away,
  the briefing must still be there when I come back. A briefing that clears itself on sight is a
  notification, and notifications are how people miss things.
- **It is bounded.** Away for a week, the briefing is not 40,000 events. It is: what completed, what
  failed, what is waiting, what it cost — and a link into the timeline for the rest.
- **Its summariser is local.** This is precisely the tier-2 workload described in
  [VISION.md §3](VISION.md#3-who-does-what): summarisation of content that never needs to leave the
  machine. Until that tier exists, the briefing is a **deterministic template over the counters** —
  which is honest, fast, free, and testable, and which should probably remain the fallback forever.

---

## 7. Security posture

Project state is derived from content ORACLE does not control, so it is a taint surface and is
treated as one.

| Input | Trust | Handling |
|---|---|---|
| Directory names | `local_foreign` | Already validated: the classifier may only resolve a name that is in the registry. A `..` in a project name cannot walk out of the projects root — enforced today in `api/app.py` |
| `git` output | `local_foreign` | Parsed into typed fields, never interpolated into a prompt as raw text |
| `AGENTS.md`, `TODO.md`, `current_task.md` | `local_foreign` | Read as data, attributed to the project, taints the turn, escalates the confirmation tier by one |
| `description` | `local_trusted` if a human wrote it; `local_foreign` if ORACLE derived it from repo content | Provenance is stored with the field. A description derived from a README carries the README's taint |

**The scope rule does not move.** Registering a project does not widen a filesystem scope. Policy
scopes stay in `config/policy.yaml` where a human edits them and git records the edit; a project row
is a *label on work*, never a grant of access. If registering a project could widen a scope, then
"discover projects" would be a privilege escalation with a friendly name.

---

## 8. What this is not

- **Not a second source of truth over git.** §2. If it ever starts caching branch names, that is the
  bug.
- **Not a project management tool.** No boards, no assignees, no due dates, no estimates. The unit
  of work is a task in the graph, and the graph already exists.
- **Not a workspace manager.** It does not open editors, arrange windows, or own the filesystem.
- **Not per-project configuration.** Test and build commands are *detected* from marker files
  because that is the thing that is actually true. A stored command drifts the moment someone
  changes their build.
- **Not automatic.** Registration is a human act, and so is archiving.

---

## 9. Acceptance criteria

The subsystem is done when all of these hold:

- [ ] `projects` table, migration `0005`, and an index on `tasks(project, status)`.
- [ ] Registering, renaming and archiving a project are testable operations that preserve `id`.
- [ ] A project whose root is deleted renders as `MISSING`, and nothing else in the app degrades.
- [ ] `ProjectObservation` reads through the tool layer, and a security test asserts there is no
      direct subprocess path.
- [ ] Counters are rebuildable from `tasks`, and a test proves recompute equals the stored value
      after a graph runs.
- [ ] Unfinished work for a project is derived from the task graph, with repo task documents
      included as tainted evidence and a security test proving they cannot become instructions.
- [ ] `continue` resolves to a planning call against real project state, and the intent eval is
      **re-run**, not assumed.
- [ ] The briefing advances `briefed_through_seq` on acknowledgement only, and a test proves an
      unacknowledged briefing survives a restart.
- [ ] Registering a project widens no policy scope — asserted in `tests/security/`.

---

## 10. Why this is one phase and not part of the UI work

The sidebar mock in [UI.md §4](UI.md#4-sidebar) already draws it:

```
  - Asterim          2 tasks  branch main +3
```

Every number in that line comes from this document. The execution tree, the orbit, the timeline and
the agent queue are all *renderings of state*, and three of the four are currently blocked on the
fact that no state has ever been produced ([current_state.md §11](current_state.md)).

Building the views first produces components that are green against fixtures and dead against the
running app — which has already happened once, to `TaskTree`, whose `after {deps}` line renders a
field the store never populates. **The state comes first, then the run that fills it, then the views
that read it.**
