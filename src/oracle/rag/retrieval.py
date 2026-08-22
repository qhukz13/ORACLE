"""Hybrid retrieval: dense + BM25 + RRF, then boosts, then diversity (RAG.md §5).

Dense-only fails on exactly the queries a developer asks most — exact identifiers, error
strings, file names. Lexical-only fails on conceptual questions, and fails *completely*
on a Russian question against an English codebase, where it shares no term with any
document. So: both, always — with one qualification that the OQ-02 benchmark forced.

**Fusion is not unconditional.** Measured on the fixture set, unweighted RRF is worth
+9 points of recall@5 on top of `e5-base` (81% → 90%) and **minus 5 points** on top of
`e5-small` (76% → 71%). The difference is what BM25 contributes on a query it cannot
answer: it still returns thirty ranked results, RRF treats them as a second opinion of
equal standing, and they displace correct dense hits out of the top 5. The fix is not to
tune weights — RAG.md §5 chose RRF precisely for having none — but to admit the lexical
list only when the query is one BM25 could plausibly answer. See `has_lexical_purchase`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from oracle.logsink import get_logger
from oracle.rag.embedding import QUERY, Embedder
from oracle.rag.store import Hit, KnowledgeStore

log = get_logger(__name__)

#: Candidates drawn from each retriever before fusion.
CANDIDATES = 30
#: Chunks handed to the Context Assembler (RAG.md §5, band 6).
TOP_K = 8
#: RRF's smoothing constant. 60 is the value from the original paper and the one RAG.md
#: specifies; it is not tuned here, and tuning it would forfeit the reason RRF was chosen.
RRF_K = 60
#: At most this many chunks from any one file, so a single large document cannot eat the
#: whole budget and starve the answer of a second source.
MAX_PER_FILE = 3

#: A term must appear in fewer chunks than this to count as discriminating. See
#: `has_lexical_purchase` — it is a floor under the percentage, not an alternative to it.
MIN_DF_CEILING = 5

BOOST_SAME_PROJECT = 1.30
BOOST_RECENT = 1.15
BOOST_ANCHOR_MATCH = 1.20

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_TERM = re.compile(r"[A-Za-z0-9_]+|[Ѐ-ӿ]+")


@dataclass(frozen=True)
class Retrieved:
    """What the Context Assembler receives: hits, and why they were chosen."""

    hits: tuple[Hit, ...]
    #: True when any hit is `local_foreign` — the turn is tainted and the gate escalates
    #: (SECURITY.md §6). Computed here rather than by the caller so it cannot be forgotten.
    tainted: bool
    strategy: str
    dense_count: int
    lexical_count: int


def _fts_term(term: str) -> str:
    """One term as FTS5 syntax.

    Quoted, always: a question is not FTS5 syntax, and a stray `AND`, quote or
    parenthesis raises `OperationalError` — a lexical failure must never fail a turn.
    Cyrillic terms also get a `*`, because `unicode61` does not stem and Russian is
    inflected: `токен` does not reach `токена` without it (OQ-08). Latin terms are left
    alone — prefix-expanding `get` would match half the corpus.
    """
    return f'"{term}"*' if _CYRILLIC.search(term) else f'"{term}"'


def fts_query(question: str, terms: list[str] | None = None) -> str:
    """Turn a human question into an FTS5 `OR` query over `terms` (default: all of them)."""
    picked = terms if terms is not None else [t for t in _TERM.findall(question) if len(t) >= 2]
    return " OR ".join(_fts_term(t) for t in picked)


def discriminating_terms(
    question: str, store: KnowledgeStore, max_df_ratio: float = 0.10
) -> list[str]:
    """The query terms that actually narrow the corpus. Empty means BM25 cannot help.

    Two jobs in one pass over the terms, because both need the same document frequencies.

    **It decides whether to fuse at all.** A Russian question against an English corpus
    has no term BM25 can answer with, and fusing thirty noise results displaces correct
    dense hits out of the top 5 — measured at minus 5 points of recall@5 on a weaker dense
    model.

    **And it decides what to send.** OR-ing every word in a question means OR-ing `the`,
    `we` and `is`, each of which matches nearly every chunk. That is what made the lexical
    half **150 ms p50 — twice the cost of the brute-force vector scan** — on a corpus of
    10k chunks. Dropping non-discriminating terms costs nothing and removes most of it.

    A term in *every* document discriminates nothing; a term in *no* document is not
    evidence either. `max_df_ratio` has a floor (`MIN_DF_CEILING`) so a small index does
    not classify every term as ubiquitous and gate itself out entirely.
    """
    total = store.db.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    if not total:
        return []
    ceiling = max(MIN_DF_CEILING, total * max_df_ratio)
    kept: list[str] = []
    for term in dict.fromkeys(t for t in _TERM.findall(question) if len(t) > 2):
        row = store.db.execute(
            "SELECT COUNT(*) AS n FROM chunks_fts WHERE chunks_fts MATCH ?",
            (_fts_term(term),),
        ).fetchone()
        if 0 < row["n"] < ceiling:
            kept.append(term)
    return kept


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion over lists of chunk ids.

    RRF needs no score normalisation between two incomparable scoring systems and no
    tuned weights — it is robust by construction, which is the entire reason it is here
    rather than a weighted blend of a cosine and a BM25 score.
    """
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] += 1.0 / (k + rank + 1)
    return scores


