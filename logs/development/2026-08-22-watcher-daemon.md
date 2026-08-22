# 2026-08-22 — the watcher under the daemon, and the filter that was not cheap

Requirement 4 of [P5-T2](../../docs/current_task.md). `Watcher` and `debounce` were built and
tested in P5-T1; nothing started them. This gives them an owner, and measures the two claims the
acceptance criterion insists are **measured, not asserted by inspection**.

## Where it lives

`rag/service.py`, spawned from the daemon lifespan through `AppState.spawn`. That choice is the
whole design:

* **HALT already reaches it.** The halt path cancels every tracked task
  ([SECURITY.md](../../docs/SECURITY.md#emergency-stop-halt)), so the emergency stop stops indexing
  too, without a second mechanism to keep in step with the first. `resume` starts it again — a human
  decides when a halt is over, and this is no exception.
* **Shutdown already reaches it.** Same cancellation, same await.
* **Failure is contained.** A missing or malformed `collections.yaml` logs
  `rag.watch_unconfigured` and the daemon starts normally. Chat, tools and the terminal do not
  depend on the index.

Two costs are deferred rather than paid at boot: the ~1 GB embedding model loads on the first
document that needs it, and `knowledge.db` is opened at the same moment. A daemon that runs all day
without an edit in an indexed project never pays either.

## Claim 1 — a save is retrievable within 10 s

`tests/test_rag_service.py::test_a_new_file_is_retrievable_within_the_budget` writes a file into a
watched tree and waits for it to come back out of the index. It passes with room: the 2 s debounce
is the floor, and the rest is one small file's chunking.

The test runs `embed=False`, so what it proves is the lexical half. That is deliberate — a unit run
must not load a gigabyte of ONNX — and it is stated in the module docstring rather than left for a
reader to discover. Everything up to the forward pass is shared with the dense half.

## Claim 2 — an `npm install` does not stall the event loop

Two separate things had to be true, and only one of them was.

**The structure was right.** `_apply` runs behind `asyncio.to_thread`, so a synchronous forward pass
cannot block the loop. Measured with a one-second synchronous stand-in for the pass while a 10 ms
ticker runs: the batch takes its full second, and worst-case loop lag stays in the low tens of
milliseconds.

**The filter was not.** `Watcher.classify_event` runs *on* the loop — it has to, it is what decides
whether an event is worth a thread at all — and it cost **0.27 ms per event**. Five thousand events
from an `npm install` is 1.3 seconds of a daemon answering nothing.

The first two guesses were both wrong, and both were rejected by measurement rather than by argument:

| guess | result |
|---|---|
| Recomputing `prunable_dirs` per event | Real, and fixed — but worth only 2.6 s → 2.1 s |
| Glob matching before the cheap directory check | Reordered, measured, **no change** — reverted |

The profiler settled it in one run:

```
160001  0.492  <frozen ntpath>:52(normcase)
160001  0.258  {built-in method _winapi.LCMapStringEx}
 80000  0.255  fnmatch.py:19(fnmatch)
```

`fnmatch.fnmatch` normcases *both* arguments on every call, and on Windows `normcase` is a
`LCMapStringEx` call into the OS locale mapper. Eight deny patterns matched against two forms of each
path is sixteen fnmatch calls per event, so 5000 events made 160,000 trips through the Win32 locale
API. Compiling each pattern tuple once — `re.compile(fnmatch.translate(p), re.IGNORECASE)`, where
`IGNORECASE` is exactly what `normcase` was providing on this platform — removes them:

```
1.33 s  ->  0.43 s     3.1x, for 5000 events
```

`_matches` is shared with the corpus walker, so the full build gets the same reduction.

**The lesson is the ordering one, again.** The module docstring says "everything cheap happens
first", and it was true about *what* the filter did and false about *how much it cost*. A claim
about performance in a comment is a hypothesis; it stayed unmeasured through P5-T1 because there was
nothing running it against thousands of events. Wiring it into the daemon is what produced the test
that found it.

## A test-environment trap worth remembering

The first version of the `npm install` test built its tree under `pytest`'s `tmp_path`, which on this
machine is `C:/Users/qhukz/AppData/Local/Temp/...`. The deny list contains `**/AppData/**`, and deny
patterns are matched against the **absolute** path — so every path in the test hit the deny branch
and its `log.debug`, and the 2.6 s being measured was mostly structlog.

A test whose fixture accidentally satisfies the rule under test measures the rule it accidentally
satisfied. The perf test now builds paths under a root that is never created and never touched, which
also proves the filter does no I/O.
