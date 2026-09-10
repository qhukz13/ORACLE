# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**OQ-18's follow-ups done, and OQ-26 rewritten on a measured premise. Next up: the two
decisions this leaves — the RRF-weighting ADR, and what to do about the eval indexing our own
writing about the eval.**

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

- **⚠ P12-T5 is still one human click**, and still an owner's task, not an agent's — approvals
  expire in 180 s, so firing it unattended writes a *refused* run into the table the run exists to
  populate. `tasks` remains **0 rows**, and with it [OQ-14](OPEN_QUESTIONS.md#oq-14) (the orbit's
  go/no-go), the execution tree's acceptance criteria, `TaskTree`'s fixture, and the sidebar
  counters. Start the daemon and UI, type `continue ORACLE` in the command bar, approve the T3
  `confirm_strong` card. `oracle-selfcheck` is the cheaper first fill — local, no egress, ~5 min.
- ~~OQ-18~~ **RESOLVED 2026-09-10, follow-ups done.** Translation works and the 0.8b mechanism
  *equals* the human ceiling; `Settings.translate_queries` confirmed `True` on evidence.
  **The shipped path composes to 71.1%** — no printed arm is the shipped path, which the first
  writeup got wrong and the correction is recorded. **The 80% gate is still missed**, so Phase 5's
  recall criterion stays unmet. [OQ-18](OPEN_QUESTIONS.md#oq-18) ·
  [dev log](../logs/development/2026-09-10-oq18-resolved.md).
  **The `gated` arm was measuring a gate the product replaced two weeks earlier** — ported, and the
  eval now opens on 13 of 38 fixtures (0 of 25 Russian) instead of 38 of 38, with the constants
  pinned by `tests/test_eval_gate_matches_production.py`.
- **A +2.6 point retrieval win is available, and it needs an ADR, not a commit.** The best
  composition is **73.7%** — Russian → `dense_mt`, English → **`rrf_w2`** (RRF weighted 2:1 toward
  dense) — against today's 71.1%. But [RAG.md §5](RAG.md#5-hybrid-retrieval) refused weight tuning
  deliberately (*"would have forfeited the property the algorithm was chosen for"*) and gated the
  input instead, measured at +8/+12. **That decision now has evidence against it and should be
  reopened properly.** Two caveats: `rrf_w2` was measured *ungated*, so 73.7% is a ceiling; and
  `rrf_w2_mt` — weighted fusion over the *translated* probe — is still unmeasured and is the one
  combination the composition cannot derive. Fold it into the next full run.
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
- **Palette results are not discoverable to assistive tech** — `<li role="option">` with `onClick`,
  no `role="combobox"`, no `aria-activedescendant`. The rest of the a11y audit is 15/15.
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
- **P11 remainder beyond the graph:** T2 orbit (blocked on OQ-14 → blocked on the click above) · the
  agent queue (needs live task data) · notifications.
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
