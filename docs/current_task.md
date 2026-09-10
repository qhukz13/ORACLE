- ~~`TaskTree`'s fixture can be re-recorded from the wire.~~ **Done 2026-09-10.**
  `scripts/record_graph_fixture.py` records a real graph off the event log, and
  `TaskTree.recorded.test.tsx` replays it through the **real store reducer**. The recording
  contains what no hand-written fixture would: the delegation service emitting its own `task.*`
  for the *same task ids* without `source: "graph"`, interleaved with the scheduler's.
  **Which assertion has teeth was measured, not assumed** — folding both ways showed counts,
  statuses and `dependsOn` all survive without the filter, and `kind` is what breaks. The first
  draft of that test claimed otherwise and asserted none of it.
- **Still open from `tasks` being non-zero:** [OQ-14](OPEN_QUESTIONS.md#oq-14) can now be judged
  against real data — but it is a go/no-go on a *centrepiece* whose visual references were never
  attached (UI.md §1/§14/§15 remain `TO VERIFY`), so it wants the owner's eye, not an agent's ·
  the agent queue has something to render.
# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**Both owner decisions are taken. [OQ-27](OPEN_QUESTIONS.md#oq-27) is resolved by
[ADR-0028](DECISIONS.md#adr-0028--a-dispatched-approval-does-not-expire) — dispatched approvals
wait. Next: [OQ-14](OPEN_QUESTIONS.md#oq-14) — build the minimal orbit against real data, run the
comprehension test, and cut it with an ADR if it fails.**

**Phase:** [11 — execution visualisation & advanced UI](ROADMAP.md#phase-11--execution-visualisation--advanced-ui--capability-arc) · **Scope:** Capability arc
**Status:** `READY` · **Set:** 2026-09-09 · **Blocked on:** nothing

**Done 2026-09-09:** [OQ-22](OPEN_QUESTIONS.md#oq-22) resolved on all four measurements ·
[ADR-0023](DECISIONS.md#adr-0023--the-knowledge-graph-is-simulated-then-frozen-canvas-rendered)
confirmed · the data layer (`rag/graph.py`, `document_vectors`, `document_positions`) · the API
(`GET /api/v1/knowledge/graph`, `POST /api/v1/knowledge/relayout`) · the view on **Ctrl+5**,
verified against the real corpus at 1,564 documents / 3,465 edges.
[Report](current_report.md) · [dev log](../logs/development/2026-09-09-oq22-canvas-vs-svg.md)

### What remains

**§11b is complete as specified.** Traces, hulls, the legend and select-as-context all landed
2026-09-09. Two things are outstanding, and neither is new construction:

1. **A live retrieval trace has never been seen.** The mechanism is unit-tested against the exact
   payload `rag/retrieval.py:to_citation` emits, but no real retrieval has lit the map up, because
   the router does not reach for `know.search` — see the tool-selection finding below. Worth one
   confirmation the first time a `know.*` call actually runs.
2. **The collection hull is on probation.** It traces each collection's core (hulling the whole
   collection just hulls the orphan ring, which is a polygon over the entire map). It reads weakly
   on this corpus, and the legend plus the per-row collection name are what actually discharge
   UI.md §1. If it does not prove useful in real use, cut it — a faint polygon that clarifies
   nothing is decoration.

### Two things to know before touching it

- **Frame budgets written as constants are wrong here.** This display is **163.9 Hz**, so one vsync
  is **6.1 ms**, not 16.7. Measure the floor; never assume it. `apps/desktop/bench/graph-render.html`
  re-measures the real thing in the real window and records itself to `logs/measurements/`.
- **The view must never simulate.** It draws on demand, one coalesced `requestAnimationFrame` per
  change. A standing loop would burn a core to say nothing and break the measured 1.09% idle.

## Still owed, in rough priority order

- ~~P12-T5 is still one human click.~~ **RUN 2026-09-10, with the owner's explicit approval.**
  `tasks` went **0 → 6 rows** (`oracle-selfcheck`: 6 tasks, 2 stages, 6 m 6 s, all succeeded), and
  `memory_attempts` has 6 rows — P9's attempt machinery holding real data for the first time.
  `continue ORACLE` then ran the whole gated path: `continue.derived` (tainted, quoting
  `current_task.md` + `ROADMAP.md`) → **`ai.delegate` T3 escalated by the taint rule itself** and
  approved → Claude returned a coherent **10-task plan** from real state → `ai.graph` T3.
  [Dev log](../logs/development/2026-09-10-p12t5-the-loop-closes.md).
  **The graph was deliberately refused**, and that is not a failure: its content was the two
  decisions escalated to the owner an hour earlier (RRF weighting; eval self-indexing). A blanket
  "approve everything" is approval to *run the loop*, not licence to let ten delegations settle
  questions that were just argued to belong to a human.
  **Then the owner said run it.** Re-planned, approved, dispatched — and the second plan was better
  aimed than the first, picking exactly what this ledger had listed as newly unblocked (read OQ-14
  for real evidence; re-record `TaskTree`'s fixture). **Both delegations then failed because their
  egress approvals expired at 180 s** — `resolution: expired, by: timeout` — and the three
  dependents were skipped. `tasks` is now **11 rows: 6 succeeded, 2 failed, 3 skipped**.
  **Acceptance now:** plan-from-real-state ✓ · sidebar ✓ · gate green ✓ · *"one end-to-end run
  writes rows to `tasks`"* — 11 rows now exist with real failures, which is better material for the
  execution tree than an all-green run · *"observed state is never persisted"* — **already met**:
  `test_the_projects_table_stores_nothing_git_owns` has asserted it all along, and the earlier
  ledger entry listing it as outstanding was simply wrong.
- ~~[OQ-27](OPEN_QUESTIONS.md#oq-27) — P12's DoD says "walks away", and you cannot.~~
  **RESOLVED 2026-09-10 by [ADR-0028](DECISIONS.md#adr-0028--a-dispatched-approval-does-not-expire)
  (owner chose option 2).** A dispatched request resolves only on an explicit answer, HALT,
  cancellation or restart; `system.boot` clears the queue so a clockless card cannot outlive the
  daemon holding it. The card reads *"waits for you"* instead of a countdown. **Nothing grants by
  waiting** — asserted, not assumed — and the *grant* is still bounded from the moment of the
  answer, so "approved once" cannot become a standing permission. Interactive cards keep the
  180 s TTL, and only two call sites opt in: the graph's delegation runner and the replan approval.
  **Superseded detail:** Every delegation a graph dispatches raises its own T3 egress card with a 180 s expiry,
  so **a gated graph decays into a failed one** in about the time it takes to make coffee. The
  timeout is right for an interactive approval and wrong for a dispatched one. Three candidate
  fixes, each with a real cost, are in the question; whichever is chosen **needs an ADR**, because
  it changes what an approval means. Do not fix it by enlarging the number until a demo passes.
- **Now unblocked by `tasks` being non-zero:** [OQ-14](OPEN_QUESTIONS.md#oq-14) can be judged
  against real data (the orbit's go/no-go) · the execution tree's acceptance can be assessed — it
  already renders the real graph with the `dagColumns` longest-path ranking working (`audit` at
  stage 2, *"after …-security"*) · `TaskTree`'s fixture can be re-recorded from the wire · the
  agent queue has something to render.
- ~~OQ-18~~ **RESOLVED 2026-09-10, follow-ups done.** Translation works and the 0.8b mechanism
  *equals* the human ceiling; `Settings.translate_queries` confirmed `True` on evidence.
  **The shipped path composes to 71.1%** — no printed arm is the shipped path, which the first
  writeup got wrong and the correction is recorded. **The 80% gate is still missed**, so Phase 5's
  recall criterion stays unmet. [OQ-18](OPEN_QUESTIONS.md#oq-18) ·
  [dev log](../logs/development/2026-09-10-oq18-resolved.md).
  **The `gated` arm was measuring a gate the product replaced two weeks earlier** — ported, and the
  eval now opens on 13 of 38 fixtures (0 of 25 Russian) instead of 38 of 38, with the constants
  pinned by `tests/test_eval_gate_matches_production.py`.
- ~~A +2.6 point retrieval win is available.~~ **Taken 2026-09-10:
  [ADR-0027](DECISIONS.md#adr-0027--rrf-is-weighted-against-the-lexical-list)** weights the lexical
  list at `LEXICAL_WEIGHT = 0.5` against each dense list. It overturns RAG.md §5's "no tuned
  weights", which is struck through rather than deleted, with the number that overturned it beside
  it. The weight is *injected* like `translator`, so `rag/retrieval.py` stays settings-free and the
  rollback is passing `1.0`.
  ⚠ **Shipped on a ceiling estimate and not yet verified.** `rrf_w2` was measured ungated;
  production gates. So a `gated_w2` arm was added — the combination that actually ships — and
  **the eval is scheduled** (`ORACLE-OQ18-eval`, fires every 15 min, 45-min cap, resumes from
  checkpoint). **On collection: compare `gated_w2` against `gated`.** If the composed number does
  not move, revert per the ADR. Do not let this sit unverified — an ADR accepted on an estimate is
  a decision waiting to be wrong.
- **[OQ-26](OPEN_QUESTIONS.md#oq-26): the eval indexes our writing about the eval, and it ratchets.**
  `MEASURED 2026-09-10`: **92 of 190 top-5 lexical slots (48%) are ORACLE documents**, for a fixture
  set with **zero** answers in ORACLE — the top two hits for one query were OQ-18 dev logs, one
  written the same day about that run. Excluding ORACLE's prose is worth +3 points of lexical
  recall@5 and finds `en-relay-dockerfile`. **But keeping it in is a deliberate decision**
  (*"pretending otherwise would be scoring against a corpus nobody has"*), and excluding *only* the
  measurement artifacts — `logs/development/` — was measured and buys nothing. So the cheap version
  is useless and the useful version overturns a considered call. Both sides now have numbers; the
  decision does not.
- ~~ORACLE cannot retrieve from a chat turn.~~ **FIXED 2026-09-10.** `know.search` was never in
  `ARG_BUILDERS`, so for intent `search` the router had exactly one candidate (`fs.list`) and — per
  ADR-0017, which builds the enum from candidates — could not spell `know.search`. Added with a new
  `"query"` shape that needs no project. `eval_selection.py` **25/25** (was 20/20) with five new
  cases including both live failures and the `fs.list` confusable pair.
  **Verified end to end:** a live turn emits `tool.started know.search` → `tool.finished`, and the
  knowledge map's retrieval trace lit up for the first time.
  ⚠ **What it revealed:** the top hits for *"taint tracking"* were ML notes about *experiment
  tracking* and a `budget.py` — not `SECURITY.md §6`. The plumbing is right and the *ranking* is
  the 68% OQ-18 measured, now visible on a real query. Worth a look once the fusion follow-ups land.
- ~~Palette results are not discoverable to assistive tech.~~ **Already fixed 2026-08-28** and
  carried stale on this list since. `CommandPalette.tsx` implements the full APG combobox pattern
  (`role="combobox"`, `aria-controls`, `aria-activedescendant`, a real listbox) and
  `CommandPalette.test.tsx`'s "the combobox contract" block pins it, including that the
  activedescendant never dangles. Verified 2026-09-10.
- **Global search's 300 ms budget cannot be met, and now there is a profile.**
  `MEASURED 2026-09-10`, warm, live index (17,329 chunks): **query embedding p50 282 ms / p95
  461 ms** and **`vec0` dense scan p50 260 ms / p95 316 ms**. Either alone exceeds the
  whole-endpoint budget, and they are strictly sequential — the scan needs the vector — so the dense
  floor is ~540 ms p50 before BM25 or the other four groups. Neither is reducible by ordinary means:
  the query encoder must be the model that built the index, and `sqlite-vec` is brute force with no
  ANN. Running the endpoint's five groups concurrently saves only the SQL ones, single-digit ms
  against this.
  **The decision is the owner's, not a code change:** revise the budget against the profile, or keep
  a row that stays red forever and gets ignored. A query-vector cache would help repeat searches but
  not p95, which is dominated by first-time queries.
- **The reindex is still unfired** — 57% of live rows exceed the 1200-char cap, and the live index is
  17,329 chunks against the eval corpus's 28,369, so it is well behind. `POST
  /api/v1/knowledge/reindex` is verified live; the full rebuild is ~1 h synchronous. It will also
  populate `document_vectors` as it goes, which makes the knowledge graph's one-time 88 s backfill
  free.
- **`DATABASE.md`'s `facts`/`attempts`/`devices` blocks are still the pre-build sketch.**
- **Tests with implicit wall-clock assumptions, now on a fourth sighting across two tests.**
  `test_a_long_burst_arrives_complete` fails under CPU starvation and passed immediately after on an
  idle box (2026-09-09). Separately, `test_the_launched_app_survives_the_toolhost_dying` was found
  racing its own startup — it launched `python.exe` with no args and no stdin, which exits in
  milliseconds, then asserted it was alive, failing ~1 run in 3 and *blaming the Job Object*. That
  one is **fixed** (it now launches something that sleeps). The pattern is not: these should become
  explicit perf tests or carry deadlines, rather than staying implicit in the merge gate.
- **A correction typed while a graph runs is refused** — the fix, when somebody hits it, is a queue.
- **Scheduled pipeline runs** are post-MVP; PIPELINES.md §5's "nothing above T1 unattended" is
  unenforced because nothing schedules anything.
- **The visual references for the UI vision were never attached**; UI.md §1/§14/§15 remain
  `TO VERIFY` against them.
- **P11 remainder beyond the graph:** T2 orbit (blocked on OQ-14 → blocked on the click above) ·
  the agent queue (needs live task data). ~~Notifications~~ **built 2026-09-10** (UI.md §12) —
  **verified live 2026-09-10**: the approval toast fired on a real `pipe.run` T2 card and vanished
  the moment it was answered; two completion toasts fired on real `task.finished` events. The gap
  recorded in UI.md §12 that morning closed the same day.
- The fossil `phase6-integration` branch can be deleted at leisure.

## Operational notes

- **`main` is the branch.** Commit and push after every task, not batched (owner's standing rule,
  2026-08-28).
- **The daemon holds `.venv\Scripts\oracled.exe`**, so syncing `uv` commands fail with `os error 32`
  while it is up. Use `uv run --no-sync …`, or stop it first — by PID, never by pattern. One plain
  `uv sync` after it stops reconciles the entry-point exe.
- **The Tauri shell spawns `oracled` as a child.** Killing the window takes the daemon with it, and
  a stale window holds `target/debug/oracle-desktop.exe` so the next `cargo` link fails with
  "Access is denied" — stop the old window by PID before rebuilding.
- **Frame budgets written as constants are wrong here.** This display is **163.9 Hz**. Measure the
  vsync floor; never assume 16.7 ms.
