# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P9-T3 (the translator, measured) and **Phase 10 — Pipelines (built)**.
**Status:** Phase 10 is done, tested and documented. P9-T3 is **half done**: the translator is
measured and shipped, the corpus re-scoring is **scheduled for 2026-08-27 07:12** and OQ-18 stays
open until it lands.
**Date:** 2026-08-26

---

## Phase 10 — pipelines, built

A YAML file becomes a validated `Pipeline`, renders against its parameters, compiles to a task
graph and runs on P7's scheduler. **No pipeline executor, no `pipeline_runs` table, no new
`TaskKind`, no new runner**, and exactly two new event types — neither of them a `task.*`, because
the steps *are* ordinary tasks and `TaskTree` renders a run with no new code.

The roadmap's extra acceptance criterion is a passing test rather than a claim: a compiled pipeline
and a hand-written graph of the same steps emit identical event sequences, element for element,
with no event type unique to either.

Two shipped pipelines in [`config/pipelines/`](../config/pipelines/), both priced against the
**real** `config/policy.yaml` and the **real** tool registry by a test — so a renamed tool or an
argument a contract does not take fails a test rather than a run. The daemon boots, discovers both,
and reports them on `/api/v1/status`; the palette offers them by name.

### Five things the spec could not have known

All five are corrected **in PIPELINES.md**, not worked around in code. A spec that disagrees with
its implementation is worse than no spec: the next reader cannot tell which half to trust.

1. **A P7 defect.** `Limits.timeout_s[TaskKind.TOOL]` is **120 s**; `dev.run_tests` declares 630 s
   and `dev.build` 930 s. **Any TOOL task running either was killed at two minutes and recorded as
   `TIMEOUT`** — which reads as "the tests hung", not "the scheduler did not wait". True since P7-T2
   and invisible because the graphs built before now ran delegations (3600 s) and verifications
   (900 s). `Task.timeout_s` (migration `0004`) is the `task` level ORCHESTRATION.md §3 already
   specified. **It is a graph fix and should outlive pipelines.**
2. **The spec contradicted itself twice.** `on_failure: ask` appears nine lines after "never a
   prompt mid-run"; §2's example uses `retry: { on: [...] }` twenty lines before §3 says the tool
   contract decides retryability, not the author. Both refused, by not existing in the enum.
3. **Three things in the worked example do not exist**: a `project` tool argument (every tool takes
   a `ScopedPath`), `oracle.report`, and `capture: junit`.
4. **A policy entry that is a floor, not a price.** `pipe.run: T2` made PIPELINES.md's own
   `tier = max(tier(step))` rule unimplementable, because `evaluate()` only ever *raises* a tier.
   Found by a read-only pipeline hanging 180 s and returning `refused` — an approval nobody
   answered. It is `T0` now, with the reasoning in `policy.yaml`.
5. **Cancelling marks the rest `CANCELLED`, not `SKIPPED`.** My test asserted the spec and the
   scheduler was right: `SKIPPED` means *an ancestor failed*, `CANCELLED` means *a person stopped
   it*, and collapsing them loses the one distinction somebody reading a stopped run needs.

### The dangerous part

One card authorising six actions and one card nobody reads are the same gesture. Six of the 21
cases in `tests/security/test_pipeline_authority.py` constrain the grant-minting: bound to the
digest the card displayed, single-use, per task, T3 refused at validation, revoked in a `finally`.
A pipeline from `<project>/.oracle/pipelines/` is `local_foreign` — repository content, the same
trust class as a checked-in `AGENTS.md` — so the gate escalates it and the card says so.

---

## P9-T3 — the translator is measured; the corpus run is not

