# 2026-08-21 — OQ-04: Windows path resolution and the filesystem sandbox

Resolves [OQ-04](../../docs/OPEN_QUESTIONS.md#oq-04).
**Answer: `os.path.realpath` is sufficient — but only if you never shortcut on
`is_symlink()`, and never match a rule against an unresolved path.**

Fixture: a real tree with `mklink /J`, not mocks. A mock would have encoded the exact
belief that turned out to be false.

## Finding 1 — junctions need no admin; symlinks do

```
mklink /J allowed\junc ..\outside   ->  Junction created
mklink /D allowed\dsym ..\outside   ->  You do not have sufficient privilege
mklink    allowed\fsym ...secret.txt->  You do not have sufficient privilege
```

Developer Mode is off and the shell is unelevated. **An unprivileged attacker on this
machine can create junctions but not symlinks**, which makes the junction the realistic
escape vector and the symlink the theoretical one. The security suite therefore treats
junction tests as required and symlink tests as skippable.

## Finding 2 — `is_symlink()` is False for a junction

```
Path.is_symlink()                False
os.path.islink()                 False
st_file_attributes REPARSE bit   True
st_reparse_tag                   0xa0000003   (IO_REPARSE_TAG_MOUNT_POINT)
```

**This is the bug that would have shipped.** The natural optimisation — "only call
`realpath` if the path is a link" — walks straight past every junction. Detection must
use `st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT`.
`test_junction_is_invisible_to_is_symlink` exists purely to keep this fact visible.

## Finding 3 — what `realpath` does and does not do

| input | `realpath` | verdict |
|---|---|---|
| junction → outside | fully resolved to the real target | ✅ escape detectable |
| `sub\..\normal.txt` | collapsed correctly | ✅ no false positive |
| `PROGRA~1\x.txt` (8.3) | expanded to the long name | ✅ |
| `normal.txt.` / `normal.txt ` | trailing dots/spaces stripped | ✅ |
| upper-case path | normalised to on-disk case | ✅ |
| `normal.txt:hidden` (ADS) | **stream preserved** | ❌ must reject explicitly |
| `\\?\C:\...`, `\\.\C:`, `\\host\C$\...` | **returned unchanged** | ❌ must reject explicitly |

`os.path.abspath` resolves none of it and must never be substituted.

## Finding 4 — ADS is a live capability, not a curiosity

```
open("normal.txt:hidden","w").write("PAYLOAD")   -> succeeds
read back                                        -> 'PAYLOAD'
normal.txt size on disk                          -> 2 bytes, unchanged
```

Data hides in a file whose size and mtime look untouched, and `realpath` will not save
you. Rejected by inspection, before resolution.

## Finding 5 — 8.3 names are enabled on this volume

`Program Files Like` → `PROGRA~1`. Harmless *because* we resolve before matching. Had
the deny-list been matched against the raw string, `PROGRA~1` would have dodged a rule
written against the long name. Same class of bug as the trailing dot.

## Consequences for the implementation

Ordering in `_reject_syntax` is load-bearing and non-obvious:

1. NUL byte
2. **device/UNC prefix** — before wildcards, because `\\?\` contains `?` and
   `\\host\C$\` contains `$`; checking wildcards first produces a correct denial for
   the wrong reason
3. ADS (colon after the drive letter)
4. wildcards, then env syntax

Then resolve, then **re-run the syntax checks on the resolved path** (a reparse point
can reintroduce a device path), then match deny rules, then match scopes.

Containment uses **path components, not string prefixes**: `startswith` accepts
`C:\Projects-evil` as inside `C:\Projects`. `test_sibling_prefix_is_not_containment`
covers it.

## Bugs the suite caught in my own code

- **The ADS regex rejected every absolute path.** `^(?:[A-Za-z]:)?[^:]*:(.*)$` looks
  right, but the optional group lets it backtrack and match the drive-letter colon
  itself. 20 tests failed at once. Replaced with an explicit slice past `X:`.
  Regex for security predicates is a bad default; slicing is auditable.
- **`....//....//` is not traversal on Windows.** I wrote a test asserting it was
  rejected; it is an ordinary (odd) directory name. The correct assertion is *no
  escape*, not *rejection* — asserting rejection would have baked a false belief about
  Windows into the suite.

## Result

`tests/security/test_path_traversal.py`: **38 passed, 1 skipped** (symlink case, needs
admin). Includes a Hypothesis property test over 300 generated adversarial paths
asserting the resolver either raises or returns a path provably inside a scope.
