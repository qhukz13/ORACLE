# 2026-08-24 — P6-T1: adapter, packet, worktree, fallback — the delegation core, offline

Requirements 2-5 of [P6-T1](../../docs/current_task.md), built in one sitting against the contract
recorded the same night ([auth log](2026-08-23-claude-auth-contract.md)). 22 new tests, all replay
or local git — zero network, zero vendor cost after the one supervised recording run.

## What landed

| | |
|---|---|
| `integrations/types.py` + `adapter.py` | ORACLE's event vocabulary and the `ExternalAgentAdapter` protocol, exactly as pinned in INTEGRATIONS.md §2. One addition to the vocabulary: `RETRYING`, because the vendor stream reports API retries and surfacing one as an error would cry wolf. |
| `integrations/claude.py` | Normalises stream-json at the boundary. The three recorded contract rules are load-bearing: finish on `result` (not stream end), wait for `init` (hooks precede it), skip unknown kinds (the vocabulary grew between two minor versions). |
| `handoff/packet.py` | The six files + `packet.json`. Redaction runs *before* rendering via the same `redact_text` as the log sink — entropy scanning on, because this text egresses. The 30k budget drops whole excerpts, lowest priority first, records the cut in CONTEXT.md, and **raises** when the task alone is over budget. |
| `integrations/workspace.py` | Worktree + **scrub** (delete `.claude/`, `.mcp.json` from the disposable copy — no opt-out parameter). Diff excludes the scrub's own deletions. Snapshot fallback for non-git projects, change detection by content hash. |
| `integrations/deliver.py` | `preflight()` decides live vs packet-on-disk *before* a workspace exists; both paths render the same packet; the live path passes it via a second `--add-dir` outside the worktree so the diff stays the delegate's alone. |

## Three details worth remembering

1. **The redactor runs once, before the budget loop.** The first draft redacted inside `_render`,
   which the budget loop calls repeatedly while trimming — every iteration would have re-counted the
   same redactions. Cleaning all inputs up front keeps the fired-labels list truthful and makes
   `_render` pure.

2. **The stderr pipe is pumped from submit.** An unread pipe fills at 64 KiB and a chatty child
   deadlocks against it mid-run — the kind of defect replay tests never see because stubs are quiet.
   Same reasoning for `collect()` draining an unconsumed stdout: the P5-T2 watcher taught that the
   defect you don't measure is the one that ships.

3. **The scrub is the isolation, so it is not a parameter.** `create_worktree` has no
   `scrub=False`. The test plants a committed `.claude/settings.json` hook and asserts the delegate's
   copy simply does not contain it — files that do not exist cannot fire. Residual, accepted and
   logged: user-level `~/.claude` hooks still load (the recording shows them firing before `init`).

## Where P6-T1 stands

Requirements 1-5 all done; gate green. Open: the carry-over `bge-m3` decision (scheduled run,
2026-08-24 05:00) — and the phase's next task owns the egress preview UI, the MCP server, and the
reference scenario end to end (retrieval-fed curation, independent `dev.run_tests` in collection).
