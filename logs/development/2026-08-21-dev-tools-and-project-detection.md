# `dev.*`, project detection, and the interpreter that was the wrong one

**Date:** 2026-08-21 · **Task:** P3-T1, requirements 6 and 9

---

## What this was supposed to answer

"Run the Asterim tests" has to become an argv. Which argv depends on the project, and
the answer has to be **derived, not guessed** — running `npm test` on a project with no
test script produces a confusing failure and teaches the user that the tool is
unreliable.

## Detection is by marker file

Not by directory name, not by file extension census. A folder called `tests` proves
nothing; a `Cargo.toml` proves a great deal. The scan looks at the project root and one
level under `apps/`, `packages/`, `crates/`, `services/`, and never walks build output.

Live result against all eight directories in `C:\Projects`:

| project | kinds | primary test command |
|---|---|---|
| Asterim | node | `npm test --silent` |
| asterim-pipeline | node | `npm test --silent` |
| AsterimDesign | **docs** | (none — correctly) |
| GameRecs | python, node | `uv run pytest -q` in `apps/api` |
| GrowAMonster | **roblox** | (none — correctly) |
| MonsterGarden | **unknown** | (none — the directory is empty) |
| ORACLE | python, node | `uv run pytest -q` |
| Source2DemViewer | rust | `cargo test` |

Two classifications took a second pass:

- **AsterimDesign** first came back `unknown`. It is 96 markdown files, 6 images and 5
  JSON files — a real project by every human measure with nothing to build. The docs
  heuristic was rejecting it on the `.json`, so `.json` joined the inert-suffix list.
  That is safe *because the marker check runs first*: a `package.json` has already been
  found by the time the heuristic is consulted.
- **MonsterGarden** is `unknown`, and that is the right answer. The directory is empty.
  Reporting a kind for it would be an invention.

## The finding that mattered: the pinned `python` is *ours*

The program allowlist pins each program to an absolute path once, at load. Measured on
this machine:

```
programs.pinned  program=python  path=C:\Projects\ORACLE\.venv\Scripts\python.exe
```

That is **ORACLE's own virtualenv interpreter**, because `which` resolves against the
PATH of the process doing the pinning, and ORACLE runs inside its venv. Pinning is
correct and is not the bug — the bug would have been detection emitting
`python -m pytest` for somebody else's project, which would have tested their code
against *our* dependencies and usually failed with a confusing `ImportError`.

The fix is not to un-pin. It is to prefer `uv run`, which resolves the environment from
the directory it is invoked in, for **every** Python project rather than only
uv-managed ones. Detection now emits alternatives in preference order and `dev._pick`
takes the first whose program is actually available — because only the executing side
knows what is installed.

Generalised in the same change: a tool may declare a *menu* of programs it might need
(`dev.run_tests` names `uv`, `python`, `npm`, `cargo` and uses one). An unavailable
entry is omitted from the pinned map rather than failing the call. Refusing to run
Python tests because `cargo` is missing would be absurd, and nothing is loosened —
what can be spawned is still exactly what policy pinned.

## Structured results, and the one place we admit to scraping

| runner | asked via | `source` |
|---|---|---|
| pytest | `--junit-xml=<tmp>` | `junit-xml` |
| vitest | `--reporter=json --outputFile=<tmp>` | `json` |
| jest | `--json --outputFile=<tmp>` | `json` |
| cargo | nothing stable off nightly | `scraped` |

`cargo` is the honest exception and the result **says so** in a field. A number scraped
out of prose and a number parsed from a report are not equally trustworthy, and hiding
which one you have is how a wrong count becomes a confident claim.

Live, against ORACLE's own suite through a real toolhost:

```
runner=pytest source=junit-xml passed=1 failed=0 skipped=0 total=1 exit=0 dur=2.15s
command: uv.exe run pytest -q --junit-xml=...\oracle-tests-g1ia6ai9.report -k <filter>
log: D:\ORACLE\data\blobs\2026-08-21\2524efab2fb9__ORACLE-tests.log
```

Two details worth keeping:

- **The exit code is the arbiter, not the parsed counts.** A collection error produces
  zero failures and a non-zero exit; calling that a pass would be the most damaging
  thing this tool could do. `ok` comes from the exit code, always.
- **The report goes in TEMP, never in the project.** A tool that litters a repository
  with its own artefacts is a tool you stop trusting.

## A capability rule that was missing

`dev.build` writes into `dist/` — so the first version declared `fs.write`, and the
registry's reversibility check immediately caught it: *"mutates at T1 but declares no
undo"*. The check was right and the contract was wrong, but not in the obvious way.

`fs.write` now means, explicitly: **writes a path the contract names.** That is what
makes an undo plan possible — we know in advance which file to back up. A tool whose
writes happen *inside a spawned program* declares `proc.spawn` instead, which already
means "may write within its scope". Enforced in the registry: `fs.write` without a
`path_fields` entry is now a boot failure.

The alternative — letting `dev.build` claim `fs.write` and an undo — would have put a
promise in the journal that nothing could keep.

## Also settled

- **`proc.spawn` at T0 is legitimate** when the argv is fixed by the tool. `git status`
  spawns and changes nothing. It stops being legitimate the moment the model picks the
  program, which is why `dev.execute` is T2 by contract and not only by policy.
- **`sys.processes` moved onto the allowlist** and dropped its bespoke pin. It now
  declares `proc.spawn` truthfully, which takes it out of the read-only build — the
  gate refuses `proc.spawn` in lockdown, so listing it there would have advertised
  something that could never run.
