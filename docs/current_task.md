# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P11-T5 — the centre stage becomes switchable, and the task inspector**

**Phase:** [11 — execution visualisation & advanced UI](ROADMAP.md#phase-11--execution-visualisation--advanced-ui--capability-arc) · **Scope:** Capability arc
**Status:** `SET` · **Set:** 2026-08-26
**Done in Phase 11 so far:** T1, T3, T4 — see below.

---

## Where Phase 11 is

| Task | State |
|---|---|
| **T1 — OQ-22 measurements** | **done.** Three of four answered; the graph is worth building, narrower than §11b describes. [dev log](../logs/development/2026-08-26-oq22-knowledge-graph.md) |
| **T2 — OQ-14, the orbit go/no-go** | **blocked on data, not on effort.** See below. |
| **T3 — execution tree, backend** | **done.** `task.*` carries `depends_on`, objective, role, agent, attempt, timings and cost. |
| **T4 — execution tree, view** | **done.** `graph/rank.ts` longest-path columns (property-tested over 200 DAGs), stage/role/agent/attempt on each row, elapsed and cost in the header, the `tt-*` stylesheet the component never had, per-row accessible button names, and seven new axe cases. |
| **T5 — switchable stage + inspector** | **this task.** |
| T6/T7/T8 — knowledge graph | after T5; T1 says build it |
| T9/T10 — timeline, search | first to be cut |

**T2 is not skipped, it is unanswerable today.** OQ-14's test is *"cover every label and it must
still be possible to say what ORACLE is doing"*, and ROADMAP defends scheduling it late by citing
"months of real event data". The `tasks` table is **empty** and the event log holds ~300 events, so
building the orbit in order to judge it would be judging it against a picture we drew ourselves.
**The cheapest unblock is a person running `oracle-selfcheck` once** — local, no egress, one card,
~5 minutes — which produces a real six-task graph with real evidence, timings and cost. Until then
T2 stays open and this file says so.

## This task

`App.tsx` renders `TaskTree` unconditionally above the chat log and has
`type Stage = "chat" | "events" | "memory"` toggled by two buttons. UI.md §2 asks for `Ctrl+1..4`
across Orbit / Chat / Timeline / Tasks, and §6b for *"selecting any of them opens this tree in the
inspector"*.

1. **Widen `Stage`** to the §2 set, minus Orbit until T2 answers. Bind `Ctrl+1..4`; keep `Ctrl+5`
   free for the knowledge graph if T7 ships.
2. **Move `TaskTree`** out of the always-on stack into its own view.
3. **`Inspector` grows a task branch** beside its `Turn` branch. Its own header already says *"when
   tasks arrive it grows a task above the turn"*.
4. **Mount `KnowledgeHealth`.** It is built, tested, has 11 passing tests — and is imported by
   nothing. ADR-0023 puts the graph re-layout action on it, so T6 needs it reachable.
5. **The evidence links §6b asks for** (`[worktree] [diff] [logs]`) need a *target* and an *action*,
   and the action is a gated tool call. They belong here, with the inspector, rather than as dead
   buttons on a row.

## Acceptance criteria

- [ ] `Ctrl+1..4` switch the centre stage; the binding is tested, not assumed.
- [ ] `TaskTree` lives in the Tasks view rather than above the chat.
- [ ] Selecting a task opens it in the inspector.
- [ ] `KnowledgeHealth` is mounted and reachable.
- [ ] Evidence affordances open something real, or are not rendered.
- [ ] `make check` green.

## Accessibility — structured so it cannot be an afterthought

Three choices make "the list view offers every graph action" true by construction:

1. **One action registry, two renderers.** `graph/actions.ts` defines the actions once; canvas and
   list both render from it, and a vitest asserts the two reachable sets are **equal**. Adding a
   canvas action without a list action fails the build.
2. **`GraphListView` is built in T7, not bolted on after**, sharing the canvas's filter state.
3. **`a11y.test.tsx` grew in T4**, before the remaining surfaces land — 4 of 12 components to 11,
   each rendering the shape the app actually produces. `Inspector` is the one still uncovered and
   should gain a case in T5, since T5 is what puts a task in it.

**Done in T4:** `contrast.test.ts` verifies UI.md §14 as arithmetic over tokens parsed out of
`styles.css`. Amber was fine (7.22–8.99:1) and the section guessed wrong about which token was
risky — `--st-halt`, the colour that means HALTED, was under 3:1 on **every** surface. Both it and
`--fg-2` were raised.

---

## Carried over, not forgotten

- **P9-T3b — the scheduled OQ-18 corpus run.** Windows task `ORACLE-OQ18-eval` fires **2026-08-27
  07:12** (~3 h) and writes `logs/measurements/oq18-translated.{txt,json}`. On collection: compose
  `dense_mt` against `dense_xl` for the shipped path, confirm or flip `Settings.translate_queries`,
  decide `en-relay-dockerfile`, resolve OQ-18, and state the answer-key correction wherever
  pre-2026-08-26 recall numbers are quoted. Then remove the task with `Unregister-ScheduledTask`.
- **The `chunker_version` guard does not fire on the indexes it was written for.** `bind()` raises
  only when a key is *present and different*, then writes the current value in — so an index built
  before the key existed passes and is stamped by whatever binds it first. Already happened to the
  live index: **57% of its 14,586 rows exceed the shipped 1200-char cap, longest 4,055** — the v1
  signature. Decide whether a missing version should refuse; the database wants a reindex either way.
- **Phase 11's other half has no data.** The `tasks` table is **empty** — 0 rows, 0 roots, 0
  superseded — and the event log holds 302 events over 5 days. The execution tree, orbit, timeline
  and queue all render supervisor activity that has never happened outside a test. ROADMAP defends
  scheduling OQ-14 late by citing "months of real event data"; there are none.
  **The cheapest unblock is `oracle-selfcheck`** — local, no egress, six steps, one approval card,
  ~5 minutes — which produces a real six-task graph with real evidence. The Phase 8 scenario is
  richer (it is the only thing that exercises `supersedes` lineage) but costs tokens and egress.
  **Both are T2-or-above and are a person's to run, not an agent's.** T3/T4 can be built against
  fixtures; their acceptance criteria cannot be judged without one real run.
- **A merge-gate test that fails under CPU starvation.**
  `test_a_long_burst_arrives_complete` lost 189 lines of a ConPTY burst twice on 2026-08-26 under
  full load, and at `HEAD` too. Idle it passes in 6 s. If it reappears, decide whether the reader
  drops output under starvation or the deadline is too tight — different repairs.
- **`make perf` and `make eval` are documented in TESTING.md §8 and defined nowhere.** Phase 11's
  acceptance depends on budgets being assertable, so add the targets or correct the doc.
- **`TaskTree.test.tsx` is green on a fixture the app cannot produce.** `store.ts` never populates
  `dependsOn`, so `TaskTree`'s `after {deps}` line is dead in the running app. Fixtures should be
  recorded from the wire, not hand-written.
- **A memory friction**: a correction typed while a graph runs is refused, because "never mid-plan"
  is implemented literally. The fix, when somebody hits it, is a queue — not an exception.
- **Band 6 is not on the interactive answer path.** P9-T2 measured why. Revisit only with a number.
- **Scheduled pipeline runs** are post-MVP; PIPELINES.md §5's "nothing above T1 unattended" is not
  enforced because nothing schedules anything. The hook exists: `check(..., max_tier=Tier.T1)`.
