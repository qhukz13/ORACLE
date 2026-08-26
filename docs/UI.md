# ORACLE — Interface Specification

> A serious developer tool that happens to look good. Not a film prop.

## 1. Visual philosophy

The reference point is a **mission console**: dark, dense, quiet until something needs attention. The
Jarvis influence is in the *language* — a luminous core, orbiting contexts, status conveyed by light
— not in the literal chrome. Concretely:

| Principle | What it means in practice |
|---|---|
| **Every pixel reports state** | If an element doesn't answer a question I actually have, it's deleted. |
| **Calm by default** | Idle = still and dim. Motion means something happened. An interface that is always animating can't signal anything. |
| **Density over spaciousness** | This is a tool for someone reading logs and diffs, not a landing page. Tight leading, small type, real information per screen. |
| **The centre earns its place** | The orbital view ships in P11 with an explicit test: cover every label and you must still be able to say what ORACLE is doing. If it fails, it gets cut. |
| **Never colour alone** | Every status carries icon + label + colour. Required for accessibility and for glanceability. |
| **The terminal is first-class** | Not a hidden debug panel. It's where trust is built: I can see the actual commands. |

### Anti-goals

Sci-fi typography that hurts legibility · animated backgrounds · gratuitous glow on text · fake
telemetry · a spinning globe · progress bars that don't map to real progress.

---

## 2. Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ⌘ search / ask…                    ● executing   Asterim   CPU▁▃▅ RAM 41%  ⏻ │  COMMAND BAR  48px
├───────────────┬──────────────────────────────────────────┬───────────────────┤
│ WORKSPACE     │                                          │ INSPECTOR         │
│               │                                          │                   │
│ ▾ Projects    │            CENTER STAGE                  │  Task #128        │
│   ● Asterim   │                                          │  ─────────────    │
│   ○ SCRAPSHIFT│      ┌──────────────────────────┐        │  status  running  │
│   ○ GameRecs  │      │  Orbit │ Chat │ Timeline │        │  started 03:41    │
│               │      └──────────────────────────┘        │  project Asterim  │
│ ▾ Tasks    3  │                                          │  agent   claude   │
│   ▸ Active  1 │         (view switches here)             │                   │
│   ▸ Waiting 1 │                                          │  Tools used       │
│   ▸ Done     │                                          │   git.status      │
│               │                                          │   know.search     │
│ ▾ Agents      │                                          │                   │
│   ● local     │                                          │  Files changed 3  │
│   ○ claude    │                                          │  [diff] [logs]    │
│               │                                          │                   │
│ ▾ Knowledge   │                                          │                   │
│   Obsidian    │                                          │                   │
│   Documents   │                                          │                   │
├───────────────┴──────────────────────────────────────────┴───────────────────┤
│ TERMINAL │ LOGS │ PROBLEMS 2 │                        ⌃` state ▴▾  ⤢         │  DOCK
│ $ npm test                                                                    │
│ ✓ 41 passing (2.1s)                                                           │
└──────────────────────────────────────────────────────────────────────────────┘
```

| Region | Width/height | Collapsible | Notes |
|---|---|---|---|
| Command bar | 48 px | no | always visible; holds global search, agent state, HALT |
| Sidebar | 240–320 px, resizable | `Ctrl+B` | tree navigation |
| Center stage | flex | no | switchable view |
| Inspector | 300–420 px, resizable | `Ctrl+I` | context-sensitive; auto-opens on selection |
| Dock | 4 states | `Ctrl+\`` | terminal / logs / problems |

**Center stage is switchable, not fixed.** `Ctrl+1..4` → Orbit · Chat · Timeline · Tasks; from
Phase 11, `Ctrl+5` → Knowledge graph (§11b). When a
conversation starts, ORACLE **auto-switches to Chat** and the orbit demotes to a 40 px core indicator
in the command bar. This is the resolution of "beautiful centrepiece vs. useful interface": the orbit
is the ambient/idle view, chat is the working view, and the transition is automatic.

---

## 3. The core (orbital view) — **Phase 11**

### What it must communicate

Only these, at a glance, from across the room:

1. What is ORACLE doing right now (state)
2. What is it working on (active context)
3. Does it need me (waiting for approval)
4. What went wrong (error)

### The core itself

A ring with a centre pulse. State is encoded by **colour + motion + label**, never colour alone:

| State | Colour | Motion | Centre label |
|---|---|---|---|
| `idle` | slate 600 | still, faint 6 s breath | `IDLE` |
| `understanding` | cyan 400 | fast inner shimmer | `THINKING` |
| `retrieving` | cyan 400 | ring sweep | `SEARCHING` |
| `planning` | blue 400 | segmented ring assembling | `PLANNING` |
| `awaiting_approval` | **amber 400** | slow 1 s pulse, ring gap | `NEEDS YOU` |
| `executing` | blue 500 | rotating arc, speed ∝ activity | `RUNNING` |
| `delegating` | violet 400 | outbound particles toward a node | `DELEGATED` |
| `summarizing` | teal 400 | contracting ring | `WRAPPING UP` |
| `error` | red 500 | single sharp flash, then static | `ERROR` |
| `halted` | red 700 | fully static, cross-hatched | `HALTED` |

