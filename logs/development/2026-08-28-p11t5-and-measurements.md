# 2026-08-28 — P11-T5, two measurements, and the OQ-18 run's post-mortem

One session, four strands: the OQ-18 corpus run found dead and relaunched hardened; OQ-24 and
OQ-25 both measured and resolved; P11-T5 built end to end; and a handful of standing debts paid
(`pytest-timeout`, `make eval`, the a11y audit's last three components, a knowledge-reindex
endpoint). Three subagents ran the isolated strands in parallel; the UI arc was done by hand.

---

## 1. The OQ-18 run was dead, and the two mysteries have answers

The hand-off said the run was alive at 14:0x, ~37%, with 13 elapsed hours "mostly unexplained".
At 15:42 the worker chain was gone, `LastTaskResult 3221225786` (`STATUS_CONTROL_C_EXIT` — the
same code that killed attempt #1), and the log was still its 63-byte header. Fifteen hours of
CPU, zero artifacts, for the second time.

**The starvation mystery is solved: the machine was asleep.** Kernel-Power event 42 at
**01:44:22** (sleep, ~1 h after the run started), Power-Troubleshooter event 1 at **13:37:12**
(wake). Sleep–wake brackets the "unexplained" stretch exactly: the run got ~80 minutes of real
compute, then nothing for ~12 hours, then resumed on wake — which also matches the CPU-seconds
arithmetic (~66k before sleep + ~33k after wake ≈ the ~93.5k measured). A scheduled task does
not keep the machine awake. Nobody had asked it to.

**The kill is explained only to a class.** `STATUS_CONTROL_C_EXIT` means a console control
event — a Ctrl+C or, most plausibly, the task's console window being closed by hand somewhere
in 14:05–15:42. The TaskScheduler operational log is disabled on this machine, so the exact
minute is unrecoverable. Not worth more forensics; worth making unrepeatable.

### The relaunch is hardened against all three failure modes

`scripts/eval_embeddings.py` (and the `.cmd`):

- **Observable.** `-u` in the `.cmd` plus `line_buffering=True` in the script's existing
  `reconfigure`, and a progress line per checkpoint:
  `2304/16519 chunks  1.35 chunks/s  elapsed 1708s  eta 10539s`. An unchanging log file now
  *is* a stuck job, which inverts the trap the docs had to warn about twice.
- **Checkpointed and resumable.** The forward pass runs in 256-chunk slices; after each slice
  the pooled vectors so far are written atomically (`.tmp` + `os.replace`) with the corpus
  fingerprint and a `complete` flag. `--load-vectors` on the same path resumes a killed pass.
  A kill now costs at most one slice (~4 min) instead of everything. Slice-local length
  sorting gives back a little of the 1.8× sorted-batch win; vectors are unaffected (padding is
  masked out of pooling).
- **Sleep-proof.** `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` for the life
  of the pass, Windows-only, with the sleep timeline in the comment.

Smoke-tested before firing the real thing: a run with `--limit 1400` was killed mid-pass at
768/1055, the partial survived, the re-run printed `resuming a killed pass: 768/1055` and
embedded only the remainder. Then `Start-ScheduledTask ORACLE-OQ18-eval` at ~16:07. As of this
log it is at ~14% with checkpoints landing on disk and an ETA of roughly three hours — the
first supervisable version of this measurement.

**Corpus note for whoever collects it:** 17,953 chunks / 16,519 semantic — the post-recalibration
chunker (MAX_CHARS 1200) produces ~50% more chunks than the 11,727 recorded on 2026-08-25, so
"2.5–3 h" was optimistic; ~1.35 chunks/s under session load says ~3.4 h. Also, watch the
answer-key line in the final log: this run printed *"answer-key documents in the lexical
candidates of 0/38 queries"* where 2026-08-26 measured 37/38 — either the fixtures file left
the corpus or the bigger corpus outranks it lexically now. Check `all 38 fixture sources
present` before trusting the recall numbers.

---

## 2. OQ-24: measured, missed, and the miss applied

`scripts/measure_observation.py` (new) times `observe()` — the daemon's exact stack: registry →
policy gate → toolhost — over the 8 real rows, cold and warm, deliberately **under** the OQ-18
load so the number is an upper bound. Full table in the script's output and OQ-24's resolution;
the shape of it:

- full 8-row fan-out: **1,654–2,673 ms warm** — 2–3× over the 1 s budget;
- per row ~150–600 ms, dominated by the two toolhost IPC round trips (~176–310 ms each under
  load vs 27.9 ms idle p50); git itself is single-digit ms;
- **the toolhost serialises invocations**, so an eager fan-out queues behind real work — the
  strongest argument against it, and one the arithmetic could never have produced;
- variance under load is ±40% pass to pass; one warm pass came out slower than cold.

Applied as the question itself prescribed: the sidebar observes **the selected row only**
(`GET /api/v1/projects/{id}` on selection and on task events), renders `⎇ main ↑3 ↓1 ~2`,
caches nothing. The list endpoint still runs no git and its pinning test is untouched. One
honest caveat: the "Source2DemViewer-sized repo" scenario OQ-24 asked for does not exist —
that directory has 3,893 files in `target/` and no `.git`, so it measures the non-repo path.

---

## 3. OQ-25: the eleventh label made accuracy better, and the slot failure has a name

Four `continue` cases added to `tests/fixtures/intent/cases.yaml` (pinning the **slot**, per the
live evidence), then the eval, now runnable as documented because `make eval` finally exists:

```
intent accuracy   97.1% (33/34)  — was 93.3% at ten labels
continue          4/4 label AND slot (Asterim, GameRecs, Asterim, honest null)
confusion         run -> continue ×1: "собери GameRecs" — the feared boundary, in reverse
latency           p50 2611 ms — POLLUTED by the OQ-18 load; not a measurement of anything
```

The four project-slot misses (30/34) decompose into: the two pre-routed-by-construction cases
(a scoring artefact carried since OQ-01), and **two cases of few-shot proximity beating slot
extraction** — `en-delegate-refactor` and `en-question-doc` are each nearly identical to a
few-shot carrying `project: null`, and the model reproduces the example's null over the
`Asterim` present in the actual sentence. At 0.8B, a matching example outweighs the instruction.

**The dead end, recorded because it is one.** The live-run failure (`continue ORACLE` →
`project: null` twice) reproduced deterministically: 9/9 null with `ORACLE` in the registry,
while `continue Asterim` resolved 3/3 in the same session. The model will not emit `ORACLE` as
a project value — the token is saturated as a company/common noun. One boundary line added
under `project:` in `_SYSTEM` ("a word matching a known project IS that project, even when it
also means something else") measured **6/6 still-null** and was reverted. Prompt instruction
does not beat that prior at this scale; `_named_project` — deterministic code — carries the
case, which is the architecture's own rule doing its job. Options if it ever matters more: a
deterministic `continue <registered-name>` pre-route (also saves ~2.6 s of model latency), or
renaming the row. Not more prompt work.

---

## 4. P11-T5: the centre stage becomes a mechanism instead of four accidents

The shape before: a `Stage` union, three bespoke header toggles each with its own flip-back
rule (one inconsistent — Events toggled on `stage === "chat"`, so pressing it from Memory went
to Events while Memory's button went home), a briefing auto-switch guarded by a ref, a
`setStage("chat")` buried in an ack callback, and `TaskTree` mounted above *every* stage —
invisible only because `tasks` is 0 rows.

Now:

- **`ViewTabs`** — a real tablist (roles, `aria-selected`, roving tabindex, arrow keys with
  automatic activation) over the six stages that exist: Chat · Tasks · Events · Memory ·
  Briefing · Knowledge. The briefing tab carries the attention dot (`.attn` finally has CSS —
  T4 shipped the class with no rule, a defect nobody saw because nobody could).
- **`Ctrl+1..4`** on the first four, with an AltGr guard (`Ctrl+Alt+digit` types characters on
  some layouts). UI.md §16's original `Orbit/Chat/Timeline/Tasks` assignment was corrected in
  place with the reason: two of those views do not exist, and the tab over the event table
  says **Events** because calling it Timeline would claim §7's grouped view.
- **Tasks is a stage** with a stated empty state; `ConfirmationCenter` and `DelegationPanel`
  deliberately stay above the switched panel on every stage — approvals expire in 180 s, and a
  card behind a tab is a card that expires unseen.
- **The inspector grew its task branch** — the P12-T4 stopgap (`onInspect` pushing a task id
  into the turn selector, where it matched nothing and the inspector silently showed the most
  recent turn) is gone. Selection is one model app-wide: `{kind: turn|task, id}`. The task
  section renders objective verbatim, ORCHESTRATION §2 status words (imported from TaskTree so
  there is one copy), attempt/lineage/dependencies, cost only where measured, and **ORACLE
  MEASURED** apart from **THE WORKER SAID**. A task id that outlived the store's five held
  graphs says so instead of showing the wrong thing.
- **`KnowledgeHealth` is mounted** (Knowledge stage) after being built, tested and imported by
  nothing since P9. Its reindex button now has a wire: `POST /api/v1/knowledge/reindex` (new,
  subagent-built), which crosses the policy gate via `executor.execute("know.reindex", ...)`
  — T1, so no card — and reflects the executor's outcome honestly. API.md updated; the
  per-collection form stays PLANNED. **The running daemon predates the route** — it goes live
  on the next daemon restart; until then the button 404s against the live process while the
  API tests cover it fully.
- **Submitting a message auto-switches to Chat** — §2's rule, §21 rule 6's one permitted
  automatic change, and every caller of `submit` is the user (composer, palette, sidebar).

Verified three ways: 305 vitest (was 277 — ViewTabs, stage keybindings incl. the AltGr guard,
the briefing→task-inspector pin, task-branch rendering, lazy-observation rendering, and axe
cases for the audit's three uncovered components: `Inspector`, `Citations`, `EgressPreview` —
the audit now covers 15/15 plus `ViewTabs`); `tsc` strict; and live against the running daemon
via the dev server — the briefing auto-switched on first paint showing the **real** crash line
("ORACLE stopped unexpectedly and restarted at 1:05:42 AM"), Ctrl+2/Ctrl+3 switched stages in
a real browser, and the Knowledge stage rendered the real index (bge-m3, 15,271 chunks,
147 MB, `projects` last indexed 4:25 PM — the watcher indexing this session's own edits).

One axe finding worth keeping: a standalone `ViewTabs` render fails `aria-valid-attr-value`
because `aria-controls` points at the panel that lives in the shell — the audit renders the
real pair, which is the rule the a11y file already states ("the shape the app actually
produces").

---

## 5. Debts paid, and one operational trap for the next session

- **`pytest-timeout` 2.4.0** — `timeout = 120` per test, thread method. A hang under a loaded
  gate becomes a named failure instead of a stuck bisect. TECH_STACK §11 justified.
- **`make eval` exists**; TESTING.md §8 stops documenting `make perf` (deliberately absent
  until a perf suite exists) and §6's confession shrank accordingly.
- **The trap:** the resident daemon holds `.venv\Scripts\oracled.exe`, so any *syncing*
  `uv` command fails with `os error 32` while it runs. `uv add` had to be split into
  `uv add --no-sync` + `uv pip install`; the gate ran as its seven exact steps with
  `--no-sync` appended. The next plain `uv sync` after the daemon stops reconciles the
  entry-point exe (content is identical; only dev deps changed).

## What did not get done, on purpose

- The knowledge reindex itself (57% oversized chunks, defect #8): a synchronous ~1 h rebuild
  would have contended with the OQ-18 pass all afternoon. The button and endpoint now exist;
  fire it when the corpus run is collected.
- P12-T5's human click, P13, and everything gated on `tasks` > 0 — a person's, per the task
  file.
- The palette combobox roles (defect #10) — still open; it is CommandPalette surgery and this
  arc was already wide.
