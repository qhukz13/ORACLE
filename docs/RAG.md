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

```yaml
# config/collections.yaml
collections:
  - id: projects
    kind: code
    roots: ["C:/Projects"]
    include_projects: [Asterim, SCRAPSHIFT, GameRecs, Source2DemViewer, asterim-pipeline]
    respect_gitignore: true
    exclude:
      - "**/node_modules/**"     # ~1000s of files, zero value
      - "**/target/**"           # Rust build output: 3,915 files in one project
      - "**/.git/objects/**"
      - "**/dist/**"
      - "**/build/**"
      - "**/*.lock"
      - "**/*.min.js"
      - "**/coverage/**"
    max_file_bytes: 1_000_000

  - id: notes
    kind: markdown
    roots:
      - "C:/Users/qhukz/Documents/AI/ML Learning"       # 157 notes
      - "C:/Users/qhukz/Documents/ObsidianNotes"
      - "C:/Users/qhukz/Documents/MLAI NOTES/ML/AI"
    obsidian: true

  # NOT indexed, deliberately: Documents/ at large, Downloads, AppData,
  # game data, anything outside a declared scope.
```

### Per-type policy

| Type | Semantic index | Lexical index | Notes |
|---|---|---|---|
| Source code | yes, symbol-level | yes | tree-sitter chunks |
| Markdown / docs | yes | yes | heading-aware; the highest-value content here |
| Config (`json`,`yaml`,`toml`) | **no** | yes | embeddings of config are noise; exact search is what I want |
| Logs | **no** | yes, last 7 days only | huge, low-value, time-sensitive |
| PDFs | yes | yes | text layer only; **no OCR in v1** |
| Notebooks | yes (source cells) | yes | outputs stripped — they contain data, not knowledge |
| Lockfiles, minified, generated | no | no | excluded entirely |
| Binaries, media, archives | no | no | metadata only |
| Git history | commit messages + diffstat only | yes | full diffs are too large and too low-signal |

The recurring principle: **embeddings are for prose and semantics; exact search is for identifiers and
config.** Embedding a `tsconfig.json` produces a vector that matches everything and means nothing.

---

## 3. Chunking

Chunk boundaries determine retrieval quality more than the embedding model does.

| Type | Strategy | Target size |
|---|---|---|
| **Code** | tree-sitter → one chunk per function/class/method, with the enclosing signature path retained; oversized bodies split at statement boundaries with an overlap of the signature | 200–800 tok |
| **Markdown** | split at headings, keeping the heading path (`# Setup > ## Auth > ### Tokens`) in every chunk | 200–600 tok |
| **PDF** | page-aware, merged into paragraph blocks | 300–700 tok |
| **Plain text** | recursive character split with 15% overlap | 300–600 tok |

Every chunk carries its ancestry. A function chunk knows it is
`Asterim / apps/server/auth/token.ts / class TokenService / refresh()`. That ancestry goes into the
chunk text *and* the metadata, because it improves both dense matching and human-readable citation.

**Obsidian specifics:** front-matter becomes metadata (tags, aliases); `[[wikilinks]]` are extracted
into a link table so retrieval can expand one hop to directly linked notes; `#tags` become filters.

---

## 4. Embeddings

**Default: `multilingual-e5-base` (768d), ONNX Runtime, CPU.** Rationale in
[TECH_STACK.md §4](TECH_STACK.md#4-knowledge) — Russian and English in one model, and CPU execution so
the 4 GB of VRAM stays dedicated to the router model. E5 requires `query:` / `passage:` prefixes; the
indexer and the retriever must agree on this, and a test asserts it (getting it wrong silently halves
quality and is a classic bug).

`EXPERIMENT NEEDED` [OQ-02](OPEN_QUESTIONS.md): compare against `bge-m3` on a mixed RU/EN fixture set;
evaluate Matryoshka truncation to 384d (halves storage and scan time, usually at minimal cost).

Embedding runs in a **separate process** at low priority so a reindex never stalls the event loop or
competes with an interactive turn.

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
path prefix) happens **before** the KNN scan, which is what keeps brute force cheap.

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

Retrieval is measured, not vibed. A fixture set of ~20 real questions with known-correct sources
lives in `tests/fixtures/retrieval/`:

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

---

## 9. Non-goals

- No cross-project "global brain" synthesis. Scoped retrieval, always.
- No automatic web ingestion. Prompt-injection funnel; needs its own design.
- No OCR in v1.
- No indexing of anything outside a declared collection root — including my home directory.
- No re-embedding on every save. Content-hash gating, always.
