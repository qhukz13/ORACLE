# 2026-08-24 — P6-T5: the Antigravity adapter, and the planning spike

Two questions, one task. Can ORACLE drive `agy` honestly (the adapter), and can `agy` return a
plan ORACLE can execute (the spike, [OQ-20](../../docs/OPEN_QUESTIONS.md#oq-20))?

Everything below was measured on this machine on 2026-08-24. Fixtures:
`tests/fixtures/agents/antigravity/`. Ledger: `logs/integrations/agy-planning-ledger.jsonl`.

---

## Part 1 — recording the contract

`scripts/record_agy_stream.py`, four steps, each an approved egress. Fixtures before function,
the same discipline `record_claude_stream.py` set for Claude.

### The CLI updated itself mid-session

The first fixture set recorded on **v1.1.17**. Forty minutes later, `agy --version` reported
**v1.1.19**. Nothing was asked to update; the binary self-updates and leaves an `agy.exe.*.old`
behind. The v1.1.17 fixtures were deleted and everything re-recorded on v1.1.19 rather than
mixing two contracts in one test suite.

**Consequence:** INTEGRATIONS.md's "re-verify quarterly" is a floor, not a schedule. A vendor CLI
can drift between two runs of the same script on the same afternoon. The fixture filenames carry
the version for exactly this reason; keep it that way.

### Finding 1 — headless `agy` is read-only, and that is load-bearing

ORACLE does not pass `--dangerously-skip-permissions` (Asterim does; INTEGRATIONS.md §5 explains
why ORACLE will not). The measured consequence:

| tool | outcome |
|---|---|
| `view_file`, `find_by_name` | run unprompted |
| `write_to_file` | soft-denied: `permission check failed … user denied permission` |
| `run_command` (the model's retry) | soft-denied the same way |

The run then ends `status: ERROR`, **exit code 1**. The trivial "count the words and write the
number to a file" task therefore *cannot succeed* under ORACLE's posture.

That reads like a defect and is not. Every role the capability registry gives Antigravity —
`planner`, `reviewer`, `researcher` (PLANNER.md §4) — is read-only. The finding to write down is
the boundary itself:

> **Antigravity can hold `planner`, `reviewer` and `researcher`. It can never hold `coder`.**

The adapter logs a warning when a packet asks for write tools, because the CLI has no allow-list
flag to express the request in and a silent downgrade would later look like a model failure.

### Finding 2 — cancellation, timed

Two attempts measured nothing before the third worked, and both dead ends are worth keeping:

1. **Asked for a file.** The run died on the permission gate before the interrupt arrived.
2. **Asked for one long response** (a 1000-word analysis). The vendor emitted
   `status: ERROR`, `error: "timeout waiting for response"` at ~20 s, unprompted. A long single
   generation is its own failure mode — noted, because a 12-task plan is a long generation.
3. **Asked for many short read-only steps**, and *timestamped every stdout line* — which is what
   finally answered the question. Without timestamps the fixture cannot say whether `result`
   arrived because of the signal or before it, and attempts 1–2 were misread for that reason.

The timeline (`cancel-v1.1.19.timing.txt`):

```
   4.68s  {"event":"init", …}
  12.00s  ← CTRL_BREAK sent
  12.11s  {"event":"result","result":{"status":"ERROR","error":"timeout waiting for response", …}}
  12.15s  child exits, code 1
```

**Semantics:** interrupt alone suffices — terminate and kill were never reached. Nothing is left
in the workspace (the vendor writes only through tools, and tools are gated). The status is
`ERROR` with a message about a timeout — **never** the documented `CANCELED`/`INTERRUPTED`.

So a cancelled run and a genuine vendor timeout are **indistinguishable from the stream alone**.
Only ORACLE's own record of having sent the signal separates them. The adapter therefore never
infers cancellation from the stream, and `INTEGRATIONS.md §5` says so.

### Finding 3 — `--json-schema` returns a filtered field, not prose

The `result` body carries `structured_output` **already filtered to the requested schema**, beside
a `json_schema` echo. The raw `response` string carried two extra vendor keys the schema never
asked for:

```json
"response": "{\"first_word\":\"the\",\"toolAction\":\"Completing the task\",\"toolSummary\":\"Finish task\",\"word_count\":9}"
"structured_output": {"first_word": "the", "word_count": 9}
```

The adapter reads `structured_output`. Anything that parses the prose would inherit vendor fields
that are not part of any contract.

### What could not be observed — the unauthenticated state

`preflight()` must distinguish three states. Two were observed for real; the third was not.

Redirecting `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA` and `XDG_CONFIG_HOME` to an empty
directory **did not deauthenticate `agy`** — it ran the task normally. Its credentials come from
somewhere else. The Antigravity IDE was running throughout (a plausible source, an untested
hypothesis). Inspecting the binary for credential-path or environment-variable strings was
attempted and blocked by this session's tooling; it was not pursued further, and the real
configuration was never touched.

A dead end inside the dead end, worth recording because it nearly became a false result: the first
version of the probe searched the failure text for the substring `"auth"` — and matched the
workdir path `…\record-agy\unauth\count.txt` inside an unrelated permission error. It reported the
state as **observed**. A probe that can pass by accident is worse than no probe; the word list is
now `authenticat`/`unauthorized`/`log in`/`sign in`/`credential`/`401`, and the directory was
renamed.

`preflight()`'s auth probe is `agy models` — a real vendor round trip that costs **no model
tokens**, which matters because every `-p` call costs ~15k input tokens before the model reads a
word. The unauthenticated branch is written from the vendor's documented behaviour and marked
`ASSUMPTION` in the adapter. **Do not infer it from a green preflight here.**

### What the adapter refuses to do

`agy` has no `--mcp-config`: its MCP servers are global config edited by `agy mcp`. So ORACLE's
guarded tools cannot be lent to a single `agy` run. Honouring a packet's `mcp_config` would mean
mutating machine state on behalf of one delegation; ignoring it would mean running a delegate that
believes it holds ORACLE's tools and silently does not. `command()` raises instead, and the packet
routes to Claude. A test pins it.

---

## Part 2 — the planning measurement

`scripts/verify_agy_planning.py`. Four real objectives (ORACLE's own Phase 7 task graph, Phase 9
memory, the `permission_denials` gap the Asterim audit found, and worktree scrub hardening) ×
`--effort low|high` × 2 repeats = **16 calls**, each driving the *real* `AntigravityAdapter` with
a JSON schema generated from a local draft of PLANNER.md §2's `ExecutionPlan`.

Sixteen, not the twenty the task asked for: the pilot measured **55.6k tokens per plan**, four
times the pre-run estimate, and the owner trimmed the grid at that point. So **OQ-20 is narrowed
with numbers, not closed at the stated sample size** — and this paragraph exists so nobody later
reads 16 as 20.

### The verdict

```
calls                16
valid first attempt  12/16 = 75%     GATE RED  (gate 90%)

low    7/8 valid   median 27.1s   median 50,571 tokens
high   5/8 valid   median 42.9s   median 64,492 tokens

cost   median 55,234 tokens/plan, 954,947 tokens across 16 calls
```

Every call (the ledger itself is `logs/**`, which git does not track — so it is transcribed here):

| objective / repeat | effort | s | tokens | tasks | edges | verdict |
|---|---|---|---|---|---|---|
| taskgraph/0 | low | 28 | 55,637 | 6 | 0 | valid |
| taskgraph/1 | low | 30 | 54,832 | 5 | 0 | valid |
| taskgraph/0 | high | 44 | 70,700 | 6 | 0 | valid |
| taskgraph/1 | high | 46 | 47,996 | — | — | **run failed** — browsed, denied |
| memory/0 | low | 27 | 51,855 | 10 | 11 | valid |
| memory/1 | low | 29 | 52,270 | 8 | 11 | valid |
| memory/0 | high | 60 | 93,058 | — | — | **run failed** — browsed, denied |
| memory/1 | high | 28 | 63,452 | — | — | **run failed** — browsed, denied |
| permission-denials/0 | low | 23 | 32,866 | 0 | 0 | **invalid** — tasks silently emptied |
| permission-denials/1 | low | 24 | 48,153 | 5 | 4 | valid |
| permission-denials/0 | high | 33 | 59,083 | 5 | 4 | valid |
| permission-denials/1 | high | 41 | 65,531 | 6 | 5 | valid |
| worktree-scrub/0 | low | 28 | 49,287 | 6 | 6 | valid |
| worktree-scrub/1 | low | 26 | 45,567 | 5 | 0 | valid |
| worktree-scrub/0 | high | 35 | 59,335 | 5 | 0 | valid |
| worktree-scrub/1 | high | 66 | 105,325 | 5 | 5 | valid |

### Finding 4 — the planner browses, and the permission gate is what stops it

**All three hard failures were `--effort high`, and all three were the same failure**: the planner
went looking around the filesystem and hit the vendor's permission gate.

```
permission check failed for read_file "C:\Users\qhukz": user denied permission
permission check failed for read_file "C:\Users\qhukz\Claude\Scheduled\oracle-mvp-watch\SKILL.md"
```

The workspace it was given was an **empty temp directory**. It reached for the owner's home
directory anyway, and in one case for a named personal file well outside anything relevant to the
objective. The denial then ended the run, which is why these count against conformance.

Three consequences, in increasing order of importance:

1. **PLANNER.md §7 says the planner "does not get the MCP tool surface" and is not meant to
   explore.** That is not enforceable through this CLI: `agy` ships its own tools and uses them,
   and there is no "no tools" flag. What actually holds it back is the vendor's permission gate
   plus `--add-dir` — not our intent. §7 should say so.
2. **`--effort high` is worse for planning, not better** — slower (43s vs 27s median), ~27% more
   expensive, and empirically the only setting that failed. If Antigravity ever holds this role,
   the registry pins `low`.
3. **Refusing `--dangerously-skip-permissions` paid for itself here, in one afternoon.** Asterim
   passes that flag. Under it, these three calls would have read the owner's home directory, and
   what they read would have gone to the vendor as context. The posture recorded in
   INTEGRATIONS.md §5 and the analysis in SECURITY.md §10 stopped it, with nobody needing to
   anticipate this specific behaviour. That is the single most valuable thing the spike found,
   and it is not about planning at all.

### Finding 5 — `structured_output` can silently return an empty collection

One call reported `status: SUCCESS`, and its `structured_output` validated against the schema —
with `tasks: []`. The raw `response` string beside it held a **complete, well-formed six-task
plan**. Those tasks used `description` where the schema requires `objective`, so the vendor's
filter appears to have dropped each non-conforming item **silently** rather than failing the call.

> A schema-shaped answer is not a validated answer. `structured_output` proves the *shape*; it
> does not prove that anything survived the filter.

ORACLE caught it only because validation check #1 is "no tasks" — the plan-repair pattern earning
its keep on its first real outing. Two things follow: keep an emptiness check on every collection
a plan may return, and note that a **tolerant parse of the raw `response`** (Asterim's `parse.js`
lesson, ASTERIM_REUSE.md Tier 1) would have recovered a *better* plan than the structured field
did. That is a fallback worth having before Phase 8, not after.

### Finding 6 — valid is not the same as schedulable

Conformance was never the whole question, and the richness counters say so:

| property | result |
|---|---|
| plans declaring **any** dependency | **7 of 12** |
| tasks with `project` set | 45 / 72 |
| tasks with `context_hints` | 45 / 72 |
| tasks with `agent_hint` | 45 / 72 |
| risks listed | 9 across 12 plans |

Five valid plans were **DAGs with no edges** — five or six tasks the scheduler would fire
simultaneously, including ones that plainly must be sequential ("define the schema" and
"implement the repository over that schema"). The `taskgraph` objective produced zero edges in all
three of its valid plans; `memory` produced 11 edges twice. The planner *can* express dependencies
and does not do it reliably.

The acceptance criteria have the same flavour: `python -m pytest tests/test_scheduler.py passes`
names a file that does not exist and would be created by the very task it is meant to verify. A
criterion a worker can satisfy by writing the test it is judged by is not verification. ORACLE's
own diff-and-tests verification is what makes this survivable — the design working — but it means
plan acceptance criteria cannot be trusted as the verification contract.

### The answer to OQ-20

**No, at the stated gate.** 75% valid-on-first-attempt against a 90% bar; 87.5% (7/8) if the
registry pins `--effort low`, which is still short and rests on eight samples. Cost is ~55k tokens
and ~27s per plan at low effort.

**The ladder promotes** (PLANNER.md §6): Claude authors plans against the same schema and the same
validation; Antigravity keeps `reviewer` and `researcher`, where its ~15k-token prompt overhead
amortises and nothing depends on a graph coming back well-formed. This changes one line of
`config/agents.yaml`'s design, not the architecture — which is exactly why the ladder was designed
before the spike ran.

**What would change the answer**, in the order worth trying: pin `--effort low`; add the single
repair round trip the plan-repair pattern already specifies (though three of four failures are a
*run* failure rather than a schema failure, so repair helps only the fourth); add the tolerant
parse of `response` as a second source; and require dependencies explicitly in the prompt instead
of hoping for them.

---

## Part 3 — one plan-authored task, executed

`scripts/verify_plan_task_live.py`. The point of this run is one sentence: **a plan authored by one
vendor, executed by another, verified by ORACLE itself.** No new machinery — the task is lifted out
of the planning ledger and pushed through the delegation lifecycle that has existed since P6-T1.

The task, plan-local id `C` from `permission-denials/low/1` — one of the plans that *did* declare a
dependency chain (A→B→C→D→E):

> Update Claude adapter to parse permission denial events and surface them in normalized events
> and collected results.

### What happened

```
23:15:27  packet rendered      6 files, 632 tokens, 0 redactions
23:15:33  approval requested   ai.delegate, tier T2 - nothing had egressed yet
23:15:33  approved             worktree cut, scrubbed ['.claude/']
23:15:33  claude submitted     pid 4336
23:22:59  finished             exit 0, success
```

**Verified by ORACLE, not by the delegate's report:** 87 diff lines across
`src/oracle/integrations/{claude,types}.py`, plus three untracked files. The delegate's structured
claim (`files_changed`) matched the diff ORACLE read off the worktree.

The change itself is good, and one detail is worth noting because nobody asked for it: the delegate
added `PERMISSION_DENIED` to the event vocabulary, parsed the `permission_denials` entries
*defensively* because the CLI does not document their shape, and wrote in the module docstring that
its own fixture is **synthetic, not a recording** — "replace it the day a real denial is captured".
It inferred this repo's fixture discipline from the code and then flagged its own deviation from it.

### Finding 7 — a verifier without a baseline calls everything a failure

The first attempt of this run reported **"ORACLE ran the tests itself: ok"** while the tests had not
run at all: `dev.run_tests` refuses to spawn a process without a `ToolHost` (ADR-0003), the harness
recorded `ran: True` because the *tool call* returned, and the report printed a green line for a
verification that never happened. Fixed by distinguishing "the tool returned" from "the suite ran",
and by wiring the host — but it is exactly the false-green this project exists to avoid, and it was
in the verifier itself.

With verification actually running, the numbers looked bad and were not:

| worktree | passed | failed | skipped |
|---|---|---|---|
| the delegate's | 583 | 28 | 19 |
| **a clean worktree at the same base commit** | 578 | **28** | 19 |

**The same 28 tests fail in an untouched worktree.** They are environment failures — a fresh
worktree has no `.venv`, so the suites that spawn a binary die on `FileNotFoundError [WinError 2]`.
The delegate added five passing tests and broke nothing. Confirmed from the other direction the
same evening: `make check` is green in the real checkout, security suite included, so the 28 are a
property of worktrees and not of the code.

> **Verification is a delta, not a threshold.** A VERIFY task that reads "28 failures" as failure
> would reject every correct delegation this repo can produce. Phase 7's verifier must baseline the
> suite in a pristine worktree and compare — which also means the baseline is a cost every graph
> pays once, not per task.

That is the most useful thing this run produced, and it only appeared because the run was real.

### What the plan got wrong, and why it did not matter

The task's own acceptance criterion named `tests/adapters/test_claude.py` — a path that does not
exist in this repo (its tests live in `tests/test_integrations_claude.py`). The delegate duly
*created* `tests/adapters/`, satisfying a criterion by building the thing it was judged against.
ORACLE's diff-and-tests verification was unaffected, because it never consults the plan's criteria.

Which is Finding 6 restated as a rule for Phase 8: **plan acceptance criteria are a hint for the
worker, never the verification contract.**

### Finding 8 — a delegation's result exists only while its worktree does

The worktree was removed after the run. **The delegate's 87-line change went with it**, and the
branch `oracle/p6t5-permission-denials-c` still points at the base commit with an empty diff.

Nothing malfunctioned; the lifecycle simply has no step that keeps anything:

* the packet forbids git commands (ORACLE owns the worktree, and a delegate that commits has
  hidden its own diff — that reasoning stands);
* so the change lives in the working tree, uncommitted;
* `DelegationService` reads the diff, runs the tests, reports — and `discard()` deletes the
  worktree. There is no *harvest*.

For Phase 6's question — "did the delegate do the work?" — that was the correct design: the diff is
evidence, and evidence is read, not kept. For a **graph**, it is a hole: task C's output is task D's
input, and a result that evaporates when its worktree is cleaned cannot be handed to a dependent
task, re-reviewed after a failure, or merged after approval.

> **P7 needs a harvest step**: at collect time, commit the worktree's diff to the task's own branch
> (ORACLE commits, not the delegate — the ban stays), so the result outlives the workspace. Then
> `discard()` throws away a checkout instead of the work.

Written down here because it cost this run its artifact to notice, and because the fix belongs to
the task runner, not to a script.

### Disposition

Nothing merged, nothing kept: both worktrees removed, the branch retained but empty (above). The
change was real and is probably wanted — `permission_denials` is on the reuse list
([ASTERIM_REUSE.md](../../docs/ASTERIM_REUSE.md) Tier 1: "not currently surfaced by
`ClaudeCodeAdapter` and should be") — and it is cheap to reproduce now that the path is proven.
Doing it deliberately, as its own task with its own fixture recorded from a real denial, is better
than merging a spike's by-product built on a synthetic fixture.