def has_lexical_purchase(question: str, store: KnowledgeStore, max_df_ratio: float = 0.10) -> bool:
    """Whether BM25 could plausibly answer this, or would only contribute noise."""
    return bool(discriminating_terms(question, store, max_df_ratio))


def _boosted(hit: Hit, base: float, *, project: str | None, question: str, now: datetime) -> float:
    score = base
    if project and hit.project == project:
        score *= BOOST_SAME_PROJECT
    if hit.anchor and hit.anchor.lower() in question.lower():
        score *= BOOST_ANCHOR_MATCH
    try:
        indexed = datetime.fromisoformat(hit.indexed_at.replace("Z", "+00:00"))
    except ValueError:
        return score
    if (now - indexed).days <= 7:
        score *= BOOST_RECENT
    return score


def _diversify(hits: list[Hit], limit: int) -> list[Hit]:
    """At most `MAX_PER_FILE` chunks per file, order otherwise preserved."""
    per_file: dict[str, int] = defaultdict(int)
    out: list[Hit] = []
    for hit in hits:
        if per_file[hit.rel_path] >= MAX_PER_FILE:
            continue
        per_file[hit.rel_path] += 1
        out.append(hit)
        if len(out) >= limit:
            break
    return out


def retrieve(
    question: str,
    store: KnowledgeStore,
    embedder: Embedder,
    *,
    collection: str | None = None,
    project: str | None = None,
    limit: int = TOP_K,
    now: datetime | None = None,
) -> Retrieved:
    """The full pipeline: two retrievers, fusion, boosts, diversity, top-k."""
    now = now or datetime.now(UTC)

    vector = embedder.encode([question], QUERY)[0]
    dense = store.search_dense(vector, CANDIDATES, collection=collection, project=project)

    lexical: list[Hit] = []
    strategy = "dense"
    terms = discriminating_terms(question, store)
    if terms:
        lexical = store.search_lexical(
            fts_query(question, terms), CANDIDATES, collection=collection, project=project
        )
        strategy = "hybrid" if lexical else "dense"

    by_id = {h.chunk_id: h for h in (*dense, *lexical)}
    rankings = [[h.chunk_id for h in dense]]
    if lexical:
        rankings.append([h.chunk_id for h in lexical])
    fused = rrf(rankings)

    scored = [
        replace(hit, score=_boosted(hit, fused[cid], project=project, question=question, now=now))
        for cid, hit in by_id.items()
    ]
    scored.sort(key=lambda h: -h.score)
    hits = _diversify(scored, limit)

    log.info(
        "rag.retrieved",
        strategy=strategy,
        dense=len(dense),
        lexical=len(lexical),
        returned=len(hits),
    )
    return Retrieved(
        hits=tuple(hits),
        # Retrieved text is untrusted input. A chunk from a dependency or from someone
        # else's repository is `local_foreign`, taints the turn, and escalates the tier
        # of any plan built from it (SECURITY.md §6). Derived here so no caller can
        # retrieve without also learning that.
        tainted=any(h.provenance != "local_owned" for h in hits),
        strategy=strategy,
        dense_count=len(dense),
        lexical_count=len(lexical),
    )


def to_citation(hit: Hit) -> dict[str, object]:
    """The attribution payload from RAG.md §7, exactly as the UI renders it."""
    return {
        "chunk_id": hit.chunk_id,
        "collection": hit.collection,
        "project": hit.project,
        "path": hit.rel_path,
        "abs_path": hit.abs_path,
        "anchor": hit.anchor,
        "score": round(float(hit.score), 4),
        "provenance": hit.provenance,
        "indexed_at": hit.indexed_at,
    }
