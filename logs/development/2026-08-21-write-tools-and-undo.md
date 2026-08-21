# 2026-08-21 — Write tools and the undo journal

P3-T1 requirements 3–4. `fs.write`, `fs.patch`, `fs.move`, `fs.delete`, plus the trash
and the undo journal that make them T1 rather than T2.

## Why the undo machinery came before the tools

[ADR-0005](../../docs/DECISIONS.md#adr-0005--one-policy-gate-risk-tiers-taint-tracking):
**reversibility beats permission.** A write runs automatically *because* it can be
undone. Without working undo, T1 is not a tier — it is a hope, and every file edit would
have to prompt, which is exactly the prompt fatigue the security model treats as a
failure mode.

So the journal is not a nice-to-have bolted on after the tools. It is the thing that
buys them their tier.

## The split across the process boundary

Non-obvious and deliberate:

- the **child** performs the backup, because it is the side doing the write, and reports
  what it did as an `UndoPlan` on the tool result;
- the **parent** records that plan in the journal, because the child holds nothing
  durable and must not be trusted with the record of what it did (ADR-0003).

The `UndoPlan` is **data, not a command string** — the journal executes it. A model can
neither author nor alter it.

Ordering inside the child is **backup → mutate**, never the reverse. A crash between the
two leaves an unreferenced backup, which is harmless. The opposite ordering loses the
file. The parent journals **before** reporting success, for the same reason: a crash
between mutation and record would leave a changed file with no way back.

## Small decisions that matter more than they look

**Writes are temp-then-`os.replace`.** An interrupted write must not leave a
half-written file where a whole one used to be.

**`fs.delete` is a move, never an unlink.** An unrecoverable delete is T4 and simply
absent from the catalogue. If I want that, I use Explorer.

**`fs.patch` refuses ambiguity.** `find="= 1"` matching twice is an *error*, not
something to resolve by picking one. Verified live:

```
found 2 occurrences but count=1; make the search text more specific
file unchanged: 'a = 1\nb = 1\n'
```

Silently patching the first match is how an agent corrupts a file while reporting
success.

**`fs.move` resolves BOTH paths.** `path_fields={"path", "destination"}`. Resolving only
the source would let a move write anywhere on disk — the destination is just as much an
attack surface, and there is a test that tries exactly that.

**Undo refuses to clobber.** If a moved file's origin exists again, undo declines rather
than overwriting it. Losing work while "undoing" would be the worst possible behaviour
from a safety feature.

## The gap the tests found: `preview()`

Two delete tests failed with `approval_required` — correct behaviour, my tests were
wrong. But fixing them exposed a real hole: **nothing could compute the argument digest
an approval must bind to** without duplicating executor internals.

Added `ToolExecutor.preview(tool, args)` → `(verdict, digest)`. It performs nothing and
returns both what *would* happen and the value an approval must carry. That is precisely
what the Confirmation Center needs in Phase 4, so the test failure surfaced a missing
API rather than just a bad assertion.

The digest is computed from **resolved** arguments, so approving `..\..\a.txt` and
approving the absolute path it resolves to are the same decision, and two spellings of
one path cannot produce two different approvals.

## Verified live, end to end

```
write (T1)                 -> allow, journalled, undo_id=u_2542fece4455
undo                       -> restored from trash, original content back
patch, ambiguous match     -> refused, file unchanged
delete without approval    -> approval_required (confirm_strong)
delete with bound approval -> trashed, then undone, file restored
same approval, other file  -> "arguments changed since approval", file survived
```

## A judgment call: `ASYNC240`

ruff flags blocking `pathlib` calls inside async handlers. Tool handlers execute inside
the toolhost child, which processes exactly one invocation at a time and has no event
loop to starve — blocking there is correct, and making every handler async-file-aware
would add noise to protect a loop with nothing else to run.

Scoped the exception to the two tool modules **with the caveat recorded**:
`ToolExecutor(host=None)` runs handlers in-process and *is* exposed to this. That path
exists for tests and as a degraded fallback, and `executor.py` now says so explicitly.
If it ever becomes a real runtime path, this decision has to be revisited.

## Result

`tests/security/`: **138 passed, 1 skipped** (was 116). New coverage: write/patch/move
round-trips with undo, delete-to-trash, undo single-use, undo refusing to clobber,
destination scope checking, ambiguity refusal, taint escalation on writes, and an
assertion that every mutating tool is either reversible-with-undo or gated above T1.
