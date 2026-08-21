# Two bugs found by a criterion I nearly ticked off

**Date:** 2026-08-21 · **Task:** P3-T1, closing out Phase 3

---

The ROADMAP asks that the terminal *"streams a long `npm install` without dropping bytes
or blocking the event loop"*. `term.*` had 13 passing tests and worked interactively, so
the honest thing was to actually measure it rather than call it done.

A numbered burst was used instead of a real `npm install`, so "no bytes dropped" is
**arithmetic** rather than a judgement call: 2000 lines, each carrying its own sequence
number, plus a 10 ms heartbeat task to prove the event loop stayed responsive.

First run:

```
lines emitted : 2000
lines seen    : 1774
missing       : 226   [151, 152, 153, ...]
VERDICT: DROPPED LINES
```

Plus, in the log, something worse.

## Bug 1: the child's logs were on the protocol channel

```
toolhost.unparseable_frame  frame='2026-08-21 15:13:33 [info] term.opened  session=term_3ac...'
```

**structlog's default logger writes to stdout — which is the toolhost's protocol pipe.**
`configure()` sends logs to stderr, but the child never called it, so every `term.*` call
was writing a log line into the parent's frame reader.

It was benign only by luck. The parent logged "unparseable frame" and skipped it — but a
log line that happened to be valid JSON would have been parsed as a `Response`, and
attributed to whatever invocation was in flight.

Fixed with a belt and braces:

```python
_PROTOCOL = sys.stdout      # captured before anything can touch it
...
configure(None, level)      # logs to stderr, no file: this process holds nothing durable
sys.stdout = sys.stderr     # a stray print() in any tool now goes somewhere harmless
```

The second line is the one that matters. It makes protocol corruption *structurally*
impossible rather than a rule everyone has to remember.

## Bug 2: two suspects, and both were wrong

The missing lines were always the **oldest** ones, and the ring buffer reported **zero**
drops. Two hypotheses, both plausible, both wrong — and each cost a measurement to
eliminate, which is the point of writing them down.

**Wrong hypothesis 1: ConPTY coalesces.** A pseudoconsole is a *screen*, not a pipe; if
the reader falls behind, scrolled-off content is legitimately gone. Testable: vary the
row count and the loss should change.

```
rows=  30   seen=2000  missing=0
rows= 200   seen=2000  missing=0
rows=1000   seen=2000  missing=0
```

Not ConPTY. It loses nothing at any size when read promptly.

**Wrong hypothesis 2: the pump is too slow.** `Session.append` recomputed
`sum(len(c) for c in buffer)` on every chunk — O(n) per append, O(n²) over a burst. That
is a genuine bug and it was fixed (a running counter), but it was not *this* bug:
reproducing the exact pump design standalone lost nothing even with a 20 ms sleep.

```
thread pump  5ms, consume 50ms   seen=2000  missing=0  produced=204015
thread pump 20ms, consume 50ms   seen=2000  missing=0  produced=204015
```

## The actual bug: the data was destroyed on the way *out*

```python
text = session.take()                              # empties the ENTIRE buffer
truncated = len(text) > MAX_READ_CHARS
return TermReadResult(text=text[-MAX_READ_CHARS:], ...)   # keeps only the LAST 16 KB
```

`term.read` consumed everything the pump had collected and then returned only the final
16,000 characters. The rest was not buffered, not counted, not recoverable — **thrown
away**. Reading a 200 KB burst every 50 ms lost the front of every read, which is exactly
"missing from line 1", and the drop counter stayed at zero because the ring had genuinely
not trimmed anything.

The fix is a signature change that makes the mistake unavailable:

```python
def take(self, limit: int | None = None) -> tuple[str, bool]:
    """Up to `limit` characters from the FRONT, leaving the rest buffered."""
```

`truncated` now means **"there is more waiting"**, not "some was destroyed". Oldest-first
with the remainder kept is also just what a terminal does.

```
lines emitted : 2000
lines seen    : 2000
missing       : 0
bytes captured: 204090        (standalone ConPTY reference: 204015)
loop ticks    : 546   p50 13.5 ms   max 22.7 ms
VERDICT: complete
```

## What to take from this

- **A "truncated" flag that means "we deleted some" is a bug wearing a field name.** It
  reads as informative and is actually a silent-loss notice. Bounding a read is correct;
  the remainder has to stay somewhere.
- **A drop counter only counts the drops you thought of.** `dropped` was added first,
  reported `0`, and was *correct* — the loss was one layer further out. A metric that
  cannot see the failure is worse than none, because it argues against looking.
- **Two disproved hypotheses were worth the time.** Neither ConPTY nor the pump was at
  fault, but eliminating them is what left only the return path. The O(n²) `sum()` found
  along the way was a real bug that would have bitten later under a longer build.
- **The criterion was nearly ticked off on the strength of 13 green tests.** Every one of
  them read short output. Nothing lies quite like a suite that never tested the case.
