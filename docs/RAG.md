# ORACLE — Project Knowledge & Retrieval

How ORACLE knows about my projects and notes. Agent-side memory (facts, preferences, prior attempts)
is a different subsystem — see [MEMORY.md](MEMORY.md).

## 1. The corpus is small, and that changes everything

Measured on 2026-08-21, not estimated:

| Source | Size |
|---|---|
| Asterim (git-tracked) | **798 files** — 267 `.ts`, 190 `.md`, 91 `.tsx`, 62 `.js`, 30 `.json` |
| Source2DemViewer | Rust; `target/` holds 3,915 files that must never be indexed |
| GameRecs, GrowAMonster, asterim-pipeline, AsterimDesign | a few hundred each |
| `Documents/AI/ML Learning` (Obsidian) | **157 Markdown notes** |
| `Documents/ObsidianNotes` | 3 notes |
| `Documents/MLAI NOTES/ML/AI` | 1 note + 1 PDF (32 MB) |

**Total: a few thousand documents → roughly 30k–80k chunks.**

**Corrected 2026-08-22, by building the index for real:
1,330 documents → 10,287 chunks, of which 9,385 carry an embedding, in an 85 MB file.** The
30k–80k estimate was three to eight times too high, because it counted documents the per-type policy
in §2 never sends to the chunker and chunks smaller than the ones §3 actually produces. The
conclusion it supported gets *stronger*, not weaker: this corpus is small — and measured retrieval
over it is **149 ms p50, 203 ms p95**, brute force included.

