# Current Report

> Latest report from the working agent. **Overwrite, don't append** — this is a snapshot for whoever
> picks the project up next.

**Task:** P3-T1 — Process isolation, then PC & dev control tools
**Status:** `DONE` — all 11 requirements, all acceptance criteria verified live
**Date:** 2026-08-21

---

## The headline

**ORACLE can now do things.** Ask it to commit your changes and it commits them, through a 0.8B
router, across a process boundary, with an undo you can use afterwards:

```
"commit my changes in Asterim with message add the feature"
  -> git.commit  ->  "Committed 59eef851 on main — 3 file(s), +10/-0."
  -> undo        ->  "commit 59eef851 undone; the changes are staged again"
```

**27 tools** (26 offerable, `git.undo` hidden). **360 tests**, 1 skipped. Gate green: ruff, mypy
`--strict`, pytest, TS typecheck, vitest.

## Acceptance criteria — measured, not inferred

Every one was run against real things: a real git repo with a real bare remote, a real ConPTY, the
real toolhost, the real model.

| criterion | result |
|---|---|
| commit end to end, undoable | ✅ routed to `git.commit`; undo restores HEAD and leaves the work staged |
| tests return structured counts | ✅ `1 passed, 1 failed, 0 skipped`, source `junit-xml` |
| `git.push` prompts; approving runs the previewed argv | ✅ `push origin main`, and the remote received it |
| recursive delete previews a real file list | ✅ and a dry run performs nothing |
| terminal streams a long burst losing nothing | ✅ **2000/2000 lines**, loop p50 13.5 ms |
| unlisted program refused, naming the rule | ✅ `programs.allowlist` |
| `shell=True` absent, lint enforces it | ✅ plus a security test, because a lint rule is one `# noqa` from advisory |
| project detection classifies all seven projects | ✅ eight directories, including an empty one that correctly says `unknown` |

## Three things that were nearly ticked off without being measured

This is the part worth reading. Each looked done and was not.

### 1. The terminal silently deleted its own output

`term.*` had 13 green tests and worked interactively. Measuring the ROADMAP criterion properly — 2000
numbered lines, so "no bytes dropped" is arithmetic — lost 226 of them.

Two plausible hypotheses were wrong and had to be eliminated by measurement (ConPTY coalescing;
a slow pump). The real bug: **`term.read` emptied the whole buffer and returned only its last
16 KB.** The rest was thrown away — which is why the missing lines were always the oldest, and why
the drop counter honestly reported zero. `truncated` was a field that meant "we deleted some" while
reading like "there is more".

The same run surfaced a second bug: **the child's structlog output was going to stdout, which is the
toolhost's protocol pipe.** Benign only by luck — a log line that happened to be valid JSON would
have been parsed as a tool response.

Full account: [`2026-08-21-terminal-loses-output.md`](../logs/development/2026-08-21-terminal-loses-output.md).

### 2. Tool selection picked the wrong tool, confidently

The first live run of *"commit my changes"* selected `git.add`, staged the files, and **reported
success**. A plausible adjacent wrong action is a small model's characteristic failure, and it is far
worse than a crash because it looks like it worked.

Fixed by measuring first: `scripts/eval_selection.py`, 18 cases, two of which must select *nothing*.
Baseline **83.3%** — and two of the three misses were the same thing, the model reaching for the
nearest tool instead of declining. Few-shot examples (with "none" appearing twice) plus summaries
rewritten to *distinguish* neighbouring tools took it to **100%**, at no latency cost.

That change then exposed a silent truncation: selection had been borrowing `ROUTE`'s context budget,
whose shape is inverted, and **the tool descriptions were being cut off before the model saw them**.

Full account: [`2026-08-21-selection-accuracy.md`](../logs/development/2026-08-21-selection-accuracy.md).

### 3. `fs.delete` declared `dry_run=True` and ignored it

The registry *requires* `dry_run` for T3 so the confirmation card can show a real preview. The
handler deleted regardless. Fixed — and it exposed a circularity: a dry run required the approval it
existed to inform. A dry run now skips the approval requirement, which puts an obligation on the
contract instead: **`dry_run=True` means the call performs nothing**, network egress included. That
is why `git.push`'s preview is computed from local refs rather than `--dry-run`, which would contact
the remote.

## What was built

```
policy/programs.py   the program allowlist: pinned at load, deny-wins, batch-argv rule
policy/apps.py       the app catalogue: aliases, never paths
tools/proc.py        the one place a process is spawned; argv lists, caps, blobs
tools/git.py         9 tools; porcelain v2, never scraped prose
tools/dev.py         4 tools; junit-xml / json reports, and one honest `scraped`
tools/terminal.py    4 tools on ConPTY; a reader thread per session
tools/apps.py        1 tool, in the parent, detached — the only exception to ADR-0003
core/projects.py     detection by marker file; test/build/lint commands per project
core/approvals.py    the approval.requested / approval.resolved round trip
router/selection.py  one tool from an enum, one string, everything else built in code
```

## Decisions that came out of building it

- **[ADR-0018](DECISIONS.md#adr-0018--a-launched-application-is-not-a-tool-call)** — a launched
  application cannot live in the Job Object. HALT must not close your editor. The alternative
  (`JOB_OBJECT_LIMIT_BREAKAWAY_OK`) would let *anything* the child spawns escape HALT, which is not
  a trade worth making to open Explorer. **ADR-0003 confirmed** against the implementation in the
  same pass, with the measurements in its record.
- **`fs.write` now means "writes a path the contract names".** That is what makes an undo plan
  possible. A tool whose writes happen inside a spawned program declares `proc.spawn` instead —
  enforced in the registry, because `dev.build` claiming `fs.write` would have promised a backup
  nothing could take.
- **`term.write` is its own capability**, not `proc.spawn`. `docs/SECURITY.md#4b` said so and was
  right: a spawn is an argv the allowlist can inspect, and a line of shell input is not.
- **Hidden tools are a category the contract needed.** `git.undo` must exist in the registry and must
  never be selectable.

## What the model is allowed to decide

Worth restating, because 100% on 18 cases is not a licence to trust it:

- it picks a **name from an enum** built out of intent-filtered candidates, so an off-menu name is
  unspellable rather than validated afterwards;
- it supplies **one string** that is inherently text — a commit message, a test filter;
- **it never writes a path.** The project path is composed from a root the runtime owns, after the
  classifier has checked the name against the registry.

There is a test that puts `C:\Windows\System32` in the model's free-text field and shows it goes
nowhere. That property does not depend on the accuracy number, which is the point of it.

## Known gaps, honestly

- **Only 11 of 26 tools are reachable from a routed turn.** Selection offers a tool only when its
  arguments can be built from *(project, one string)*. `dev.execute`, `term.write`, `fs.write` and
  `git.push` need an argv, a command, file content or a remote. They are callable through the API
  and will be reachable from a plan; half-filling them would mean inventing something.
- **No UI for approvals yet.** The events are emitted and the WS command works; the card is Phase 4.
- **Terminal sessions die with the toolhost.** Correct — a shell belongs in the Job Object — but it
  means a crash loses your session, and that should be visible in the UI rather than silent.
- **[OQ-13](OPEN_QUESTIONS.md#oq-13) (what approval rate causes prompt fatigue) is still an
  assumption.** Now measurable: T1 covers writes, commits, tests, builds and branches, so daily use
  should produce very few prompts. Worth counting once Phase 4 makes daily use real.
