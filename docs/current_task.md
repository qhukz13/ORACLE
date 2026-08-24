# Current Task

> Single active task. **Overwrite this file when the task changes** — it is a snapshot, not a history.
> History belongs in git and `logs/development/`.

---

## Task

**P6-T4 — The capstone: ORACLE decides to delegate, and the reference scenario runs**

**Phase:** [6 — External agent integration](ROADMAP.md#phase-6--external-agent-integration--post-mvp) · **Scope:** Post-MVP
**Status:** `IN PROGRESS — requirements 1-4, 6 done 2026-08-24; the live run is the only step left` · **Set:** 2026-08-24
**Previous task:** P6-T3 — **done** except the live CLI recording; see [current_report.md](current_report.md).

---

## Carry-over

1. ~~**bge-m3**~~ — **DONE 2026-08-24.** The run produced artefacts and resolved
   [OQ-02](OPEN_QUESTIONS.md#oq-02): `bge-m3` wins, 61% against 55%, but only once the fusion gate
   stopped admitting BM25 on all 38 queries — measured against the shipped gate it *lost*, 53%. The
   gate fix shipped with it; `DEFAULT` did not change, because a ~2.5 h rebuild and 3 GB resident is
   the owner's call. Opened [OQ-18](OPEN_QUESTIONS.md#oq-18): 61% is still nineteen points under the
   Phase 5 recall gate. [Log](../logs/development/2026-08-24-oq02-bge-m3.md).
2. **The live MCP recording** from P6-T3 requirement 1 — one supervised run against the real CLI,
   payload previewed. Folded into this task's requirement 5, because the reference scenario needs a
   live run anyway and one egress serves both.

## Why this task exists

The machinery is built and none of it is reachable from a sentence. A delegation today starts only
from a raw WS command; ORACLE itself never decides that a job is too big for it. That decision is
what [INTEGRATIONS.md §8](INTEGRATIONS.md#8-reference-scenario) calls the actual intelligence in the
system:

> Step 7 — recognising that this needs a stronger model — is the actual intelligence in the system,
> and it is a decision a small model can make reliably because it is a classification, not a
> solution.

This task makes that sentence true, and then proves the whole twelve-step scenario end to end.

## The decision this task opens with

**Does ORACLE delegate on its own, or propose and wait?** It delegates — and the egress preview is
the human gate, which is exactly what P6-T2 built it for. Adding a second "shall I delegate?"
question before the "here is what would be sent" question is two prompts for one decision, and
[SECURITY.md §2](SECURITY.md#2-design-principles) says the answer to fatigue is fewer prompts, not
politer ones. A delegation that is never approved costs a packet render and nothing else.

## Requirements

1. ~~**The `delegate` intent stops being a stub.**~~ **DONE 2026-08-24.** Better than planned: the *pre-router* already recognised "ask Claude to …" deterministically and was answering with a Phase-6-not-built stub, so the explicit route now costs ~5 ms and no model call. A project the user named in their own sentence is used; anything else asks. As designed: It already exists in the classifier's vocabulary
   and its measured accuracy is in the P1 fixtures; today it lands in the "that needs a phase that
   isn't built yet" branch. Route it into `DelegationService` with the project resolved, and when
   no project can be resolved, **ask** rather than guessing — a delegation against the wrong
   repository is expensive in a way a wrong answer is not.
2. ~~**Escalation: the failure signature.**~~ **DONE 2026-08-24** (`_failure_signature`, narrow by design: a suite that ran and failed, never a tool that could not run). As designed: After an actionable turn that ends in a *reproducible
   failure* — `dev.run_tests` reporting failures is the case §8 describes — ORACLE escalates the
   same work to a delegate, carrying what it already learned (the failing tests, the tool it ran)
   into the packet's ATTEMPTS.md. Deterministic, not a second model call: the signal is "a
   verification tool reported failure in this turn", which is a fact, not a judgement.
3. ~~**`delegating` is a real state.**~~ **DONE 2026-08-24**; both routes finish the turn `delegated`, documented in AGENT_RUNTIME.md. As designed: `agent.state: delegating` during the handoff, and the turn
   finishes with `outcome: "delegated"` while the delegation continues under its own `task.*`
   stream — a turn that stayed open for a ten-minute delegation would block the session for work
   the user can already watch in the panel.
4. ~~**The reference scenario as an executable test.**~~ **DONE 2026-08-24** — `tests/test_reference_scenario.py`, four cases: the full scenario, green-tests-do-not-escalate, the explicit route, and the unresolvable project. As designed: [INTEGRATIONS.md §8](INTEGRATIONS.md#8-reference-scenario)'s
   twelve steps, driven with `FakeProvider` and the stub CLI: classify → tool → failing tests →
   **escalate** → packet with the prior attempt → egress approval → run → collect diff +
   independent tests → report. The test asserts the *sequence*, because the ordering is the design:
   nothing egresses before the approval, and the evidence at the end is ORACLE's, not the
   delegate's.
5. **One supervised live run, closing two open items.** The real CLI, the real MCP bridge, a real
   worktree: prove the delegate can call `mcp__oracle__*` back into the daemon and that the call
   lands in the audit log. Record the exchange into `tests/fixtures/mcp/` so the transport contract
   is pinned by evidence like the vendor stream is. Payload previewed before it is sent.
6. ~~**The UI can start one.**~~ **DONE 2026-08-24** — the palette offers a delegation once the query names a known project, worded so the entry says a human still approves the egress. As designed: A delegation begins from something a person can type — the command
   palette is the natural home, since it already routes typed intent. The panel from P6-T2 then
   shows it. Without this the capstone is reachable only from a websocket frame.

## Constraints

- **No new prompt before the egress preview.** Escalation proposes by *starting* the delegation;
  the preview is where a human says no.
- Escalation is **deterministic**: a fact about the turn's tool outcomes, never a second model call.
  A model deciding to spend money on a stronger model is a loop nobody asked for.
- The reference-scenario test uses `FakeProvider` and the stub CLI — it must run in CI, offline,
  every time. The live run of requirement 5 stays a script, not a test.
- A delegation started by escalation carries the failure into ATTEMPTS.md. A delegate that repeats
  the attempt ORACLE just made is the waste the packet format exists to prevent.
- Tool count unchanged; `ai.delegate` remains a policy entry rather than a registry tool.

## Acceptance criteria

- [x] "ask Claude to fix the auth tests in Asterim" starts a delegation, with the project resolved,
      and an unresolvable project asks instead of guessing. Asserted with `FakeProvider`.
- [x] A turn whose `dev.run_tests` reports failures escalates, and the resulting packet's
      ATTEMPTS.md names what ORACLE already tried. Asserted on the rendered file.
- [x] `agent.state: delegating` is emitted, and `turn.finished` carries `outcome: "delegated"` while
      the `task.*` stream continues.
- [x] The reference scenario passes as one test, asserting the order — in particular that no egress
      precedes the approval.
- [ ] One live run: the delegate calls back through MCP, the call is in the audit log, and the
      exchange is recorded as a fixture.
- [x] The palette can start a delegation; vitest covers the entry point.
- [x] The gate green including the security suite — `check: OK` 2026-08-24.
- [x] *(Carry-over)* bge-m3 recorded in OQ-02 2026-08-24, decision to the owner: the numbers
      favour switching, `DEFAULT` left unchanged pending it.

## Relevant files

Modify: `src/oracle/router/pipeline.py` (the delegate branch + escalation) ·
`src/oracle/delegation/service.py` (attempts from the failing turn) ·
`apps/desktop/src/components/CommandPalette.tsx` · `docs/INTEGRATIONS.md` §8 (mark it as executed) ·
`docs/AGENT_RUNTIME.md` (the `delegating` state).
New: `tests/test_reference_scenario.py` · `scripts/verify_mcp_live.py`.
Read first: [INTEGRATIONS.md §8](INTEGRATIONS.md#8-reference-scenario) · `router/pipeline.py` ·
[AGENT_RUNTIME.md §3](AGENT_RUNTIME.md#3-state-machine).

## Dependencies

P6-T1..T3, all built. The live run needs the owner's go-ahead, like every egress.

## Risks

| Risk | Mitigation |
|---|---|
| Escalation fires on every red test and burns quota | It carries the same approval gate as any egress; the preview names the cost before anything is sent |
| The `delegate` intent misfires on "fix the typo" | The P1 fixture set already measures this label; re-run it, and if it regressed, that is a finding not a footnote |
| The reference test becomes a mock of itself | It drives the real pipeline, real gate, real packet renderer and real worktree; only the model and the vendor CLI are fakes, and both replay recorded behaviour |

## Definition of done

All acceptance criteria · INTEGRATIONS.md §8 annotated with what actually happened when it ran ·
the phase's Definition of Done in ROADMAP.md re-read and either met or explicitly amended ·
the gate green · `current_report.md` overwritten · this file updated to **P7-T1** (Pipelines) or the
phase's remaining work, whichever the state argues for.
