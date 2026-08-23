# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P5-T2 — closed 2026-08-23 except one automated item; **P6-T1 is set** and is the active task.
**Status:** Phase 5 is built, measured, gated. The single open item — the `bge-m3` decision — has its
measurement scheduled and closes itself.
**Date:** 2026-08-23

---

## What was decided today (owner, 2026-08-23)

1. **tree-sitter stays off.** Built, tested, one constant from on; the expanded fixture set decides
   later. `chunking.SYNTAX_AWARE = False` ships.
2. **The indexing budget is confirmed as written** in ROADMAP.md: cold ≤ 60 min · warm < 2 min ·
   after a chunking change ≤ 25 min · incremental < 5 s. All measured, none guessed.
3. **The `bge-m3` full-corpus run is authorised** and scheduled: one-time task
   `bge-m3-full-corpus-run`, 2026-08-24 05:00 local, ~2.5 h, into a separate DB — the shipped index
   and the e5-base cache are untouched. It writes a dev log and updates OQ-02; the final model choice
   stays with the owner. *(The desktop app must be running at 05:00, or the task fires on next launch.)*
4. **Sequencing rule 1 waived, narrowly**, to start P6-T1 now: the one remaining P5-T2 criterion is
   the recorded bge-m3 decision, and nothing in P6 depends on which embedding model ships — the
   `know.*` interface does not change with the `ModelSpec`.
5. **The owner's three-tier LM Studio model stack is recorded** in ROADMAP.md ("Idea backlog"):
   Qwen 2.5 3B light / Qwen3 14B default / Qwen3 27B heavy, DeepSeek-harness browser search as a
   feasibility spike, and maximally explicit prompts to local models as a requirement enforced by the
   fixture suites. Unscheduled; items 1–2 would amend Phase 1 when taken up.

## Where P5 landed

| | |
|---|---|
| Embedding cache | Cold rebuild 42.8 min → warm **37 s**, zero forward passes. |
| tree-sitter | Built and **off** — better anchors, measurably worse recall (71–76% vs 81% on 21 cases). |
| Watcher | Under the daemon, HALT-aware; found and fixed a 3.1× filter defect (`fnmatch` → compiled patterns). |
| PDF | Text layer via `pypdfium2`, page anchors, `local_foreign`; costs nothing in recall. |
| Russian fixtures | 8 → **25** (38 total), ground truth read from files. `e5-base`: **55% overall, 36% Russian** — the old set had overstated Russian by 26 points. OQ-02 reopened; the bge-m3 run is the answer. |
| Fusion | Denominator bug fixed (per-script document frequency); ranks better, recall unmoved — the model, not the fusion, is the lever. |

**Gate:** green 2026-08-23 — ruff, mypy, tsc, pytest, security, vitest. Branch `phase5-knowledge`,
docs-only changes uncommitted at the time of writing.

## The active task

**P6-T1 — The adapter, the packet, and the fallback: delegation without a UI.** See
[current_task.md](current_task.md): recorded stream-json contract fixtures, `ExternalAgentAdapter` +
`ClaudeCodeAdapter` against a stub CLI, the Handoff Packet builder with asserted redaction and a 30k
ceiling, worktree isolation, and the packet-on-disk fallback. No egress without the owner seeing the
payload; the preview UI and MCP server are P6-T2+.

Logs: [tree-sitter](../logs/development/2026-08-22-treesitter-chunking.md) ·
[watcher](../logs/development/2026-08-22-watcher-daemon.md) ·
[PDF](../logs/development/2026-08-22-pdf.md) ·
[fusion denominator](../logs/development/2026-08-22-fusion-denominator.md) ·
[embedding cache](../logs/development/2026-08-22-embedding-cache.md)
