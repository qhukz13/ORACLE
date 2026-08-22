# 2026-08-22 — OQ-08: does FTS5 `unicode61` handle Russian acceptably?

Resolves [OQ-08](../../docs/OPEN_QUESTIONS.md#oq-08). Cheap to check, so it was checked while the
[OQ-02 benchmark](2026-08-22-oq02-embeddings.md) was running — both are the retrieval half of Phase 5
and the answers interact.

**Answer: yes, with two gaps, both of which are fixed at index time rather than by replacing the
tokenizer.**

## Setup

```
SQLite    3.50.4 (bundled with the uv-managed CPython 3.12.13)
Table     CREATE VIRTUAL TABLE t USING fts5(body, tokenize='unicode61')
Method    insert known rows, query, look at what comes back
```

## What works

| Query | Matches | |
|---|---|---|
| `токен` | `ТОКЕН доступа…` | **Cyrillic case folding works.** unicode61 folds Cyrillic, not only Latin. |
| `ОБНОВЛЕНИЕ` | `Обновление refresh…` | Both directions. |
| `MAX_YAML_DEPTH` | the row | |
| `yaml`, `depth` | the row | **Underscores are separators**, so a screaming-snake constant is searchable by its parts. |
| `entitlementGuard` | the row | Exact identifier match, as expected. |

## Gap 1 — no stemming, and Russian is inflected

`токен` does **not** match `токена`. Nothing folds Russian case endings, and this is the failure that
matters: a question is typed in whatever case the sentence needs, and the source says something else.

```
токен      -> []                              # "обновление токена доступа" not found
токена     -> ['обновление токена доступа']
токен*     -> ['обновление токена…', 'токены и сессии', 'токенизация текста…']
```

**Mitigation: prefix-expand Cyrillic query terms** — send `токен*`, not `токен`. It recovers the
inflections, and it costs precision in a specific, bounded way: `токен*` also matches `токенизация`
(*tokenization*), a different word that happens to share the stem. Truncation is a crude stemmer and
over-matches on purpose.

Two reasons to accept that cost rather than reach for a Russian snowball tokenizer:

* BM25's IDF already discounts a term that matched a large bucket of rows, and RRF fusion means the
  lexical list only has to be roughly right — the dense list is voting too.
* A custom FTS5 tokenizer is a C extension. That is a dependency and a build step, to fix a problem
  the fusion layer largely absorbs. Revisit if the fixture set shows Russian lexical misses that RRF
  does not rescue.

Only Cyrillic terms should be expanded. Prefix-expanding `get` would match half the corpus.

## Gap 2 — camelCase is not split, and this is a code corpus

`entitlement` does **not** match `entitlementGuard`. unicode61's `separators` option adds separator
*characters*; a case transition is not a character, so no configuration of unicode61 splits
`camelCase`. Confirmed rather than assumed.

**Mitigation: a second FTS5 column holding the split identifiers**, written at index time:

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(body, ident, tokenize='unicode61');
-- body:  the chunk verbatim
-- ident: identifiers exploded into parts — "entitlementGuard" -> "entitlement Guard"
```

An unqualified `MATCH` searches every column, so `entitlement` finds the row through `ident` without
the query needing to know the column exists. `ident:` remains available for a deliberately
identifier-scoped search.

Verified working, including `ident:access AND ident:token` finding `signAccessToken`.

This is the same transform `lex_tokens()` in `scripts/eval_embeddings.py` applies, and it is why the
BM25 baseline in the OQ-02 run scored 100% on the three `kind: lexical` fixtures.

## Verdict

`unicode61` stays. No custom tokenizer in v1. Two obligations follow into the implementation:

1. The FTS5 schema has an `ident` column, populated with split identifiers at index time.
2. The query builder prefix-expands Cyrillic terms and leaves Latin terms alone.

Both need a test, because both are the kind of thing that silently degrades to "works, but worse"
rather than failing.
