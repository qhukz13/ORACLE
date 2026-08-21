# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P3-T1 — Process isolation, then PC & dev control tools
**Status:** `IN PROGRESS` — toolhost done and proven; the tools themselves are next
**Date:** 2026-08-21

---

## What was done

**`oracle-toolhost` exists and works.** Tools now execute in a separate, low-privilege process inside
a Windows Job Object. That was requirement #1 of this task and the hard prerequisite for every tool
that spawns a process — which is all of the remaining ones.

`116 security tests` (was 103) + 89 unit/API + 14 TS. Full gate green.

## The claim, proven live

ADR-0003's load-bearing argument is not "a crash is contained" — it is that **killing a thread does
not kill `npm install`'s grandchildren**, so HALT would be a lie without process isolation.

With a child that spawns a grandchild sleeping for 600 s:

```
before HALT: toolhost=True  spawner=True  grandchild=True
after  HALT: toolhost=False spawner=False grandchild=False
```

Job membership is inherited, so anything the child spawns — and anything *those* spawn — dies with
the job. This is the same mechanism already proven one level up in the Tauri shell (OQ-11).

## Cost of the boundary, measured

```
cold (includes process start): 1342 ms
warm: p50 27.9 ms   p95 29.0 ms      budget <50 ms
```

~28 ms per call is what ADR-0003 costs. The router already spends ~1.5 s per turn, so the boundary is
not the bottleneck and never will be. The 1.3 s cold start *was* user-visible on a first tool call,
so the host is now pre-warmed in the background at boot — fire-and-forget, because a broken toolhost
must not stop the agent from starting and explaining itself.

## The bug that cost the most time

**`loop.connect_read_pipe(sys.stdin)` does not work on Windows' Proactor event loop.**

The failure mode is the dangerous kind: the child starts, emits `ready`, and then silently never
reads again. It looks alive. Every call times out at 30 s with nothing to point at. Fixed by reading
stdin on a worker thread. **Suite runtime went 201 s → 15.8 s** — the 201 s was almost entirely
timeouts.

Recorded as [OQ-16](OPEN_QUESTIONS.md#oq-16) with a rule for the codebase, because the same trap is
waiting for the voice daemon, a PTY bridge, and any adapter streaming an external agent's stdout.

## Two leaks `filterwarnings = ["error"]` caught

Both real, neither noise:

- **`start()`'s failure path leaked a process and a pipe.** When job assignment fails we correctly
  refuse to continue — but the original code killed the child without reaping it or closing its
  transport. A slow leak in a process that restarts the toolhost repeatedly.
- **asyncio subprocess transports are only reclaimed in `__del__`.** No public API to close them, so
  `_reap()` closes `proc._transport` explicitly.

## Also fixed

`sys.info` was returning a hardcoded `cpu_percent: 0.0` — a plausible-looking number that is always
wrong, which is worse than none. CPU load is a *rate*: now sampled from two `GetSystemTimes` reads
120 ms apart, reporting real load.

## What crosses the boundary

Crosses: a pre-authorised `Invocation` — tool id, validated args, **already-resolved** absolute
paths, a timeout.

Does not: policy, scopes, tiers, secrets, the audit log, the event log, or any route back into the
runtime. Two details worth keeping:

- the child gets a **constructed** environment and **refuses to start** if `ANTHROPIC_API_KEY` and
  friends are present — absent, not merely unused, enforced from both sides;
- the child **never resolves a path itself**, so the sandbox decision cannot drift to the wrong side
  of the pipe. There is a test for exactly that.

**No retries, deliberately.** A timeout does not mean the side effect did not happen. Both timeout
paths return *"may or may not have completed — it will not be retried"*, worded identically because
which side noticed the deadline is an implementation detail and the caller's uncertainty is the same.

## What was built

```
src/oracle/toolhost/
  jobobject.py  Job Object via ctypes: KILL_ON_JOB_CLOSE, process + memory limits
  protocol.py   Invocation / Response — deliberately small
  __main__.py   the low-privilege child; holds nothing, refuses secrets in env
  host.py       supervision: spawn, assign, dispatch, timeouts, restart, reaping
tests/security/test_toolhost.py   grandchild kill · kill-on-close · no-secrets ·
                                  child-never-resolves-paths · 100-call soak, zero orphans
```

## Still to do in this task

The toolhost was the prerequisite; the tools it exists to isolate are not built yet:

- **write tools** — `fs.write`, `fs.patch`, `fs.move`, plus the undo journal and trash;
- **`git.*`** — status/diff/log/add/commit/branch/stash, and `git.push` at T2;
- **`dev.*`** — `run_tests` with structured results, `build`, `lint`, allowlisted `execute`;
- **`term.*`** — blocked on [OQ-09](OPEN_QUESTIONS.md#oq-09) (`pywinpty`, ConPTY resize/encoding);
- **tool selection in the router** — the agent still cannot choose a tool; the pipeline says
  "tools arrive in Phase 2" for actionable intents;
- **approval issuance** — `Approval` is enforced but nothing creates one yet (Confirmation Center is
  Phase 4).

## Recommended next action

Continue [P3-T1](current_task.md) with the **undo journal and `fs.write`** — the smallest write tool,
which forces the reversibility machinery into existence before anything harder needs it.

```
uv run python scripts/check.py        # gate, incl. 116 security tests
uv run python scripts/audit.py verify # audit chain
uv run oracled                        # backend (pre-warms the toolhost)
```
