# 2026-08-22 — tree-sitter chunking: better anchors, worse recall

Requirements 2 and 3 of [P5-T2](../../docs/current_task.md). Builds the tree-sitter chunker that
[RAG.md §3](../../docs/RAG.md#3-chunking) always specified, and re-measures the fixture set.

**The outcome is not the one the task expected.** tree-sitter names symbols far better — that
criterion is met outright — and on this corpus it *retrieves worse*, by two fixture cases, across four
builds. So it is built, tested, and **off**: `chunking.SYNTAX_AWARE = False`. §6 is the decision and
the reasoning; §3 is a measurement error worth more than the rest of the log, because the baseline had
silently moved underneath me and the comparison nearly lied.

## 1. What the regex got wrong

Across the corpus its most common "symbol" was `equal` (548 occurrences), and its second was
`useEffect` (219). Both are **calls**. A call that takes a callback opens a block, and to a line
matcher that is indistinguishable from a definition:

```ts
describe('the thing', () => {     // a call
export function ChangesView() {   // a declaration
```

Every test file in the corpus was being shredded into assertion-sized fragments anchored on the name
of the assertion.

## 2. What the syntax tree gives

| | regex | tree-sitter |
|---|---|---|
| Most common anchor | `equal` — a call | `main` — a real function |
| Second | `useEffect` — a call | `(file)` |
| Control-flow keywords as anchors | `if`, `for` | none |
| Ancestry | none | `TokenService.signAccessToken`, `ReviewRepository.delete` |

Names now come from the grammar's `name` field, so a keyword or a call expression *cannot* become
one. The acceptance criterion — no control-flow keyword and no call expression as an anchor across
the whole corpus — is met.

The surviving suspicious-looking anchors were checked individually rather than waved through:
`equal` × 14 is a hand-rolled `function equal(label, actual, expected)` test helper, `delete` × 2 are
`ReviewRepository.delete` and `ApiClient.delete`, and `describe` × 25 is
`function describe(name: string): void`. All are real declarations. That is the point of fixing the
*mechanism* rather than banning a list of names.

## 3. The baseline had moved (the measurement nearly lied)

First tree-sitter run: **recall@5 71%**, against a recorded baseline of 81%. Before believing a
10-point regression, the two runs were checked for a shared corpus. They did not have one.

`config/collections.yaml` roots a collection at `C:/Projects`, which contains ORACLE itself, and the
walk skips untracked files. **Committing the phase-5 work is what made `tests/fixtures/retrieval/cases.yaml`
indexable** — a file that contains all 21 fixture questions verbatim. It promptly began answering
them:

```
cases whose top-5 contained the fixture file: 12 of 21
  ru-two-users-one-agent      #2  ORACLE/tests/fixtures/retrieval/cases.yaml
  ru-feature-entitlement      #2  ORACLE/tests/fixtures/retrieval/cases.yaml
  en-secrets-at-rest          #4  ORACLE/tests/fixtures/retrieval/cases.yaml
  ...
```

This is a leak in the *measurement*, not a fault in retrieval. A file of questions is a legitimate
corpus document and production should keep returning it; it simply cannot be allowed to answer
itself. `measure()` now discards it before ranking — from a wider candidate list, so each case still
gets five real chances — and prints how many slots it took.

The lesson generalises past this one file: **a benchmark whose corpus contains the benchmark is not
measuring the system.** The corpus here is the developer's own machine, so this will recur every time
a fixture, a dev log or a report about retrieval gets committed.

## 4. The control run

With the leak closed, the two numbers still disagreed — so the corpus was held fixed and only the
chunker varied, by disabling `blocks_for` and rebuilding. This is the comparison the task file
demanded ("do not accept *it looks better*"), and it is affordable only because the embedding cache
landed first: the control build was a **100% cache hit and finished in 45 s** instead of 43 minutes.

| Same corpus, same fixtures, same `measure()` | recall@5 | crosslang | semantic | lexical |
|---|---|---|---|---|
| regex (control) | **81%** | 62% (5/8) | 90% | 100% |
| tree-sitter, first version | **76%** | 62% (5/8) | 80% | 100% |

Identical except for one case: `en-secrets-at-rest`, *"how are credentials encrypted at rest and
where does the key come from"*.

## 5. The actual bug: trivia belongs to what follows it

Both indexes cut `SecretVaultService.ts` into 23 chunks. The anchors tell the story:

```
regex          constructor · dataDir · key · encrypt · decrypt · getSecret · deleteSecret · getStatus
tree-sitter    SecretVaultService · SecretVaultService · SecretVaultService.encrypt · SecretVaultService …
```

Seven chunks had lost their method name to the bare class name. Dumping the raw blocks showed why:

```
    76  (file)                  /** AES-256-GCM's nonce. 12 bytes is the size the mode is defined for. */
    20  IV_BYTES                const IV_BYTES = 12;
     9  (file)                  export
    61  SecretSettingKey        type SecretSettingKey = (typeof SECRET_SETTING_KEYS)[number];
```

The grammar reports a node starting at `type SecretSettingKey`, not at the `export` in front of it,
and a doc comment is a *sibling* of the declaration rather than part of it. Emitting the gap before
each declaration therefore:

* **severed every `/** … */` from what it explains** — and for this fixture, the answer to "where does
  the key come from" lives in the prose of a doc comment, not in the code under it; and
* **stranded the bare word `export` as a block**, which is not valid source and reads as nothing.

`_pack` then merged each orphaned comment forward into the following method, and the chunk inherited
the *comment's* anchor — the enclosing class — rather than the method's.

`_lead_start` fixes it: a declaration begins at the trivia that introduces it. Walking backwards in
bytes over comment, decorator and blank lines, with one hard rule — **a gap that does not end in a
newline shares its last line with the declaration**, so a source line is never cut in half. That
single rule is what recovers `export type X = ...`.

```
   96  IV_BYTES        /** AES-256-GCM's nonce. 12 bytes is the size the mode is defined for. */
   70  SecretSettingKey  export type SecretSettingKey = (typeof SECRET_SETTING_KEYS)[number];
  129  SecretVaultError  /** A vault failure that callers can branch on without matching message text */
```

**It made recall worse: 76% -> 71%.** Two more rules came out of chasing that, each a real defect
found by looking at the chunk that should have won:

* **Trivia attaches only when it is *adjacent*.** A blank line between a comment and the next
  declaration is the author saying the comment is not about it. `SecretVaultService.ts` opens with a
  `/** Local Secret Vault (P9-01) ... */` block describing the whole module — the prose that answers
  the fixture — and gluing it to `VAULT_ENVELOPE_PREFIX` buried it among `const X = 12;` lines. With
  the rule restored to contiguous-only, the file-header chunk came back and two cases recovered
  (`en-yaml-billion-laughs` dense rank 16 -> 5, `en-secrets-at-rest` back into the candidate set).
* **Punctuation is not a block.** `public_field_definition` stops before its `;`, so a class of ten
  fields leaves ten one-character spans — and a block gets its anchor written above it, so a chunk of
  `RelayClient` fields read `RelayClient` / `;` / `RelayClient` / `;` with the code crowded out. Those
  now fold into the statement in front of them.

Both are correct. **Neither recovered the recall.**

## 6. The decision: better anchors, and the line matcher still ships

Four builds, same corpus, same fixtures, same `measure()`:

| chunker | recall@5 | crosslang | semantic | lexical |
|---|---|---|---|---|
| **line matcher (control)** | **81%** | 62% (5/8) | 90% | 100% |
| tree-sitter | 76% | 62% (5/8) | 80% | 100% |
| + trivia attached | 71% | 50% (4/8) | 80% | 100% |
| + contiguity rule | 71% | 50% (4/8) | 80% | 100% |
| + punctuation coalescing | 71% | 50% (4/8) | 80% | 100% |

The acceptance criterion is `>= 80%` **and no worse than the pre-tree-sitter number**. It is not met,
so `chunking.SYNTAX_AWARE` is **False** and the line matcher is what runs.

Exactly **two fixtures** separate the two chunkers, and in both the line matcher wins by *accident*:
it packs neighbouring text into one chunk, so a file's header prose lands beside the code it
describes, and a conceptual question ("how are credentials encrypted at rest and where does the key
come from") matches the paragraph rather than the method. tree-sitter deliberately separates those,
which is right for citing a symbol and wrong for answering a question about a module.

Two things follow, and it is worth being precise about which is which:

* **What is measured:** on this fixture set, symbol-precise boundaries retrieve worse than
  neighbour-packed ones, by two cases.
* **What is not:** whether that generalises. Twenty-one cases at 4.8 points each cannot adjudicate a
  two-case difference in *either* direction — including the direction I would have preferred. The
  four builds above are consistent in sign, which is the only reason this is written as a finding
  rather than as noise.

So the code stays, tested and one flag from being on, and the decision waits for the expanded fixture
set that requirement 6 already calls for. Deleting it would throw away the anchor quality, which is
not in doubt; switching to it would be marking something done that the measurement says is not.

The honest summary of my own three fixes: each corrected a real defect in the chunk text, each was
verified by looking at the bytes, and **none of them moved the number the criterion is about.** That
gap between "visibly better" and "measurably better" is the whole reason the criterion is a number.

## 7. Two earlier bugs, for the record

* **A `MIN_GAP_BYTES = 40` threshold discarded source text.** `import crypto from 'crypto';` and
  `export class TokenService {` are both shorter than 40 bytes and were being dropped as "a blank
  line and a brace". Chunking may merge text or re-anchor it; it may never discard it. The threshold
  is gone, and small blocks are `_pack`'s problem, not the parser's.
* **Returning `None` for a file with no declarations re-introduced the original bug.** A test file
  that is nothing but `describe(...)` and `it(...)` declares nothing — and handing it to the line
  splitter anchored it on exactly those calls again, on precisely the files that suffered worst.
  `None` now means only "there is no grammar for this suffix". "This file declares nothing" is an
  answer, not a failure.

## 8. Cost

`tree-sitter-language-pack` is one abi3 wheel covering all 18 grammars this corpus needs, verified to
load on this machine before code was written against it — the OQ-09 rule applied to a new dependency.
Ledger entry in [TECH_STACK.md](../../docs/TECH_STACK.md#phase-5-dependency-ledger--2026-08-22).

Parsing is not the cost that matters; embedding is. Re-chunking moved most boundaries, so most chunk
texts were new and had to be embedded — a 31-minute rebuild at 45% cache hit. That is the case
requirement 1 was sequenced first to make affordable, and it is why the control run cost 45 seconds.
