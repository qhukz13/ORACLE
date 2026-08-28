# 2026-08-28 (evening) — the timeline, global search, and a defect that hid for four days

Second arc of the day. The owner made three calls mid-evening: the OQ-18 corpus run moves
off working hours (a one-time **04:00** trigger with `WakeToRun`; the evening attempt was
stopped at ~60%), **`main` is the branch** (PR #1 merged the `phase6-integration` fossil;
AGENTS.md now says push to main after every task), and the queue keeps moving. Four tasks
shipped, each committed and pushed on completion.

## 1. The timeline (UI.md §7) — `components/Timeline.tsx`

The flat events table had been standing in for the timeline slot since P4, and the tab
honestly said "Events" because of it. Now §7's real shape: contiguous events of one turn
(or one task, for turn-less graph events) fold into collapsible groups — **contiguity,
never a re-sort**, because the log's order is the truth being displayed — with a single
substring filter that forces its matches open, and per-group `[inspect]` driving the
app-wide selection into the inspector.

The standing a11y audit earned its keep **before** the first commit: the first cut nested
the inspect button inside `<summary>`, and axe called it — `nested-interactive`, serious.
The disclosure became a `button[aria-expanded]` (APG), collapsed rows stay mounted under
`hidden` so `aria-controls` never dangles, and the audit's comment about claiming ARIA
patterns only with the behaviour attached got one more proof it pays rent.

Verified against the live daemon: 14 groups folded from the real 500-event stream, the
filter narrowing to the `knowledge.state` bursts caused by this very session's edits.

## 2. Global search, the backend (`GET /api/v1/search`) — and the real story

Two searches wearing one endpoint: files/notes via `know.search` **through the executor
and the gate** (tainted rides through), projects/tasks/events as SQL over the API's own
rows (the briefing's precedent), and GIT only when a project is named — `git.log` has no
grep and the toolhost serialises, so an all-repo sweep is OQ-24's fan-out under a new
name. Each group fails alone; LIKE is escaped so `100%` means the string `100%`.

**Measuring §11's 300 ms target found a four-day-old live defect.** The first latency probe
returned refusals, not numbers: `knowledge.db was built with {'embedding_model':
('bge-m3', 'multilingual-e5-base')…}`. `src/oracle/tools/knowledge.py` had **hardcoded
`E5_BASE`** in `_store()`, `_cache()` and `_embedder()` since before the 2026-08-24 model
switch — the indexer moved to `bge-m3`, the API-process paths moved (`embedding.DEFAULT`),
and the tool layer kept a private copy of the name. Consequences, from the switch until
tonight:

- every `know.search` / `know.search_code` / `know.read_context` through the toolhost
  failed `bind()` with a SchemaMismatch — **the user-facing retrieval tools were dead in
  the live system for four days** and nothing said so, because nothing routed a real
  `search`-intent turn in that window;
- `know.reindex` (the Rebuild button's tool) failed the same way — *before* touching the
  index, mercifully: bind() raises before the store opens, so the wrong-model rebuild
  that would have been catastrophic was never reachable;
- **the fixture suite was insulated by the bug's own shape**: a test's empty tmp index
  binds whatever the tool asks for, self-consistently. Green on a world the machine does
  not have — the same lesson as `TaskTree`'s hand-written fixture, one layer down.
- the first test run after the fix hit the 120 s `pytest-timeout` once (the first-ever
  successful 2.3 GB bge-m3 load, cold cache, right after the probe held 3 GB) and passed
  clean on every rerun — the new timeout did exactly its job: a named failure, not a
  wedged gate.

Fix: the tool layer aliases `embedding.DEFAULT` exactly once (`_MODEL`), and a security
test pins `knowledge._MODEL is embedding.DEFAULT` — "one name to change" is only true
when nobody keeps a private copy of the name. **The fix reaches the live system on the
next daemon restart**, same restart the reindex and search endpoints are waiting for.

**The number, once the tools worked:** cold 34.2 s (the one-time model load the daemon's
prewarmed toolhost holds resident); warm **p50 681 ms / p95 1,270 ms** for the retrieval
half through the gate, 16 results, real corpus. §11's `p95 < 300 ms` — written before
bge-m3, whose own budget was 400 ms and whose in-process p95 measured 332 (OQ-02) — is
**missed 4× and recorded in place**, not quietly re-argued. The overlay's answer is a
300 ms debounce and `elapsed_ms` on screen.

## 3. Global search, the overlay (`components/GlobalSearch.tsx`)

`Ctrl+Shift+F`, the palette's combobox contract (born with the roles on — the palette's
audit history is why), six labelled groups with counts, Tab cycling the non-empty ones.
The design decision worth recording: **Enter does only what the app can honestly do** —
select a project (selection, *not* `continue`; starting work stays behind the sidebar's
approval card), inspect a task, jump an event to the Timeline. Files, notes and git
commits are previews and Enter does nothing on them: the app has no viewer, and a fake
affordance is worse than none. `Ctrl+Enter` ("send as context") is deferred with them —
it needs a context-package path no API provides yet. The absent GIT group states its
reason on the surface.

## Ledger

- Suites: **834 + 411 python** (4 search tests, 1 model-pin), **327 UI** (9 search, 11
  timeline/keybinding/axe among the day's 50 new). Gate green per task before each push.
- Commits tonight (all on `main`): timeline `eaece6e` (pre-merge), schedule/branch docs
  `b2165ff`, search backend + defect `041ba86`, overlay `a1cfae8`, plus this ledger.
- **Waiting on one daemon restart:** `POST /knowledge/reindex`, `GET /search`, and the
  `know.*` model fix are all in source but not in the process that has been up since
  01:05. Restart `oracled`, then the Knowledge stage's Rebuild button, the search
  overlay, and live retrieval all light up at once.
- OQ-18 fires at **04:00** (`WakeToRun`; the machine wakes for it and the script's sleep
  guard keeps it awake). The evening checkpoint will be refused — tonight's commits
  changed the corpus, which is the fingerprint guard working — so it starts clean on an
  idle box; collect in the morning per current_task.md.