At that scale, brute-force vector scan is tens of milliseconds and an ANN index is pure overhead. This
is the entire justification for sqlite-vec over Qdrant/pgvector
([ADR-0006](DECISIONS.md#adr-0006--sqlite-only-storage-two-files-sqlite-vec--fts5)). It also means
**retrieval quality, not retrieval speed, is the only thing worth optimising.**

The second observation matters just as much: `C:\Users\qhukz\Documents` also contains Paradox
Interactive saves, League of Legends configs, Arma 3 data and a 32 MB PDF. Any design that says
"index my Documents folder" is wrong on this machine.

---

## 2. What gets indexed

**Explicit opt-in per collection. There is no "index everything" mode.**

The live declaration is [`config/collections.yaml`](../config/collections.yaml); it is a file a human
edits, not something the agent writes. Its shape:

```yaml
deny:                            # checked first, for every collection, on the PATH —
  - "**/Passwords/**"            # so a secret is not read in order to learn it is a secret
  - "**/*.env"
  - "**/.ssh/**"
  # …

collections:
  - id: projects
    kind: code
    roots: ["C:/Projects"]
    include_projects: [Asterim, AsterimDesign, GameRecs, GrowAMonster, ORACLE,
                       Source2DemViewer, asterim-pipeline]
    respect_gitignore: true
    exclude:
      - "**/node_modules/**"     # ~1000s of files, zero value
      - "**/target/**"           # Rust build output: 3,893 files, in a project with no .git
      - "**/.git/objects/**"
      - "**/dist/**"
      # …
    max_file_bytes: 1_000_000

  - id: notes
    kind: markdown
    roots:
      - "C:/Users/qhukz/Documents/AI/ML Learning"       # 157 notes
      - "C:/Users/qhukz/Documents/ObsidianNotes"        # see `deny` — this root has a Passwords/ folder
      - "C:/Users/qhukz/Documents/MLAI NOTES/ML/AI"
    obsidian: true

  # NOT indexed, deliberately: Documents/ at large, Downloads, AppData,
  # game data, anything outside a declared scope.
```

Three corrections that the first real walk of this machine forced, on 2026-08-22:

* **A root is not a promise.** `Documents/ObsidianNotes` — named as a notes root in the original
  draft of this section — contains a `Passwords/` folder holding `Passwords.md` and
  `Bank accounts.md`. Hence the top-level `deny` list, which is matched against the path before the
  file is opened, and which no per-collection `include` can override.
* **`SCRAPSHIFT` does not exist** on this machine; `AsterimDesign`, `GrowAMonster` and `ORACLE` do,
  and `MonsterGarden` is an empty directory. The list above is the one that matches the disk.
* **Exclusion has to happen during traversal, not after it.** `Path.rglob` enumerates
  `node_modules` in full before any filter can reject it, and on Asterim that walk took longer than
  embedding the entire corpus. The walker prunes directory names as it descends — the same rule
  §6 states for the watcher: drop before hashing, not after.

### Per-type policy

| Type | Semantic index | Lexical index | Notes |
|---|---|---|---|
| Source code | yes, symbol-level | yes | line-matched blocks; tree-sitter is built but off (see §3) |
| Markdown / docs | yes | yes | heading-aware; the highest-value content here |
| Config (`json`,`yaml`,`toml`) | **no** | yes | embeddings of config are noise; exact search is what I want |
| Logs | **no** | yes, last 7 days only | huge, low-value, time-sensitive |
| PDFs | yes | yes | text layer only, **no OCR**; page-anchored; always `local_foreign` (§below) |
| Notebooks | yes (source cells) | yes | outputs stripped — they contain data, not knowledge |
| Lockfiles, minified, generated | no | no | excluded entirely |
| Binaries, media, archives | no | no | metadata only |
| Git history | commit messages + diffstat only | yes | full diffs are too large and too low-signal |

The recurring principle: **embeddings are for prose and semantics; exact search is for identifiers and
config.** Embedding a `tsconfig.json` produces a vector that matches everything and means nothing.

**Every PDF is `local_foreign`**, unconditionally. Nobody writes a PDF in Obsidian: every one in this
corpus is something acquired — a textbook, a paper, a datasheet — so it is text by someone else, which
is exactly what the provenance flag means ([SECURITY.md §6](SECURITY.md#6-prompt-injection-and-taint-tracking)). The
rule is a generalisation and it is the one that fails safe: being wrong escalates the policy tier of a
plan built on the content, and never relaxes it.

---

## 3. Chunking

Chunk boundaries determine retrieval quality more than the embedding model does.

| Type | Strategy | Target size |
|---|---|---|
| **Code** | tree-sitter → one chunk per function/class/method, with the enclosing signature path retained; oversized bodies split at statement boundaries with an overlap of the signature | 200–800 tok |
| **Markdown** | split at headings, keeping the heading path (`# Setup > ## Auth > ### Tokens`) in every chunk | 200–600 tok |
| **PDF** | page-aware, packed across pages, anchored `p. 12` | 300–700 tok |
| **Plain text** | recursive character split with 15% overlap | 300–600 tok |

Every chunk carries its ancestry. A function chunk knows it is
`Asterim / apps/server/auth/token.ts / class TokenService / refresh()`. That ancestry goes into the
chunk text *and* the metadata, because it improves both dense matching and human-readable citation.

**The code row is aspirational, and `chunking.SYNTAX_AWARE` is `False`.** The tree-sitter chunker is
built and tested; a line matcher is what runs. **The line matcher names symbols far worse and
retrieves better** — by two fixture cases out of 21, consistently across four builds (81% against
71-76%). It wins by accident: it packs neighbouring text together, so a
file's header prose lands beside the code it describes and a conceptual question matches the
paragraph. Twenty-one cases cannot settle a two-case difference, so the decision waits on the
expanded fixture set, and the flag is one line.
[Log](../logs/development/2026-08-22-treesitter-chunking.md).

**Three rules the syntax tree does not give you for free**, kept for when it is switched on. Each was
found by measuring, not by reading the tree:

1. **A declaration starts at the trivia that introduces it.** The grammar reports a node starting at
   `type Foo`, not at the `export` in front of it or the `/** … */` above it. Cutting at the node's
   own start severs a doc comment from what it documents.
2. **…but only if they are adjacent.** A blank line between a comment and the next declaration is the
   author saying the comment is not about it. A file's leading `/** … */` describes the *file*, and
   gluing it to whichever constant happens to come first buries the prose that explains the module.
3. **Punctuation is not a chunk.** A grammar's field node stops before its `;`, so a class of ten
   fields leaves ten one-character spans. Each would otherwise get its own anchor line in the chunk
   text and crowd the code out.

Under all three: **chunking may merge text or re-anchor it, and may never discard it.** Asserted
per-file, byte for byte, in `tests/test_rag_treesitter.py`.

**Obsidian specifics:** front-matter becomes metadata (tags, aliases); `[[wikilinks]]` are extracted
into a link table so retrieval can expand one hop to directly linked notes; `#tags` become filters.

---

## 4. Embeddings

**Default: `bge-m3` (1024d), ONNX Runtime, CPU** — since 2026-08-24; `multilingual-e5-base` (768d)
before that, and still one line away. Rationale in
[TECH_STACK.md §4](TECH_STACK.md#4-knowledge) — Russian and English in one model, and CPU execution so
the 4 GB of VRAM stays dedicated to the router model. E5 requires `query:` / `passage:` prefixes and
bge-m3 requires none; the indexer and the retriever must agree on whichever is in force, and a test
asserts it (getting it wrong silently halves
quality and is a classic bug).

**Confirmed by measurement, [OQ-02](OPEN_QUESTIONS.md#oq-02), 2026-08-22**
([log](../logs/development/2026-08-22-oq02-embeddings.md)). The default was right; two of the
suggested economies were not, and both are now rejected with numbers:

Recall from a 3,000-chunk sample so every candidate saw identical chunks; throughput from a
dedicated idle-machine run, because the first figures were taken under varying load and
overstated the winner's margin:

| | dense r@5 | hybrid r@5 | RU→EN r@5 | chunks/s |
|---|---:|---:|---:|---:|
| **`e5-base`, 768d** | 81% | **90%** | 75% | **4.71** |
| truncated to 384d | 71% | 81% | 62% | 4.71 |
| int8 (`_avx512_vnni` export) | 52% | 76% | 62% | 4.59 |
| `e5-small`, 384d | 76% | 71% | 38% | 7.95 |
| `bge-m3`, 1024d | 90% | 95% | **100%** | 1.37 |

**On the full corpus, `e5-base` scores 81% — one point over the gate — and 62% on the
Russian questions.** The sample overstated it by 9 points overall and 13 on the
cross-language column. `bge-m3` is the likely fix at 3.4x the indexing cost — the
comparison that settles it was run on 2026-08-24 and is below.

**Corrected again, 2026-08-22.** Every Russian figure in the table above comes from a set
of **eight** questions. Expanding it to 25 put `e5-base` at **36% on Russian and 55%
overall** — the small set overstated it by 26 points, on top of the 13 the sample had
already overstated. The English and lexical columns are unaffected.

### Settled 2026-08-24: `bge-m3`, and the gate was scoring it wrong

Both candidates built over the same full corpus on the same day, measured by the same code
over all 38 fixtures ([log](../logs/development/2026-08-24-oq02-bge-m3.md)):

| embedding | fusion gate | recall@5 | crosslang | p95 | full rebuild |
|---|---|---:|---:|---:|---:|
| `e5-base` | as shipped | 55% | 36% | 271 ms | 42.8 min cold |
| `e5-base` | fixed | 55% | 36% | 260 ms | 42.8 min cold |
| `bge-m3` | as shipped | 53% | 32% | 401 ms ✗ | ~2.5 h cold |
| **`bge-m3`** | **fixed** | **61%** | **44%** | 332 ms | ~2.5 h cold |

**Against the retrieval code as it shipped, `bge-m3` loses.** The lexical gate below was
admitting BM25 on 38 questions out of 38, and fusion can only displace a correct dense hit
that exists — so an unfiltered lexical list cost `bge-m3` twelve points of cross-language
recall and `e5-base` nothing. The 2026-08-22 conclusion that the Russian failures *are* the
embedding was read through that same instrument.

**`DEFAULT` is `bge-m3` from 2026-08-24.** The switch was taken with the gate fix, because
neither is worth much without the other: `bge-m3` through the old gate scored 53%, below
the model it replaced. It costs ~3 GB resident instead of ~1.5 GB and a full rebuild.
`e5-base` keeps its `ModelSpec`, and `KnowledgeStore.bind` refuses an index built by the
other model — so going back is a rebuild, not a silent regression.

**None of this reaches the gate.** 61% against 80%, with 7 of 25 Russian cases never
entering the candidate set at all — a shape no reranker can fix. That is now
[OQ-18](OPEN_QUESTIONS.md#oq-18).

* **Do not truncate.** Matryoshka truncation costs 9 points here, not "minimal": `multilingual-e5-base`
  is *not* Matryoshka-trained, so its first 384 dimensions are half an embedding rather than a
  smaller one. The saving is 4 MB.
* **Do not use the published int8 export.** It gains 13% throughput on this CPU — which has AVX2 but
  no AVX-512 and no VNNI, so the export's own kernels do not apply — and loses 29 points of dense
  recall, falling below BM25 alone.

Two properties are load-bearing and each is asserted rather than assumed. E5 requires
`query:` / `passage:` prefixes, and the indexer and retriever must agree — `Embedder.encode` takes the
role as a required argument for that reason. And **~20% of chunks exceed the 512-token limit and are
silently truncated** (`TO VERIFY` in `rag/chunking.py`: measure the cost before building a
token-aware splitter).

Embedding runs off the event loop at low priority so a reindex never stalls an interactive turn.
A full rebuild is **~1 hour** on this CPU, not minutes — see §6.

---

## 5. Hybrid retrieval

Dense-only retrieval fails on exactly the queries a developer asks most — exact identifiers, error
strings, file names. Lexical-only fails on conceptual questions. So: both, always.

```
query
 ├─▶ dense:  embed → sqlite-vec KNN (k=30), pre-filtered by metadata
 └─▶ lexical: FTS5 BM25 (k=30), same filters
        │
        ▼
   Reciprocal Rank Fusion   score = Σ 1/(60 + rank_i)
        │
        ▼
   boosts:  same project ×1.3 · recently edited ×1.15 · heading/symbol match ×1.2
        │
        ▼
   diversity: max 3 chunks per file (stop one file from eating the budget)
        │
        ▼
   top 8 → Context Assembler (band 6)
```

RRF is chosen because it needs no score normalisation between two incomparable scoring systems and no
tuned weights — it is robust by construction. Metadata pre-filtering (project, collection, language,
path prefix) happens **before** the KNN scan, which is what keeps brute force cheap — in sqlite-vec
that means vec0 partition keys, so the predicate is pushed into the scan rather than applied after it.

### Fusion is conditional  `MEASURED 2026-08-24`

Robust by construction is not the same as harmless. Measured on the fixture set during
[OQ-02](OPEN_QUESTIONS.md#oq-02):

| dense model | dense only | + BM25 via RRF |
|---|---:|---:|
| `e5-small` | 76% | **71%** (−5) |
| `e5-base` | 81% | **90%** (+9) |
| `bge-m3` (full corpus, crosslang) | 44% | **32%** (−12) |

A Russian question against an English codebase shares no meaningful term with any document, so BM25
has nothing to say — **and says it in thirty ranked results anyway**. Unweighted RRF treats that as a
second opinion of equal standing, and it displaces correct dense hits out of the top 5. Fusion is not
free when one retriever is systematically blind to a query class.

**The cost scales with how good the dense half is**, which is the rule the three rows above spell
out and which took two attempts to read. Fusion can only displace a correct dense hit that exists;
`e5-small` had few, `e5-base` more, `bge-m3` many. So the fusion policy cannot be measured
independently of the embedding, and a model comparison run through a leaky gate measures the gate.

So the fix keeps RRF unweighted and gates its *input* instead. Three conditions, each measured, all
in `rag.retrieval.discriminating_terms` (`has_lexical_purchase` is the boolean form):

1. **The term appears in fewer than 10% of chunks** (with a `MIN_DF_CEILING` floor, so a small index
   does not gate everything out). A term in every document discriminates nothing, and OR-ing `the`,
   `we` and `is` is what made the lexical half cost 150 ms p50 — twice the brute-force vector scan.
2. **The term is in the script the corpus is written in.**  `MEASURED 2026-08-24`  Document frequency
   measures rarity in the corpus, not uninformativeness in the language: in a corpus that is 6%
   Cyrillic, `если` sits at 4% of the Russian and survives, and the genuinely rare Russian words
   match whichever unrelated project happens to be documented in Russian. Either way BM25 answers
   from the wrong neighbourhood. The rule is *minority*, not Cyrillic — a Russian-majority corpus
   gates out Latin. This supersedes the per-script denominator of 2026-08-22, which dropped the
   stopwords and moved recall not at all.
3. **The survivors cover at least 40% of the question's answerable terms.**  `MEASURED 2026-08-24`
   One Latin word inside a Russian sentence passed rule 2 alone and opened the gate, costing that
   fixture its top-5 slot under both embeddings — a one-term OR query ranked by BM25 is close to a
   document-frequency ordering. It is a *share*, not a count, so a bare `PairingService` lookup is
   100% of its question and still fuses. Terms absent from the corpus are excluded from the
   denominator: a word BM25 cannot answer is not a word BM25 declined to answer.

Tuning RRF's weights would have forfeited the property the algorithm was chosen for; dropping a list
that is provably noise does not. Rules 2 and 3 together are worth **+8 points of recall@5 and +12 on
the cross-language column** to `bge-m3`, cost `e5-base` nothing, and take the gate from opening on
38 queries out of 38 to 11 — which is also 69 ms off `bge-m3`'s p95.

**No reranker in v1.** Post-MVP, if the fixture set shows a gap: ONNX `bge-reranker-base` on CPU,
top-30 → top-8.

---

## 6. Incremental indexing

```
watchfiles (debounce 2 s)
   │
   ├─ ignored path?        → drop
   ├─ size/type excluded?  → drop
   ├─ hash unchanged?      → drop   (mtime alone is unreliable on Windows)
   └─ changed → parse → chunk → embed changed chunks only → upsert → prune orphans
```

Chunk identity is `sha256(file_path + chunk_ordinal + chunk_text)`, so an edit at the top of a file
does not invalidate every chunk below it. A full reindex is always available and always safe —
`knowledge.db` is disposable by design.

**Windows-specific care:** `watchfiles` (Rust `notify`) is used over `watchdog` for reliability;
events during a `npm install` arrive in the thousands, so debouncing and exclusion happen *before*
hashing; files locked by another process are retried with backoff rather than logged as errors.

### The measured cost, and why this path is load-bearing  `MEASURED 2026-08-22`

| | measured |
|---|---|
| Full rebuild — 1,330 docs, 10,287 chunks, 9,385 embedded, 85 MB | **42.8 min** |
| Incremental pass, nothing changed (1,330 documents) | **1.4–4.4 s** |
| Walk + chunk, no embedding | 25 s |
| Retrieval, p50 / p95 over the full corpus | **149 / 203 ms** |

The Phase 5 acceptance criteria asked for a full index in **under 10 minutes**. That target was set
before anything was measured and **is not achievable on this CPU** at any quality that passes the
recall gate — the corpus is ~3.7M tokens and `e5-base` sustains roughly 1,100 tokens/s here.

This is not a small correction. "A rebuild is cheap, so the incremental path is a convenience" and
"a rebuild is an hour, so the incremental path is the product" are different designs. It is the
second one. Disposability still holds — deleting `knowledge.db` is always safe — but it costs an
hour of background CPU, so the UI says so before starting one.

The 4.4 s figure is dominated by walking and hashing 1,330 files, not by embedding: a single changed
file costs that plus one document's worth of encode.

---

## 7. Attribution

Every retrieved chunk carries provenance, and every answer built from retrieval cites it:

```json
{ "chunk_id":"…", "collection":"projects", "project":"Asterim",
  "path":"apps/server/auth/token.ts", "anchor":"TokenService.refresh",
  "lines":[142,178], "score":0.83, "provenance":"local_owned",
  "indexed_at":"2026-08-21T09:02:11Z" }
```

Two reasons this is mandatory, not a nicety:

1. **Trust.** An uncited claim from a 2B model is worthless; a cited one is checkable in one click.
2. **Security.** `provenance` feeds taint tracking — a chunk from `node_modules` is `local_foreign`
   and taints the turn ([SECURITY.md §6](SECURITY.md#6-prompt-injection-and-taint-tracking)). This is
   why retrieval and security are not separable concerns.

---

## 8. Quality measurement

Retrieval is measured, not vibed. A fixture set of real questions with known-correct sources lives in
`tests/fixtures/retrieval/`. **38 as of 2026-08-22, of which 25 are Russian** — it began at 21 with
8 Russian, and that was too few to carry a decision: at n=8 one case is 12.5 points, and the model
choice in [OQ-02](OPEN_QUESTIONS.md#oq-02) turned on a margin smaller than that.

```yaml
- q: "как работает refresh токена в Asterim"     # Russian query, English codebase — the hard case
  expect_any: ["apps/server/auth/token.ts"]
- q: "where do we configure the relay Dockerfile"
  expect_any: ["Dockerfile.relay"]
- q: "what did I decide about the pipeline retry policy"
  expect_any: ["decisions.md", "docs/pipeline.md"]
```

Gate: **recall@5 ≥ 80%**. The suite runs on any change to chunking, embeddings, or fusion. Without it,
"improvements" to retrieval are guesswork — and cross-language retrieval (Russian question, English
code) is precisely the case that silently regresses.

Two rules the set has had to learn about itself:

* **Ground truth is read, not retrieved.** A fixture set derived from the system it measures agrees
  with whatever that system already does.
* **The benchmark must not be in the corpus.** `C:/Projects` contains ORACLE, so committing this file
  made it indexable — and a document containing all 38 questions verbatim promptly took a top-5 slot
  in 12 of them. `measure()` discards it before ranking. It stays a legitimate corpus document; it
  just cannot answer itself.

---

## 9. Non-goals

- No cross-project "global brain" synthesis. Scoped retrieval, always.
- No automatic web ingestion. Prompt-injection funnel; needs its own design.
- No OCR in v1.
- No indexing of anything outside a declared collection root — including my home directory.
- No re-embedding on every save. Content-hash gating, always.