**Done, and it changes the design:** `qwen3.5:0.8b` translates a fixture question in **1.6 s p50**,
and **5 of the 25 came back still in Russian** — valid JSON, inside the length cap, and not a
translation. Unguarded that is the worst failure shape available: the second probe embeds the same
question twice, RRF fuses a ranking with itself, and every log line says it worked. The shipped
translator rejects an output that still carries the source script. Raw output is kept in
[`oq18-translations-unguarded.json`](../logs/measurements/oq18-translations-unguarded.json) so the
guard can be argued from a number.

A second measurement changed a constant: the same call took **19.7 s with the machine busy**
against a 20 s budget. A budget that fits only an idle machine is a feature that switches itself
off under load and says nothing. It is 45 s now — enough for warm-but-loaded, still refusing a cold
model load.

**Query translation ships on the Handoff Packet path only**, behind `Settings.translate_queries`.
`tests/test_rag_degradation.py` asserts the property that let it ship at all: no translator, a
refusal, a timeout, an empty string — every one thins retrieval to the native probe, so the worst
case of the mechanism is that it improves nothing.

### The finding that outlives the task

**The answer key was in the corpus.** `tests/fixtures/retrieval/cases.yaml` holds all 38 fixture
questions verbatim beside their expected paths, and `C:/Projects` contains ORACLE — so it is the
strongest lexical match for **37 of the 38 queries that measure this system**.

It was found and fixed on 2026-08-22 (commit `b660172`) **in `scripts/index_knowledge.py`**, and
`scripts/eval_embeddings.py` never got it — which is the script that produced **every number OQ-18
records**. Two copies of one idea and a fix reaching one of them: the shape of four of the five
instrument defects this project has found. There is one copy now, imported rather than restated.

So `en-relay-dockerfile` is **not** structurally unreachable, as P9-T2 recorded. It is at
unique-file rank 4, behind the fixture file and three ORACLE documents *about* the fixture file.
What actually sinks it is RRF's arithmetic, now pinned by `TestRrfBuriesASingleListDocument`: a
document one retriever cannot produce scores `1/61` and one both lists hold scores `1/61 + 1/62`,
so a config file — indexed lexically, never embedded — cannot survive fusion. That is a property of
the design, not a broken fixture, and weighting RRF to rescue one case would trade the property the
algorithm was chosen for.

### What is scheduled, and why it moved

The corpus run was killed at 2h45m and re-scheduled as a **Windows task, `ORACLE-OQ18-eval`, for
2026-08-27 07:12** (`StartWhenAvailable`, so it runs at next boot if the machine is off). It takes
~3 hours and writes `logs/measurements/oq18-translated.{txt,json}` and the reusable forward pass to
`D:/ORACLE/scratch/`.

Moving it was the better choice on the merits, not only for the CPU: the killed run loaded its
corpus at 11:12, and **Phase 10 rewrote four ORACLE documents that are in that corpus**. Its numbers
would have described a snapshot that no longer exists — and since OQ-18's live finding is precisely
that ORACLE's own documents contaminate the measurement, measuring a stale snapshot is worse than
measuring the committed state.

---

## A test that fails under load, and what that is worth knowing

`tests/security/test_terminal.py::…::test_a_long_burst_arrives_complete` lost 189 lines of a ConPTY
burst — twice — while the corpus run had all 24 threads saturated. It also failed at `HEAD` with
none of this work applied (verified in a clean `git worktree`), so it was never this branch's doing.
**On an idle machine it passes in 6 s, and the full gate is green.**

So: a flake, not a bug. But it is a flake in the *merge gate*, and it is the one test whose subject
is data loss — so "it only fails when the machine is busy" is an uncomfortable thing for it to mean.
Recorded in [current_task.md](current_task.md) rather than closed: if it reappears, the question is
whether the ConPTY reader really does drop output under starvation or whether the test's own
deadline is too tight, and those have different fixes.

`make check` is **green**: **1,061 Python** + **171 TypeScript**, security suite included.

## Next

**P11-T1** ([current_task.md](current_task.md)) — but read the two carried-over items there first;
one of them is a scheduled job that will have produced numbers by the time anybody reads this.
