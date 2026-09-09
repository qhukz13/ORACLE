# OQ-18 keeps dying, and two sessions hardened the wrong mechanism

**Date:** 2026-09-09 · **Scope:** why three OQ-18 corpus runs died mid-pass ·
**Outcome:** the cause is an explicit `SetSuspendState` call from another process, which nothing
inside our process can veto. The fix is to survive the sleep, not to prevent it.

## The three deaths

| run | died | after | what was blamed |
|---|---|---|---|
| 2026-08-28 ~01:44 | Kernel-Power 42 | ~12 h lost | "the machine slept" → added `keep_system_awake()` |
| 2026-08-28 evening | stopped ~60% | — | stopped deliberately, PC performance |
| 2026-08-29 04:05 | exit `1073807364` | **4 m 22 s** | "the sleep guard did not hold" |

The third is the one that gives it away. `1073807364` is `0x40010004`,
`DBG_TERMINATE_PROCESS` — the process did not crash, it was *terminated* — and it happened four
minutes into a three-hour job that had just been woken by `WakeToRun` specifically so it could run.

## What the event log actually says

```
04:00:47  Power-Troubleshooter 1   The system has returned from a low power state
04:05:35  Kernel-Power 187         User-mode process attempted to change the system state by
                                   calling SetSuspendState or SetSystemPowerState APIs
04:05:36  Kernel-Power 42          The system is entering sleep.  Sleep Reason: Application API
04:05:50  Kernel-Power 107         The system has resumed from sleep
```

**Sleep Reason: Application API.** Not an idle timeout. Not the unattended-wake timer. Another
user-mode process called `SetSuspendState` and Windows obliged.

Two more facts make it conclusive rather than suggestive:

* `powercfg /query SCHEME_CURRENT SUB_SLEEP` reports **STANDBYIDLE on AC = 0 — Never.** This machine
  is already configured never to idle-sleep on mains power. There is no idle timer to lose to.
* **21 of the last 21 sleeps** on this box are `Application API`. Not most — all of them. The idle
  path has never once been the cause; something always sleeps this machine explicitly, always
  between 23:00 and 05:00.

## Why the hardening did nothing

`keep_system_awake()` calls `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`. That
tells the power manager *"do not idle-sleep while I am working"*. It is the correct API for the
problem it was written for, and that problem does not exist here — the idle timeout was already
disabled. **`SetThreadExecutionState` cannot veto another process's `SetSuspendState`.** Nothing a
process can do from the inside can.

So the guard was added after the first death, and the second and third deaths were then read as *"the
guard did not hold"*, which sent the next session looking at the guard again. The diagnosis was
never wrong about *what* happened — the machine slept — only about *why*, and the why is the half
that decides the fix.

**What would have caught this sooner:** the exit code. `DBG_TERMINATE_PROCESS` says *terminated by
something else*, and `Sleep Reason` in Event 42 names the mechanism in plain English. Both were
available on 2026-08-28. Neither was read; "the machine slept" was treated as a complete answer when
it was half of one.

## The culprit is not named, and does not need to be

No scheduled task on this machine has a suspend action, and the TaskScheduler operational log has no
entries in that window. A Start-menu "Sleep", a vendor utility, and a maintenance routine all present
identically as `Application API`, so naming it would need process-level tracing across a night.

That work is not worth doing, because **it does not change the fix.** Whatever it is, it is a thing
the owner's machine does at night, on purpose or otherwise, and a three-hour measurement that
requires the machine never to be slept is a measurement that will keep failing.

## The fix: survive it

The pass already checkpoints every 256 chunks and resumes — that machinery was added on 2026-08-28
and is the part that was right. What was missing is anything to *re-fire* it.

* The scheduled task now **repeats every 30 minutes for 24 hours** with `MultipleInstances =
  IgnoreNew`, so a sleep costs only the time asleep. Each firing resumes from the last checkpoint.
* `StopOnIdleEnd` is now **false**. It defaults to true, and would let an idle transition kill a
  three-hour pass on its own — a second way to lose the run that had nothing to do with the first.
* `StartWhenAvailable` is set, so a firing missed while the machine was asleep runs on wake.
* The wrapper **exits immediately** if `logs/measurements/oq18-translated.json` exists, so the
  repetition costs nothing once the run is done.
* The wrapper now **appends** to `oq18-translated.txt` instead of truncating it. Truncating meant
  every attempt destroyed the evidence of why the previous one stopped — which is the only reason
  this took three deaths to diagnose.

`keep_system_awake()` is left in place. It is cheap, correct for the idle case, and this machine's
power configuration could change; but the docstring no longer claims it is what keeps the run alive.

## The measurement is running

Started 2026-09-09 19:22 with the machine awake and the owner present — the checkpoint from
2026-08-29 was refused, correctly, because tracked files changed and the corpus fingerprint moved
(`c2682739…` → `862307e5…`). So this is a clean pass, ~2.5–3 h.

**An unchanging log is still a stuck job.** Progress is a rate-and-ETA line per 256-chunk checkpoint
in `logs/measurements/oq18-translated.txt`.
