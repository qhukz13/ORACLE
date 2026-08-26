# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** **P12-T2 — the `continue` intent and unfinished-work derivation.** Plus P12-T1 (the
project entity) and the vision realignment, all the same day.
**Status:** Done, with **one acceptance item knowingly outstanding** — the intent eval was not
re-run ([OQ-25](OPEN_QUESTIONS.md#oq-25)).
**Date:** 2026-08-26 · **1,184 Python tests**, up 51 on T1 and 113 on the morning.
**Dev logs:** [T2](../logs/development/2026-08-26-p12t2-continue-intent.md) ·
[T1](../logs/development/2026-08-26-p12t1-project-entity.md) ·
[vision realignment](../logs/development/2026-08-26-vision-realignment.md)

---

## What shipped

*"Continue Asterim"* now resolves a project, reads what is actually left, and hands a planner an
objective built from evidence — or asks, when there is nothing to build one from.

| | |
|---|---|
| `core/unfinished.py` | the derivation: open tasks, repo task documents, and the objective renderer |
| `IntentLabel` | an eleventh member, `continue`, with a stated boundary and four few-shots |
| `TurnPipeline.continue_work` | a hook, like `run_pipeline` — the router resolves the project and hands off; it does not decide the work |
| `_continue_project` | the daemon side: register on first use, derive, ask or plan |
| `approve_graph(untrusted_sources=…)` | the card names the files whose text is inside the objective |
| `continue.derived` | a new **critical** event: how many open tasks, which files were quoted |
| Migration `0006` | fixes a latent detonator in `0005` — see below |

**The ordering is the design.** ORACLE's own task graph is authoritative; the repository's task
documents are evidence; and **nothing is the third answer, not a guess**. `objective_of()` returns
`None` for an empty derivation and the caller asks — because a planner handed a project name and
nothing else produces plausible work, and plausible work is unfalsifiable.

---

## Migration 0006 — the most useful thing found today

Found by a test that was trying to assert something else.

T1's generated column used `json_extract(spec, '$.project')`. **`json_extract` raises on malformed
JSON** rather than returning NULL, and the column is indexed — so the blast radius is the whole
table. On any database already holding one task row with an unparseable `spec`, migration 0005
would have **failed at `CREATE INDEX`**; past that, every read of the column would raise: the
counter rebuild, the unfinished-work query, the whole projects surface.

Same shape as the dead collection root that disabled live re-indexing for *every* collection with
one absent path. It did not bite — `tasks` was 0 rows and `TaskStore.save()` only writes valid
JSON — which is precisely why it was worth fixing before the conditions arrived unannounced.

`json_valid()` guards it; a malformed row is now simply unattributed. **0005 was not edited** — an
applied migration is a historical fact, and editing one leaves every database that ran it
disagreeing with the file describing it.

---

## Taint buys attribution, not escalation

The plan was for repo notes to raise the confirmation tier. Reading `approve_graph` showed that
would be **theatre**: the graph card already evaluates as `Provenance.EXTERNAL` at T2, because
ADR-0021 treats every plan as untrusted. There is no further escalation to give.

What was missing was provenance, not severity. The card now names the files. My own acceptance
criterion had said "escalates the tier by exactly one"; it was wrong, and the honest fix was to
change the criterion rather than add an escalation that does nothing so the sentence could stay
true.

---

## The injection surface, fenced three times

Reading someone's `TODO.md` into a planner prompt is a prompt-injection channel **by
construction**. So: **scope** (through `fs.read`, so the policy engine resolves the path — a
project outside every scope cannot be read by asking ORACLE to continue it), **framing** (quoted
inside a fence named after the file, under an untrusted heading, with ORACLE's own record first —
order is a defence), and **authority** (the plan is still validated, still cannot name its own
executor, still reaches a card that names the file).

`tests/security/test_continue_evidence.py` runs five payloads through the renderer, including one
that forges the closing fence, and uses a real `PolicyEngine` for the scope and traversal cases.

---

## The eval was not re-run — deferred, not dropped

At the owner's direction. Intent accuracy (**93.3%**, 30 fixtures, ten labels) has not been
re-measured against eleven. [OQ-25](OPEN_QUESTIONS.md#oq-25) records the risk — *"run the Asterim
tests"* and *"continue Asterim"* are one word apart to a 0.8B classifier — and what shipped
instead: the prompt states the boundary rather than leaving it inferable, four few-shots cover it
including a Russian one, and a test pins both so a future edit cannot quietly delete the
mitigation. A wrong route here is recoverable, which is why it blocks nothing.

Noted while writing it up: **`make eval` is documented in TESTING.md §8 and defined nowhere**, so
OQ-25's documented resolution path does not currently exist.

---

## Two smaller lessons

**A source-inspection test that matched on text asserted nothing after a reformat.** It grepped for
`execute("`; `ruff format` wrapped the call across two lines. Rewritten over the AST.

**A fake that invented a file.** `_FakeExecutor` matched paths by suffix, so `docs/TODO.md` was
served the body registered for `TODO.md`. A fixture that invents data is worse than none, because
it looks like coverage.

---

## Next

**[P12-T3](current_task.md) — the briefing.** The resume pointer already exists and already
advances monotonically; this builds what reads it. Deterministic prose only: counts and outcomes
are arithmetic over task rows, with no model on the path and no possibility of a hallucinated
summary of your own work.

**And now genuinely runnable by a person: P12-T5, the first real `continue`.** T1 and T2 close the
loop for the first time — *"continue ORACLE"* will resolve this project, read its real open tasks
and this repository's own `docs/current_task.md`, and ask before planning. It needs **Ollama
running** (there is no slash-command bypass for `continue`) and it asks twice. It is the run that
finally puts rows in `tasks`, which is what P11's orbit, timeline and queue are all waiting on.
