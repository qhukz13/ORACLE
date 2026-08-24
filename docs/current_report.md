# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P6-T4 — the capstone. **Done, all acceptance criteria.** Phase 6 has one unbuilt task left
(the Antigravity adapter, ROADMAP task 7) and one aspirational DoD clause ("daily use") that only
time can check off.
**Status:** The live MCP run is green — the delegate called back through ORACLE's gate and the call
is in the audit log. `bge-m3` shipped as `DEFAULT`. Gate green.
**Date:** 2026-08-24

---

## What happened

**The owner took the OQ-02 switch.** `DEFAULT = BGE_M3` shipped with the docs updated to the
measured costs: 1024d, 140 MB index, ~3 h cold build, 3.6 min warm from the embedding cache, ~3 GB
resident. The health endpoint and its tests now read `DEFAULT` instead of a hardcoded spec, so a
model switch can never make the health view lie. `e5-base` keeps its `ModelSpec` one line away, and
`KnowledgeStore.bind` still refuses an index built by the other model.

**The live run closed P6-T4.** One supervised egress, payload previewed and approved, against the
real `claude` CLI and the real `python -m oracle.mcp` bridge, with a throwaway daemon on loopback.
All five checks green: server loaded, tool offered, delegate used `mcp__oracle__fs_read` instead of
its own Read, answer correct, call in the audit log. The exchange is pinned as
`tests/fixtures/mcp/live-verify.jsonl`; [INTEGRATIONS.md §8](INTEGRATIONS.md#8-reference-scenario)
records how it actually went.

It took three attempts, and both failures were findings, not flakes:

1. **`mcp.tools_rejected: bad signature`** — the script minted its capability token from its own
   `TokenStore` while the daemon verified with its own process-lifetime key. That refusal is the
   design ("one per daemon", never persisted); the script now mints from the running daemon's store,
   which is the only way a real delegation gets a token anyway.
2. **`0 entries` in the audit check** — the audit log lives under `settings.log_dir`
   (`log_dir/audit/audit.jsonl`), not `data_dir`, and the throwaway daemon had inherited the real
   one. Its single entry — a genuine, gated `fs.read` — landed in the owner's live audit log
   (harmless, left in place; the log is hash-chained). The verification daemon now scopes `log_dir`
   into its workspace like everything else.

## Where Phase 6 stands

Every P6-T4 acceptance criterion is checked. Against the phase's Definition of Done in ROADMAP.md:

- **"Claude adapter in daily use"** — cannot be true the day the capstone lands; it is now
  *usable* daily (palette, explicit route, escalation), which is what the phase could deliver.
- **"Fallback proven by disabling the CLI"** — done in P6-T3.
- **"Antigravity either working or explicitly documented as blocked"** — neither: `agy` is verified
  and Supported (OQ-05 resolved), but ROADMAP task 7's `AntigravityAdapter` is not built. This is
  the phase's remaining work.

## What's next — the owner's pick

1. **P6-T5, the Antigravity adapter** — finishes Phase 6's task list against an already-verified
   CLI contract (INTEGRATIONS.md §5).
2. **[OQ-18](OPEN_QUESTIONS.md#oq-18)** — best measured recall is 61% against the Phase 5 gate's
   80%, and 7 of 25 Russian fixtures never enter the candidate set. Blocks the Phase 5 gate, and
   context quality is what delegation quality rests on.
3. **P7-T1, Pipelines** — the next phase, if Phase 6 is declared done-enough with the above
   recorded.