`awaiting_approval` is the only state permitted to be visually loud. It should be impossible to miss
and impossible to confuse with `executing`.

### Nodes and orbits

Nodes are contexts: projects, tasks, agents, collections, processes.

- **Ring = category.** Ring 1 (innermost): active tasks. Ring 2: projects. Ring 3: agents and
  collections. Ring 4: ambient processes (docker, watchers).
- **Angle = `hash(node.id)`, deterministic.** Asterim sits at the same angle today and next month.
  Stability is what makes the view readable; this is why there is no force simulation
  ([TECH_STACK.md](TECH_STACK.md#visualisation-svg--deterministic-layout-not-a-graph-library)).
- **Radius = recency/attention** — a node touched in the last minute pulls inward.
- **Size = magnitude** (task count, index size). **Opacity = staleness.**
- **Edges appear only during actual data flow**, and fade after 2 s. A permanently drawn graph is
  wallpaper; an edge that appears when ORACLE reads from Obsidian is information.

Node badge: count (open tasks), state dot, and a one-word status. Hover → tooltip with the last event.
Click → Inspector. Double-click → focus that context (filters the whole UI to it).

### Cost and honesty rules

- Idle: **< 5% CPU**; animation pauses entirely when the window is unfocused or `prefers-reduced-motion` is set.
- Rotation is slow — one revolution per 90 s. Fast rotation reads as "busy" and would lie.
- **No decorative nodes.** If there is nothing orbiting, the ring is empty and that is the honest answer.

---

## 4. Sidebar

```
WORKSPACE                         ⌃B

▾ PROJECTS                    [+]
  ● Asterim          2 tasks  ⎇ main ↑3
  ○ SCRAPSHIFT
  ○ GameRecs
  ○ Source2DemViewer
  ⋯ show all (7)

▾ TASKS                         3
  ▸ Active                      1
  ▸ Waiting on me               1   ← amber when > 0
  ▸ Completed today             6
  ▸ Failed                      1   ← red when > 0

▾ AGENTS
  ● local      qwen3.5:2b   GPU 3.1/4.0 GB
  ○ claude     idle
  ⚠ antigravity  not configured

▾ KNOWLEDGE
  Obsidian Notes      161 docs   ✓ 2m ago
  Projects            798 docs   ⟳ indexing 42%
  Documents           —          not indexed

▾ RECENT
  auth.service.ts
  ROADMAP.md
```

Rules: sections collapse and persist · counts are live · **"Waiting on me" is the only sidebar item
allowed to demand attention** (amber, and it sorts to the top when non-empty) · the agent section
shows the real model and real VRAM, because on 4 GB that number is genuinely operational · clicking a
project scopes the whole UI to it (a breadcrumb appears in the command bar).

---

## 5. Dock: terminal, logs, problems

### States (`Ctrl+\`` cycles collapsed ⇄ small; `Ctrl+Shift+\`` expands)

| State | Height | Use |
|---|---|---|
| Collapsed | 32 px tab strip | out of the way; badge shows unread errors |
| Small | 200 px | glancing at output while working |
| Expanded | 45% | reading a build failure |
| Fullscreen | 100% | debugging |

### Terminal tab

xterm.js, backed by a real ConPTY in the backend. Multiple sessions as sub-tabs, each **bound to a
project** (`cwd` pinned, shown in the tab). Search (`Ctrl+F`) with match highlighting. stdout in
foreground colour, **stderr in red-tinted foreground** (not a red background — unreadable), and
agent-initiated commands prefixed with a distinct glyph so I can always tell **who typed it**:

```
 ⟩ npm test                    ← I typed this
 ◆ git status                  ← ORACLE ran this (click → the task that did)
```

That distinction is a trust feature, not decoration.

### Logs tab

The structured event stream, not raw text. Columns: time · level · source · trace · message. Filter
chips (level, source, task). Click a row → the Inspector shows the full event. Follow-tail toggle
that auto-disables on manual scroll.

### Problems tab

Aggregated actionable failures: failed tools, failed tests, indexer errors, integration failures.
Each row → jump to source.

---

## 6. Task Inspector

Opens on any task selection. Sections, in order of what I actually need:

```
Task #128  ·  investigate Asterim auth
● running · 4m 12s · started 03:41

PROJECT   Asterim (⎇ fix/auth)
AGENT     claude-code  →  monitor
TRIGGER   chat message · "why is Asterim auth broken?"

PROGRESS  ▓▓▓▓▓▓▓░░░  step 4/6
          ✓ git.status            84ms
          ✓ know.search           210ms
          ✓ fs.read ×3            41ms
          ◆ ai.delegate           running 3m
          ○ dev.run_tests
          ○ report

FILES     3 changed   [view diff]
COMMANDS  7 executed  [view in terminal]
COST      local 1,240 tok · claude $0.032
LOGS      [open filtered log]

[Cancel task]  [Pause]  [Open worktree]
```

Rules: every row is a link to evidence · costs are shown because delegation is not free · Cancel is
always present and always works · a failed step shows the typed error plus a retry affordance where
retrying is safe.

---

## 6b. The execution tree — **Phase 11**

The supervisor architecture's primary new surface: one root task, its plan, its tasks, their
attempts, and the evidence for each — as a tree, because that is what it is
([ORCHESTRATION.md §6](ORCHESTRATION.md#6-observability)). The tree **is a query** over the
`tasks` table and the event log; it maintains no state of its own.

```
▾ ⚙ continue development on Asterim              running · 12m · $0.14
  ✓ plan        antigravity · planner            4 tasks · [view plan] [egress #1]
  ✓ A implement retry logic   claude · coder     diff +214/−36 · tests 41/41 · [worktree]
  ▾ ✗ B cover the 401 case    claude · tester    tests 40/41 — FAILED       [evidence]
      ✗ attempt 1                                the failing case · [diff] [logs]
      ◆ B′ (replan 1/2)       claude · tester    running 2m
  ○ C review                  antigravity        waiting on B′
  ○ D digest                  local              waiting on C
```

Rules, inherited and extended:

- **Every row links to evidence** — ORACLE's measurements (diff stat, test counts, scope check),
  with the worker's own claim shown separately and labelled as a claim. Same rule as the Task
  Inspector: dead-end numbers are a bug.
- **Superseded tasks stay visible**, collapsed under their replacement — the lineage is the
  explanation of what the graph cost. Nothing is erased, because the event log doesn't erase.
- Status vocabulary matches [ORCHESTRATION.md §2](ORCHESTRATION.md#2-task-model) exactly —
  `skipped` renders differently from `cancelled`, `timeout` differently from `failed`, and each
  says why in one word.
- Layout uses a topological rank with **longest-path** column assignment (ported from Asterim's
  `dagColumns` — drawing a node next to the root would claim a parallelism the graph does not
  have), plain SVG/DOM, keyboard-navigable, no graph library — the ADR-0013 philosophy applied
  to a tree.
- Per-row cancel for anything running; the graph card's approve/deny state mirrors into the
  Confirmation Center like every approval.

In the **orbital view**, a root task is one node on the tasks ring; its workers appear as child
glyphs while running (`ORACLE → Asterim → Claude·coder / Claude·tester / AGY·review`, the
replan brief's picture). Selecting any of them opens this tree in the inspector. The orbit still
answers "what is ORACLE doing"; the tree answers "how, and with what evidence".

### What was built  `P7-T3, 2026-08-25` — the list, not the tree

`TaskTree.tsx` is the *plain* version of the above: a list per graph, with dependencies, status,
and a cancel button per stoppable row. The orbital view, the longest-path layout and the
superseded-attempt lineage stay Phase 11; this exists because until it did, a running graph was
visible only by reading the `tasks` table by hand.

Three of the rules above are already load-bearing and are enforced by tests, not by intention:

- **Evidence and claim render apart.** `ORACLE measured: 583 passed, 29 failed` and
  `the worker said: "everything passes"` are different elements with different labels. A vitest
  asserts they are not the same node — the backend keeps the two apart through the runner, the
  store and the API, and the last place it could be thrown away is the screen.
- **`skipped` does not read like `cancelled`.** It renders as *"skipped — an earlier task did not
  succeed"*, because "skipped" alone reads as a choice somebody made, and it was not. Another
  test asserts the two labels differ.
- **Nothing optimistic.** The cancel button sends `graph.cancel` and changes no row; the status is
  whatever the server last said, exactly as the delegation card's discard button behaves.

The store folds `task.*` events stamped `source: "graph"` into a `graphs` slice, separate from
`delegations`. Both are folded from the same event types: a delegation is one worker's lifecycle,
a graph is the shape of the work, and a `DELEGATION` task appears in both under one `task_id` —
which is what will let a reader click from one to the other when Phase 11 draws it properly.

## 7. Activity timeline

The event log, rendered. A vertical chronological stream, grouped by turn, filterable by
project/task/tool/level.

```
03:41:02  ▸ turn started        "why is Asterim auth broken?"
03:41:02    intent investigate  conf 0.81   →  Asterim
03:41:03    context assembled   6 chunks · 4,180 tok   [inspect]
03:41:04  ✓ git.status          84ms   branch fix/auth, 3 modified
03:41:05  ✓ know.search "auth"  210ms  6 results       [inspect]
03:41:07  ⚠ approval requested  ai.delegate → claude   [approved by desktop 03:41:19]
03:41:19  ◆ claude started      worktree .oracle/wt/128
03:45:41  ✓ dev.run_tests       41 passed, 0 failed
03:45:44  ● turn completed      4m 42s
```

`[inspect]` opens the exact context or result that was used. **This is the debugging surface for the
agent itself** — when ORACLE does something strange, this is where I find out why. Retention matches
the event log; older entries are summarised per turn.

---

## 8. Agent queue

Compact, in the sidebar or as a center view:

```
NOW       investigate Asterim auth        4m12s   [cancel]
NEXT      run frontend tests                      [skip]
WAITING   claude · review changes         3m      [monitor]
BLOCKED   git.push → needs approval               [review] ← amber
DONE      indexed Obsidian (161 docs)     2m ago
```

`BLOCKED` items always sort to the top and mirror into the Confirmation Center.

---

## 9. Confirmation Center  `BUILT 2026-08-21`

The most safety-critical surface in the product. **It must show the real action, never a paraphrase.**

```
┌────────────────────────────────────────────────────────┐
│ ⚠  APPROVAL REQUIRED                          T2       │
│                                                        │
│ ORACLE wants to run:                                   │
│                                                        │
│   git push origin fix/auth                             │
│   in C:\Projects\Asterim                               │
│                                                        │
│ WHY   "push the fix branch so CI can build it"         │
│ TASK  #128 investigate Asterim auth        [inspect]   │
│                                                        │
│ EFFECT  3 commits → origin/fix/auth (new remote branch)│
│         visible to others · cannot be unpublished      │
│                                                        │
│ ⓘ proposed after reading node_modules/@auth/README.md  │
│   ⚠ this turn is TAINTED — tier raised T1→T2           │
│                                                        │
│ expires in 4:38                                        │
│                                                        │
│   [ Approve  A ]   [ Deny  D ]   [ Always for Asterim ]│
└────────────────────────────────────────────────────────┘
```

### What was built, and the two places it differs from this sketch

The card renders `approval.requested` and nothing else: if a fact is not in the event it
could not have informed the decision. Verified live — asking ORACLE to push itself
produced a card listing **the exact nine commits** that would be published, computed
from local refs.

- **"Always for X" was not built, and should not be.** A scoped standing approval is a
  way to make prompts cheaper, and the answer to prompt fatigue is *fewer* prompts —
  which reversibility and the T1 tier already deliver. Revisit only with data from
  [OQ-13](OPEN_QUESTIONS.md#oq-13).
- **EFFECT comes from a real dry run**, not from a description. `dry_run=True` is a
  contract promise that the call performs nothing, which is why it needs no approval of
  its own — and why `git.push`'s preview is computed from local refs rather than
  `--dry-run`, which would contact the remote.

One thing the sketch did not anticipate: **a replayed approval looks live.** History
replays from seq 0 after a reload, so a request from a backend that has since exited
arrives looking new and sits at the head of the queue where nothing can answer it.
Expiry is counted from the server's timestamp, and an already-expired approval never
joins the queue.

#### The graph card  `P8-T3, 2026-08-25`

An `ai.graph` approval carries a whole plan, and until this task the UI rendered a one-line
summary of it: the card fell through to the generic EFFECT block while the payload held every
task, its role, its agent and whether it would egress. "Approving what you did not read is the
attack" is P8-T1's own sentence about this exact card, so `GraphCard` renders all of it —
**objectives verbatim**, never summarised, because an instruction hidden inside a plan is only
defended against if it is visible here.

It also states **who wrote the plan** (`authored_by`, the ladder's rung, and every descent with
its reason). A plan a model decomposed and a deterministic template ORACLE fell back to are
different objects, and a person needs to know which is in front of them *before* they read the
tasks ([PLANNER.md §6](PLANNER.md#6-fallbacks)). A replan's card says its tasks are being **added**
to a graph already running, which failure they replace, and that the failed task stays failed.

Rules:

- The command block is the **actual resolved argv**, monospaced, selectable, never re-worded by a model.
- **Provenance line is mandatory** when the turn is tainted. It's the single most useful signal for
  spotting a prompt-injection attempt ([SECURITY.md §6](SECURITY.md#6-prompt-injection-and-taint-tracking)).
- **T3 requires a typed phrase** (the project name) and a 10 s cool-down before Approve enables.
- Approve is **never** the default focus, and a 500 ms guard blocks accidental double-Enter from a
  previous action.
- Expiry is visible and real — an expired approval cannot be used.
- "Always for X" creates a **scoped, expiring** rule and says exactly what it will cover.
- Queued approvals stack with a count; each is decided individually. No "approve all".

---

## 10. Command palette (`Ctrl+K`)  `BUILT 2026-08-21`

The fastest path to anything, and the UI half of the pre-router
([AGENT_RUNTIME.md](AGENT_RUNTIME.md#step-1-in-detail--the-pre-router-earns-its-keep)). Every action
routed here costs zero model latency, which is why it ships in the MVP.

```
⌘  run asterim
   ▸ Run pipeline: asterim-check                    pipeline
   ▸ Run tests: Asterim                             dev.run_tests
   ▸ Open project: Asterim                          navigate
   ▸ Ask agent: "run asterim"                       chat        ⏎ fallback
```

Modes by prefix: `>` commands · `@` projects · `#` tasks · `/` files · `?` ask the agent · plain text
searches everything. Results are ranked by recency and frequency. **The last resort is always "ask
the agent"** — the palette never dead-ends.

---

## 11. Global search (`Ctrl+Shift+F`)

Full-surface search across six sources, grouped, with counts and previews:

```
Search: authentication

PROJECTS  (3)   Asterim · GameRecs · SCRAPSHIFT
FILES     (12)  auth.service.ts · authMiddleware.ts …
NOTES     (4)   Obsidian/Auth patterns.md …
TASKS     (2)   #128 investigate Asterim auth …
LOGS      (31)  03:41 know.search "auth" …
GIT       (7)   a3f21c fix auth token refresh …
```

Backed by hybrid retrieval (P5) for notes/files and direct queries for the rest. Target p95 < 300 ms.
`Tab` cycles source groups; `Enter` opens; `Ctrl+Enter` sends the result to the agent as context.

---

## 11b. The knowledge graph — **Phase 11**

> Added 2026-08-24 from the owner's design references: film-HUD radial consoles (layered
> translucent rings, a luminous focused core, everything else receding into depth) and
> Obsidian-style cluster graphs (collection-coloured constellations, hub-and-spoke stars, orphans
> drifting at the rim). The brief: *see everything ORACLE knows — Obsidian vaults, project docs,
> PDFs — as one interactive map.* The data layer mostly exists: `knowledge.db` already holds
> every document, its collection, its embeddings, and a `links` table of extracted `[[wikilinks]]`
> ([RAG.md §3](RAG.md#3-chunking), `rag/store.py`). This section is the view over it.
>
> Layout and rendering decisions are recorded as
> [ADR-0023](DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered);
> the open measurements are [OQ-22](OPEN_QUESTIONS.md#oq-22).

### What it must answer

The graph earns its place the same way the orbit does — by answering questions the list view
cannot. The four it exists for:

1. **Shape** — how is my knowledge actually organised? Where are the hubs, the clusters, the
   bridges between a vault and a project?
2. **Neglect** — what is orphaned, stale, or was never indexed?
3. **Reach** — starting from this note, what is connected, one and two hops out?
4. **Use** — what did ORACLE just retrieve to answer me, and from where?

If, after real use, it answers none of these better than search does, it gets cut and an ADR
records that — the same honesty gate as the orbit ([OQ-14](OPEN_QUESTIONS.md#oq-14) applies to
both, per view).

### Nodes and edges

| Element | Source | Encoding |
|---|---|---|
| **Node = document** | `knowledge.db` documents (~1,330 today; design ceiling 10k) | colour = **collection** (each vault/project/doc-set gets a stable token-derived hue) · size = link degree · opacity = staleness (same semantics as the orbit) |
| **Explicit edge** | the `links` table (`[[wikilinks]]`, already extracted at index time) | solid, dim by default |
| **Semantic edge** | k-nearest-neighbour over document embeddings, thresholded, capped per node — computed offline with the index, never live | fainter, dashed; **off by default**, a toggle — inferred similarity is a suggestion, and drawing it like a fact would lie |
| **Retrieval edge** | episodic: documents co-cited in one answer (event log) | appears only in trace mode, below |
| **Collection hull** | derived | a barely-visible tinted region behind each cluster, so colour is not the only carrier (accessibility rule) |

**Orphans are shown honestly**: documents with no edges sit on an outer arc at the rim — the
reference images' peripheral ring, kept because it *is* the honest rendering of disconnection.
Finding them is question 2; hiding them would delete the answer. Documents that **failed to
index** appear hollow with an error affordance; collections that are registered but unindexed
appear as a single ghosted hull with a "index this" action. No decorative nodes, ever.

### Layout — simulated, then frozen

The stability principle from [ADR-0013](DECISIONS.md#adr-0013--deterministic-svg-orbit-no-force-simulation)
holds — a map you cannot memorise is decoration — but its mechanism (hash-angle polar layout)
cannot scale to a thousand nodes where *cluster adjacency is the information*. The resolution
(ADR-0023):

- A force-directed layout runs **offline** — in the indexing worker, alongside a reindex — and
  the resulting positions are **persisted in `knowledge.db`** next to the documents they place.
- The live view **never simulates**. It renders frozen positions; the vault sits where it sat
  last month. No jitter, no per-frame physics cost, idle CPU stays under the 5% budget.
- New documents are placed incrementally at the centroid of their neighbours (or the collection
  hull's edge when unlinked) without moving anything else.
- **Re-layout is an explicit action** on the index health view, like reindexing — with a
  before/after preview, because it destroys spatial memory and should be chosen, not suffered.

### Visual language

The reference material's *language*, filtered through this document's anti-goals (no gratuitous
glow, no fake telemetry, nothing animates without meaning):

- **Idle**: a dim constellation on `--bg-0`. Labels appear only above a zoom threshold and for
  hubs; the map reads as shape first, names second.
- **Focus mode** (click a node, or arrive from search): the selected node and its 1–2-hop
  neighbourhood come to full luminance; everything else recedes to near-black rather than
  disappearing — depth, not deletion. This is the gold-hologram reference as an interaction
  state, not a permanent style. `Esc` releases it.
- A focused node gets a **radial metadata ring** — the HUD language — showing collection, age,
  degree, and tags as labelled arcs. Every arc is real data with a real click-through; the moment
  one is decorative it is removed.
- **Motion means something happened**: a node being reindexed pulses once; a retrieval trace
  animates once, then stays lit until dismissed. Nothing loops. `prefers-reduced-motion` replaces
  all of it with instant state changes, fully usable.

### Interaction

| Gesture | Effect |
|---|---|
| pan / zoom | free navigation; labels density scales with zoom |
| hover | tooltip: title · collection · modified · links in/out |
| click | Inspector: metadata, outlinks/backlinks (each a jump), chunk list, **Open** (via `app.launch` alias — Obsidian for notes, editor for code), **Ask ORACLE about this** (pre-fills chat with the doc pinned as context) |
| double-click | focus mode on that node's neighbourhood |
| `Ctrl+F` in view | search-to-locate: matches glow, view flies to the best hit; wired to the same hybrid search as everything else |
| filter chips | collection · project · type (note/doc/pdf/code) · touched-within time slider · edge-type toggles |
| lasso / shift-click | **select-as-context**: the selection becomes a context package — pin it to the next turn, or hand it to the packet builder. Local, T0; if it later feeds a delegation, the ordinary egress preview prices it like any other context |
| from a chat citation | **"show on graph"**: trace mode — the answer's cited documents light up with their retrieval edges, and the graph becomes the explanation surface for *why those sources*. The timeline's `[inspect]` and this view are two renderings of the same events |

### Rendering and budgets

SVG struggles past a few hundred nodes (ADR-0013 said so itself, back when it was irrelevant), so
this view renders on **canvas**, with a DOM overlay for the focused node, its ring, labels and
the inspector — keeping text selectable and focusable where it matters.
Measured, not hoped ([OQ-22](OPEN_QUESTIONS.md#oq-22)): pan/zoom at 60 fps on the full corpus ·
idle < 5% CPU and full pause when the view is hidden or the window unfocused · offline layout of
the full corpus within the incremental-index budget · first paint < 1 s from cached positions.

### Accessibility

The orbit's rule, unchanged: **a full list-view equivalent**, not alt text — a searchable,
sortable table (document · collection · in/out links · modified · staleness) with the same
filters and the same actions, toggled by a control and default for screen readers. Focus mode's
neighbourhood is enumerable from the inspector as a list. Colour never carries meaning alone:
collections get hulls and labels, staleness gets a badge.

### Explicitly not

No live physics in the viewport · no 3D · no edge bundling until a measured hairball demands it ·
no automatic "AI insights" overlaid on the map (the graph shows what *is*; analysis happens in
chat where it can cite) · no second data pipeline — every node, edge and failure state comes from
`knowledge.db` and the event log, or it does not appear.

## 12. Notifications

Toasts, bottom-right, max 3 stacked, then a "+N" collapse.

| Kind | Duration | Sound |
|---|---|---|
| Task completed | 4 s | none |
| Task failed | sticky until dismissed | none |
| **Approval needed** | sticky + core state change | optional, off by default |
| Degradation (LLM offline) | sticky banner, not a toast | none |

Never notify about routine automatic actions — that is log territory. A notification means *something
changed that I would want to know without looking*. Everything else is noise, and noise trains me to
ignore the channel that matters.

---

## 13. System monitor

A compact strip in the command bar: `CPU ▁▃▅ 34%  RAM 41%  GPU 78%  VRAM 3.1/4.0`. Expandable to a
small panel with 60 s sparklines and the top processes.

**VRAM is the one genuinely operational number** — it explains why the model is slow or why it
unloaded — so it gets a colour threshold (amber > 85%, red > 95%). Everything else is ambient. This is
not a hardware monitoring application; if it starts growing tabs, it has failed.

---

## 14. Colour and status semantics

Tokens, defined once as CSS variables and used everywhere. Meaning is fixed across the app.

| Token | Hue | Means | Used by |
|---|---|---|---|
| `--st-idle` | slate 500 | nothing happening | core, nodes |
| `--st-think` | cyan 400 | reasoning/retrieving | core, chat |
| `--st-run` | blue 500 | executing | core, tasks, terminal |
| `--st-wait` | **amber 400** | **needs a human** | approvals, queue, sidebar |
| `--st-ok` | green 500 | success | tests, tasks |
| `--st-err` | red 500 | failure | problems, errors |
| `--st-halt` | red 700 | halted / blocked by policy | core, banner |
| `--st-ext` | violet 400 | external agent | delegation |

Base surfaces: `--bg-0` (app) → `--bg-1` (panel) → `--bg-2` (raised) → `--bg-3` (input). Text:
`--fg-0` primary, `--fg-1` secondary, `--fg-2` muted.

**Contrast: all text ≥ 4.5:1, all status indicators ≥ 3:1 against their surface.** Amber on dark is
the risky one and must be verified, not assumed. A light theme is Post-MVP but the token structure
makes it a values change, not a refactor.

### Verified  `2026-08-26`

It had not been. `a11y.test.tsx` disables axe's `color-contrast` rule — correctly, because happy-dom
lays nothing out — so the one rule this section singles out as risky was the one rule the audit
could not check, and nothing else checked it. `contrast.test.ts` now does, as a pure function over
the token values parsed **out of `styles.css` itself**, so it cannot drift from the stylesheet.

**Amber was fine, and this section guessed wrong about which token was risky.** `--st-wait` measures
7.22–8.99:1 — comfortably over both bars on every surface. Two others failed:

| token | was | measured | now |
|---|---|---|---|
| `--st-halt` | `#b91c1c` | **2.99 / 2.82 / 2.63 / 2.40** — under 3:1 on *every* surface | `#dc2626`, 3.21 worst |
| `--fg-2` | `#5c6779` | 3.38 / 3.19 / **2.98 / 2.71** — under 3:1 on the two raised surfaces | `#6b7688`, 3.38 worst |

`--st-halt` is the serious one: **the least visible status in the application was the one that means
everything has stopped.** Raising it narrows the luminance gap to `--st-err`, which is acceptable
because the table above never asked hue to carry that distinction — halt is *"fully static,
cross-hatched"* where error is not, and §17 gives the halted state a red-tinted border across the
whole UI with every control disabled. Colour is the least of four signals.

`--fg-2` is muted decoration rather than body text, so 3:1 is the bar that applies to it — but it was
under even that on `--bg-2` and `--bg-3`, which are exactly the surfaces cards and inputs use.

---

## 15. Motion

| Token | Duration | Easing | Used for |
|---|---|---|---|
| `--m-instant` | 80 ms | ease-out | hover, focus |
| `--m-quick` | 160 ms | ease-out | panels, tooltips |
| `--m-normal` | 240 ms | ease-in-out | view transitions |
| `--m-slow` | 400 ms | ease-in-out | dock resize |
| `--m-ambient` | 90 s | linear | orbit rotation |

Rules: motion communicates causality (a new node animates *from* the thing that created it) · nothing
loops except the core state indicator · **`prefers-reduced-motion` disables all ambient motion and
shortens transitions to 80 ms**, and the interface must remain fully usable in that mode — verified in
tests, not assumed.

---

## 16. Keyboard

| Keys | Action |
|---|---|
| `Ctrl+K` | Command palette |
| `Ctrl+Shift+F` | Global search |
| `Ctrl+1..4` | Orbit / Chat / Timeline / Tasks |
| `Ctrl+B` / `Ctrl+I` | Toggle sidebar / inspector |
| `Ctrl+\`` / `Ctrl+Shift+\`` | Cycle dock / expand dock |
| `Ctrl+Enter` | Send message |
| `Esc` | Close overlay · cancel edit · defocus |
| `A` / `D` | Approve / Deny **(only when an approval card has focus)** |
| `Ctrl+.` | Jump to the oldest pending approval |
| `Ctrl+Shift+C` | Cancel the current task |
| **`Ctrl+Alt+Shift+H`** | **HALT** — global, works even when unfocused |
| `Ctrl+P` / `Ctrl+Shift+P` | Quick-open file / project |
| `F1` | Shortcut cheat sheet |

HALT is deliberately awkward: four keys, so it cannot be hit by accident, but it is a **global**
hotkey so it works when the window isn't focused — which is exactly when I'd need it.

**Everything in the MVP is reachable without a mouse.** That is a P4 acceptance criterion.

---

## 17. States: loading, empty, error, offline

| State | Treatment |
|---|---|
| **Loading** | Skeletons matching final layout — never spinners for content. Spinners only for indeterminate actions under 2 s. Beyond 2 s, show what is happening ("indexing 42% · 340/798 files"). |
| **Empty (first run)** | Onboarding, not a blank page: connect a project → pull a model → index notes → ask something. Three steps with real buttons. |
| **Empty (no results)** | Say what was searched and offer the next action ("nothing in Obsidian for *auth* — search all projects?"). |
| **Error** | Typed cards: what failed · why (plain language) · what ORACLE tried · what I can do. Never a raw traceback in the primary surface; `[details]` expands it. |
| **Offline (backend down)** | Full-width banner, reconnect countdown, auto-retry with backoff. Cached view stays readable. |
| **Degraded (LLM down)** | Banner: "reasoning offline — commands and search still work". Chat input disabled with an explanation; palette stays live. This state must feel *limited*, not *broken*. |
| **Halted** | The whole UI takes a red-tinted border, the core shows `HALTED`, every action control is disabled except Resume. Unmissable by design. |

---

## 18. Accessibility

Non-negotiable, and cheap if done from P4 rather than retrofitted.

- Full keyboard operability; visible focus rings (2 px, `--st-think`), logical tab order, focus traps
  in modals with restoration on close.
- Semantic landmarks (`banner`, `navigation`, `main`, `complementary`, `contentinfo`).
- `aria-live="polite"` for agent state changes; `aria-live="assertive"` **only** for approval requests
  and errors.
- **The orbit has a full list-view equivalent**, not a token alt-text — same data, same actions,
  toggled by a control and used automatically by screen readers.
- xterm.js screen-reader mode available behind a setting.
- All status conveyed redundantly (icon + text + colour).
- `prefers-reduced-motion` and `prefers-contrast` respected.
- Minimum target size 32×32 px for pointer targets; 24 px in dense tables with adequate spacing.
- Axe audit with **zero criticals** is a P4 gate.

---

## 19. Responsive behaviour

One codebase, three layouts. Breakpoints are about *available space*, not device class.

| Width | Layout |
|---|---|
| ≥ 1600 | Full: sidebar + center + inspector + dock |
| 1200–1600 | Inspector becomes an overlay drawer |
| 900–1200 | Sidebar collapses to icon rail; dock overlays |
| < 900 (mobile) | Single column, bottom tab bar: **Chat · Tasks · Approvals · System**. No orbit, no terminal input (read-only log view). See [MOBILE.md](MOBILE.md). |

Mobile is not a shrunken desktop: it is the *approve, observe, ask* subset. Composing a complex plan
on a phone is not a use case; approving one at a bus stop is — and even then, T3 is desktop-only.

---

## 20. Component hierarchy

```
AppShell
├── CommandBar
│   ├── GlobalSearchInput  ├── CoreIndicator(mini)  ├── ProjectBreadcrumb
│   ├── SystemStrip        └── HaltButton
├── Sidebar
│   └── TreeSection ×4 (Projects · Tasks · Agents · Knowledge) → TreeNode
├── CenterStage
│   ├── ViewTabs
│   ├── OrbitView      → CoreVisual · OrbitRing ×4 → OrbitNode · FlowEdge   [P11]
│   ├── ExecutionTree  → TaskNode (tree) · AttemptRow · EvidenceLink        [P11]
│   ├── KnowledgeGraph → GraphCanvas · FocusRing · CollectionHull           [P11]
│   │                    · GraphListView (a11y equivalent) · TraceOverlay
│   ├── ChatView       → MessageList → MessageBubble · ToolCallCard · PlanCard
│   │                                 · CitationChip · StreamingIndicator
│   ├── TimelineView   → TimelineGroup → TimelineEvent
│   └── TasksView      → TaskTable · AgentQueue
├── Inspector
│   └── TaskInspector │ NodeInspector │ EventInspector │ ContextInspector
├── Dock
│   └── DockTabs → TerminalPanel(xterm) │ LogPanel │ ProblemsPanel
└── Overlays
    ├── CommandPalette  ├── GlobalSearch  ├── ConfirmationModal(T3)
    ├── ToastStack      └── OnboardingFlow
```

Shared primitives: `StatusDot`, `RiskBadge`, `Duration`, `TokenCount`, `PathChip`, `ProjectChip`,
`CodeBlock`, `DiffView`, `Sparkline`, `EmptyState`, `ErrorCard`, `SkeletonBlock`.

---

## 21. Interaction rules

1. **Selection drives the inspector.** One selection model across the app.
2. **Every number is a link.** "3 files changed" opens the diff. "7 commands" opens the terminal
   filtered. Dead-end numbers are a bug.
3. **Destructive controls are never adjacent to routine ones**, and never the default focus.
4. **Optimistic UI is banned for anything with a side effect.** Show `pending` until the backend
   confirms. An interface that lies about whether it pushed to origin is worse than a slow one.
5. **Long operations report real progress or say they can't.** No fake percentages.
6. **The agent never silently changes my view.** It may highlight or badge; only auto-switching to
   Chat on a new turn is permitted, because I initiated it.
7. **Everything the agent did is reachable in ≤ 2 clicks from the timeline.** This is the property
   that makes an autonomous tool trustworthy.
