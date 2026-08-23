# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P6-T1 — The adapter, the packet, and the fallback: delegation without a UI**

**Phase:** [6 — External agent integration](ROADMAP.md#phase-6--external-agent-integration--post-mvp) · **Scope:** Post-MVP
**Status:** `NOT STARTED` · **Set:** 2026-08-23
**Previous task:** P5-T2 — **done except one item, and that item is automated.** See the carry-over
below and [current_report.md](current_report.md).

---

## Carry-over from P5-T2 — one item, closing itself

**Sequencing rule 1 is waived by the owner (2026-08-23) for this transition**, deliberately and
narrowly: the only open P5-T2 criterion is *a recorded decision on `bge-m3` vs `e5-base`*, the
measurement for it is authorised and scheduled (one-time task `bge-m3-full-corpus-run`,
2026-08-24 05:00 local, separate DB, shipped index untouched), and **nothing in P6 depends on which
embedding model ships** — Phase 6 consumes retrieval through the `know.*` interface, and swapping
the model is one `ModelSpec`.

When the run's numbers land: record the decision in [OQ-02](OPEN_QUESTIONS.md#oq-02), tick the last
P5-T2 criterion, and put the model choice in front of the owner. That is the whole remaining debt.

Everything else P5-T2 settled, so it is not re-litigated: tree-sitter built and **off** (owner's
call, 2026-08-23 — the expanded fixture set decides later); the indexing budget in ROADMAP.md
**confirmed as written**; the gate green including security on 2026-08-23.

## Objective

The first slice of delegation, built back-to-front: the **Handoff Packet** (the vendor-neutral core
abstraction), the **adapter protocol** with a `ClaudeCodeAdapter` tested against a *recorded*
contract, **worktree isolation**, and the **fallback path** — everything in
[INTEGRATIONS.md](INTEGRATIONS.md) §2, §3, §6, §7 that does not need a UI. The egress preview UI and
ORACLE's MCP server are P6-T2+; until the preview exists, **nothing egresses** except the single
supervised smoke run in requirement 1.

## Requirements

1. ~~**Record the vendor contract as fixtures.**~~ **DONE 2026-08-24.** Two fixtures recorded from
   the real CLI: `smoke-v2.1.238.jsonl` (owner approved the previewed payload; 19 events; the run's
   work independently verified) and `auth-failed-v2.1.238.jsonl` (the subscription-auth failure,
   recorded at zero egress). The recording found **three contract corrections** now in
   INTEGRATIONS.md §3: `result` is the semantic end but not the last line; `system/init` is not
   first when user-level hooks exist; unknown event kinds must be skipped, never fatal.
   **The auth finding, 2026-08-23, rewrote the contract first:** installed CLI is v2.1.238, and
   the owner authenticates via **Max subscription with zero API credit** — `--bare` ignores both the
   OAuth login and `CLAUDE_CODE_OAUTH_TOKEN` (measured: *"Not logged in"*, zero egress), while a
   non-bare run authenticates with no key at all (measured: structured result collected). ORACLE
   therefore runs **without `--bare`** and rebuilds its isolation with the **worktree scrub** +
   `--setting-sources user` + `--strict-mcp-config`. All recorded in
   [INTEGRATIONS.md §3](INTEGRATIONS.md#3-claude-code-cli--supported).
2. ~~**`ExternalAgentAdapter` protocol + `ClaudeCodeAdapter`.**~~ **DONE 2026-08-24.**
   `src/oracle/integrations/` — types, protocol, adapter; 9 contract tests against a stub CLI
   replaying the recorded fixtures (`tests/test_integrations_claude.py`), including normalisation
   to the exact event sequence, auth-failure surfacing, cancel escalation mid-stream, and a
   `collect()` that drains an unconsumed stream rather than deadlocking. As designed: the protocol exactly as pinned in
   [INTEGRATIONS.md §2](INTEGRATIONS.md#2-the-adapter-interface); vendor stream-json normalised to
   ORACLE's `AgentEvent` vocabulary at the adapter boundary (`parent_tool_use_id` → subagent
   attribution, `system/api_retry` → a *retrying* state, final `result` → cost). Contract tests run
   against a **stub CLI** that replays the recorded fixtures — deterministic, no network, no cost.
3. ~~**Handoff Packet builder**~~ **DONE 2026-08-24** — `src/oracle/handoff/`: the six files +
   `packet.json`, redaction *before* rendering via the same `redact_text` as the log sink (entropy
   scanning on, because this text egresses), and the 30k budget as an asserted ceiling that drops
   whole excerpts lowest-priority-first and records the cut in CONTEXT.md. Refuses (raises) rather
   than truncates when the task alone exceeds the budget. Selection steps: git state implemented
   (`gather.py`); the retrieval-fed steps belong to the task that wires the reference scenario end
   to end. As designed, per [INTEGRATIONS.md §6](INTEGRATIONS.md#6-the-handoff-packet--fallback-and-the-core-abstraction):
   the six files + `packet.json`; context assembly steps 1–8 — curated selection (diff, failing-test
   imports, hybrid retrieval scoped to the project, the project's own agent docs, git state, prior
   attempts), then **REDACT**, then the **30k-token budget as an asserted ceiling**. Large context
   goes into the worktree as files — piped stdin is capped at 10 MB and that cap shapes the design.
4. ~~**Worktree isolation + collection.**~~ **DONE 2026-08-24** — `integrations/workspace.py`. As
   designed: `git worktree add` under `.oracle/wt/<task-id>`, base commit recorded; then the
   **scrub**: delete `.claude/` and `.mcp.json` from the worktree before invocation, so the target
   project's hooks and MCP servers cannot load — this carries the isolation `--bare` used to
   provide, and it has **no opt-out parameter**. Diff against base excludes the scrub's own
   deletions; discard is asserted to leave the real tree byte-identical. Non-git projects: snapshot
   copy with content-hash change detection, recorded as a limitation. The independent
   `dev.run_tests` half of `collect()` waits for the reference-scenario wiring.
5. ~~**The fallback is first-class.**~~ **DONE 2026-08-24** — `integrations/deliver.py`:
   `preflight()` failure (binary missing, unauthenticated) routes to packet-on-disk with a clear
   explanation *before* anything is built for egress — asserted with the CLI absent, including that
   **no workspace is ever created** for a run that could never start. Both paths render the same
   packet; the live path points the delegate at it via a second `--add-dir` outside the worktree,
   so the packet never pollutes the diff the result is judged by.

## Constraints

- **No egress without the owner seeing the payload.** Until the preview UI exists, the only live
  invocation is requirement 1's supervised smoke run. Everything else runs against the stub.
- **No `--bare`, by measurement, not preference** — it is unusable with the owner's subscription
  auth. The exposure it guarded against (hooks + MCP auto-discovery from untrusted folders) is
  closed materially instead: the worktree scrub is a **precondition of every invocation**, asserted
  by a test that plants a `.claude/settings.json` hook in a fixture repo and proves it never fires.
  No `ANTHROPIC_API_KEY` handling anywhere — the subscription login is the only credential, and
  ORACLE never reads or stores it.
- A planted secret in a candidate context file must be redacted in **every** rendered packet file —
  this is the ROADMAP acceptance criterion, tested, not inspected.
- `tests/security/test_injection.py` passes unchanged; retrieved text stays untrusted inside
  CONTEXT.md (source-attributed excerpts, never instructions).
- Every new dependency gets a TECH_STACK.md ledger line. Expected: none — the adapter is subprocess +
  json over the standard library.
- No new user-facing tools this task (cap stays 33/40); delegation is surfaced in a later task.

## Acceptance criteria

- [x] Recorded stream-json fixtures exist; the INTEGRATIONS.md contract section matches the installed
      CLI version, re-verified with dates. **Done 2026-08-24, with three corrections recorded.**
- [x] Adapter contract tests pass against the stub CLI: event normalisation, cancel (SIGINT →
      SIGTERM escalation), non-zero exit → failure with the printed result captured, `preflight()`
      honest on missing binary. **9 tests, all green 2026-08-24.**
- [x] The packet builder, run on the reference scenario's shape
      ([INTEGRATIONS.md §8](INTEGRATIONS.md#8-reference-scenario)), produces all six files +
      `packet.json`; context ≤ 30k tokens **asserted**; the planted secret redacted everywhere.
      **Done 2026-08-24** — `tests/test_handoff_packet.py`: the planted `sk-ant-` key and an
      assigned password are absent from every rendered file, and the over-budget case *raises*
      rather than truncates.
- [x] With the CLI unreachable, the fallback engages automatically with a clear explanation —
      asserted by test. **Done 2026-08-24**, including "no workspace created".
- [x] Worktree lifecycle: create → **scrub** → run (stub) → collect diff + independent test result →
      discard leaves `git status` in the real tree byte-identical. Asserted — a fixture repo commits
      a `.claude/settings.json` hook and `.mcp.json`; the scrub leaves the delegate a copy where
      neither exists (files that are not there cannot fire), the real tree keeps both, the diff
      excludes the scrub, and discard restores porcelain-identical status. **Done 2026-08-24.**
      The independent `dev.run_tests` half of collection waits for the reference-scenario wiring.
- [x] One supervised live smoke run end to end, payload reviewed by the owner first, result collected
      from the worktree. **Done 2026-08-24** — owner approved; the run's output file verified
      independently of the agent's report (`count.txt = 9`).
- [ ] The gate green including the security suite.
- [ ] *(Carry-over)* The `bge-m3` result recorded in OQ-02 and the model decision put to the owner.

## Relevant files

New: `src/oracle/integrations/` (protocol, `claude.py`, stub-CLI fixtures under
`tests/fixtures/claude_stream/`) · `src/oracle/handoff/` (packet builder).
Modify: `docs/INTEGRATIONS.md` (re-verified contract) · `docs/TECH_STACK.md` (ledger, expected empty).
Read first: [INTEGRATIONS.md](INTEGRATIONS.md) end to end · [SECURITY.md §7](SECURITY.md#7-secrets-and-egress) ·
[RAG.md §5](RAG.md#5-hybrid-retrieval) (retrieval feeding step 3 of context assembly).

## Dependencies

P5's subsystem (built, gate green). The embedding-model decision is **not** a dependency — see the
carry-over. OQ-05 (Antigravity) is resolved but the `AntigravityAdapter` is explicitly *not* in this
task.

## Risks

| Risk | Mitigation |
|---|---|
| Vendor CLI surface drifts under us | The recorded-fixture suite *is* the pinned contract; requirement 1 re-verifies against the installed version before code is written |
| Secret scan misses a shape | Planted-secret tests are the floor, not proof; redaction runs per chunk before rendering, and the preview (P6-T2) stays the human backstop |
| Scope creep into UI | Egress preview UI, MCP server, Antigravity are all explicitly P6-T2+; this task ships no UI |
| Windows worktree cleanup (locked files, long paths) | Discard path tested on this machine, including with a process still holding the worktree open |

## Definition of done

All acceptance criteria · INTEGRATIONS.md contract re-verified with dates · the carry-over recorded
in OQ-02 · the gate green including the security suite · `current_report.md` overwritten · this file
updated to **P6-T2**.
