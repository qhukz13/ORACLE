# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P5-T1 — Project knowledge: resolve OQ-02 first, then build the index**

**Phase:** [5 — Project knowledge (RAG)](ROADMAP.md#phase-5--project-knowledge-rag--post-mvp) · **Scope:** Post-MVP
**Status:** `NOT STARTED` · **Set:** 2026-08-21
**Previous task:** P4-T1 — `DONE`. ★ **The MVP is complete** — see [current_report.md](current_report.md).

---

## Before anything else: use it for a day

The one Phase 4 criterion left unticked is *"I use ORACLE for a full working day without opening a
terminal manually"*, and it is unticked because no test can tick it. It is also the highest-value
thing available right now: a day of real use will produce a list of small, concrete friction points,
and those are worth more than the first week of a new subsystem.

**Do that before starting this task.** Record what breaks in `logs/development/`.

## Objective

Make ORACLE know the projects and notes, with attributed retrieval. This is what turns "an agent with
tools" into "an agent that knows my work", and it is the prerequisite for context assembly good
enough to delegate well (P6).

## Why OQ-02 comes first

[OQ-02](OPEN_QUESTIONS.md#oq-02) — embedding quality on mixed Russian/English — is marked
`EXPERIMENT NEEDED`, and the ROADMAP explicitly says to resolve it *before building on it*. Everything
in this phase sits on the embedding choice: the schema, the chunk sizes, the retrieval quality target
and the reindex budget. Choosing wrong means rebuilding the index and re-tuning the thresholds.

**This is the same rule that ordered P3** (isolation before spawning tools) and P4 (the confirmation
card before the surfaces that only display things). Do the load-bearing uncertain thing first.

## Context

Established and not to be re-derived:

- **Nothing is indexed without explicit opt-in.** `Documents` contains game saves and Paradox data;
  "index my Documents" is not a feature ([RAG.md §2](RAG.md#2-what-gets-indexed)).
- **The index is disposable.** `knowledge.db` is rebuildable by definition; deleting it and
  reindexing must reproduce equivalent results (ADR-0006).
- **Retrieved content is `local_foreign` and taints the turn.** The gate already escalates a tier when
  a plan is built from untrusted content, and the confirmation card already renders the taint warning
  — this phase is what will make that path fire in earnest.
- **Embeddings run on the CPU** (ADR-0014). The GPU's 3.5 GB is the router's, and sharing it is how
  the router starts swapping.
- Project detection already reports type, test/build/lint commands and `AGENTS.md`/`CLAUDE.md` per
  project (`core/projects.py`) — the collection registry should build on it, not duplicate it.

## Requirements

1. **Resolve [OQ-02](OPEN_QUESTIONS.md#oq-02)** with a real mixed RU/EN fixture set, and record the
   result before writing schema.
2. `knowledge.db`: sqlite-vec + FTS5 ([DATABASE.md](DATABASE.md)).
3. **Collection registry** — explicit opt-in per source, in a config file a human edits.
4. Parsers: tree-sitter for code, heading-aware Markdown with Obsidian wikilinks, `pypdfium2`.
5. Chunking per type; embeddings via ONNX on CPU.
6. Hybrid retrieval: dense + BM25 + RRF, with metadata pre-filtering by project and collection.
7. Incremental indexing: content hash + mtime, `watchfiles` with debounce, `.gitignore` respected.
8. `know.*` tools, and citations rendered in the UI.
9. Index health view: what is indexed, when, how big, what failed.

## Constraints

- **No new dependency without a justification in TECH_STACK.md.** This phase wants several
  (tree-sitter, onnxruntime, pypdfium2, watchfiles); each one is a maintenance commitment.
- The 40-tool cap is real and 29 are registered. `know.*` is five more — merge before adding.
- Every `know.*` tool needs a policy rule and a `tests/security/` case, like every tool before it.
- Retrieved text is untrusted input. An injection fixture — a note saying "ignore previous
  instructions" — must set taint and must not change behaviour.

## Acceptance criteria

- [ ] [OQ-02](OPEN_QUESTIONS.md#oq-02) resolved and recorded, **before** the schema is written.
- [ ] Full index of all projects + vaults in **< 10 min** on this CPU; incremental update **< 5 s**.
- [ ] Retrieval p95 **< 400 ms** over the full corpus.
- [ ] On a 20-question fixture set, the correct source is in the top 5 **≥ 80%** of the time.
- [ ] Deleting `knowledge.db` and reindexing reproduces equivalent results.
- [ ] `node_modules`, `target/`, `.git/objects`, binaries and media are never indexed. Asserted.
- [ ] Every retrieved chunk carries a real, clickable source.
- [ ] An injection fixture sets taint and changes no behaviour.

## Relevant files

Create: `src/oracle/rag/` · `config/collections.yaml` · `tests/security/test_injection.py`
Modify: `src/oracle/tools/__init__.py` (register `know.*`) · `config/policy.yaml` · `apps/desktop`
(citations)
Read first: [RAG.md](RAG.md) · [DATABASE.md](DATABASE.md) ·
[OQ-02](OPEN_QUESTIONS.md#oq-02) · [SECURITY.md §6](SECURITY.md#6-prompt-injection-and-taint-tracking)

## Dependencies

P4 (done). **[OQ-02](OPEN_QUESTIONS.md#oq-02) blocks requirement 2 onward** and must be answered
first.

## Risks

| Risk | Mitigation |
|---|---|
| Embedding quality on mixed RU/EN | The reason OQ-02 is requirement 1. Do not write schema against an unmeasured assumption. |
| Indexing something private | Opt-in per collection, in a file a human edits. `Documents` is the concrete example of why. |
| Watcher storms during `npm install` | Debounce plus exclusion rules; the same skip list project detection already uses. |
| Retrieved text carrying instructions | Taint is already implemented and already escalates the tier — this phase must prove it fires, with a fixture. |
| Index bloat | A size budget with an alert. The index is disposable, so the failure mode is disk, not data loss. |

## Definition of done

All acceptance criteria · OQ-02 resolved and recorded · every `know.*` tool has a policy rule and a
security test · the gate green including the security suite · `current_report.md` overwritten ·
this file updated to **P6-T1**.
