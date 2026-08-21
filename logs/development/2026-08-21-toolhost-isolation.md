# 2026-08-21 — Process isolation: `oracle-toolhost`

Implements [ADR-0003](../../docs/DECISIONS.md#adr-0003--tool-execution-in-a-separate-process),
deferred at the end of Phase 2 and built first in Phase 3 because it is the hard
prerequisite for any tool that spawns a process.

## The claim, and the proof

ADR-0003 says the privilege boundary must be a **process** boundary. The load-bearing
half of that is not "a crash is contained" — it is that **killing a thread does not kill
`npm install`'s grandchildren**, so HALT would be a lie without it.

Measured live, with a child that spawns a grandchild sleeping for 600 s:

```
before HALT: toolhost=True  spawner=True  grandchild=True
after  HALT: toolhost=False spawner=False grandchild=False
```

A Windows **Job Object** with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` is what makes that
true. Job membership is inherited, so anything the child spawns — and anything *those*
spawn — is in the job. `Popen.kill()` gives none of this.

Same mechanism already proven one level up in the Tauri shell (OQ-11): force-quitting
the shell took `oracled` with it.

## The bug that cost the most time

`loop.connect_read_pipe(sys.stdin)` **does not work on Windows' Proactor event loop.**

The failure mode is nasty: the child starts, emits its `ready` frame, and then never
responds to anything. It looks alive. Every call times out at 30 s. The only clue was a
`_ProactorReadPipeTransport._loop_reading()` traceback on stderr, which the parent was
discarding.

Fix: read stdin on a worker thread (`asyncio.to_thread(sys.stdin.buffer.readline)`).
The child handles one invocation at a time, so a thread per read costs nothing.

**Test-suite runtime went 201 s → 15.8 s** once this was fixed — the 201 s was almost
entirely timeouts.

## Two resource leaks `filterwarnings = ["error"]` caught

Both surfaced as `ResourceWarning`, both were real:

1. **`start()`'s failure path leaked a process and a pipe.** When job assignment fails
   we correctly refuse to continue, but the original code killed the child without
   waiting for it or closing its transport. In a long-lived process that restarts the
   toolhost, that is a slow leak rather than a warning.
2. **asyncio subprocess transports are only reclaimed in `__del__`.** There is no public
   API to close them, so `_reap()` closes `proc._transport` explicitly.

Keeping warnings-as-errors was worth it: both were pointing at genuine problems, not
noise to suppress.

## Measured cost of the boundary

```
cold (includes process start): 1342 ms
warm: p50 27.9 ms   p95 29.0 ms      budget <50 ms
```

~28 ms per call is the price of ADR-0003. For scale, the router already costs ~1.5 s per
turn, so the boundary is not the bottleneck and never will be.

The 1.3 s cold start *is* user-visible on a first tool call, so the host is now
**pre-warmed in the background at boot** — fire-and-forget, because a broken toolhost
must not stop the agent from starting and explaining itself.

## What crosses the boundary, and what does not

Crosses: a pre-authorised `Invocation` — tool id, validated args, **already-resolved**
absolute paths, a timeout.

Does not cross: policy, scopes, tiers, secrets, the audit log, the event log, or any way
back into the runtime. The child cannot widen its own permissions because it has none.

Two belt-and-braces details:

- The child is given a **constructed** environment, not an inherited one, and **refuses
  to start** if `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GITHUB_TOKEN` are present. Absent,
  not merely unused — and enforced from both sides.
- The child **never resolves a path itself**. With no `resolved` mapping the handler
  fails rather than helpfully resolving the raw argument, which would move the sandbox
  decision to the wrong side of the pipe. There is a test for exactly that.

## Retry policy: none, deliberately

A timeout does not mean the side effect did not happen — it means we stopped waiting.
Both the parent-side and child-side timeout paths return the same message:

> The action may or may not have completed — it will not be retried.

Retrying here is how an agent creates two commits, or two pushes, and calls it
resilience. The wording is identical on both paths on purpose: which side noticed the
deadline is an implementation detail, the caller's uncertainty is the same.

## Also fixed: `sys.info` was returning a fake CPU number

`cpu_percent` was hardcoded to `0.0` — a plausible-looking value that is always wrong,
which is worse than returning nothing. CPU utilisation is a *rate*, so it needs two
`GetSystemTimes` samples and an interval; now sampled over 120 ms and reporting real
load (5.3% at rest here).

## Result

`tests/security/`: **116 passed, 1 skipped** (symlink case, needs admin). Includes the
grandchild-kill test, the kill-on-close test, the no-secrets test, the
child-never-resolves-paths test, and a 100-call soak asserting zero orphaned processes.
